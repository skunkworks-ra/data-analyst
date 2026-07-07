"""
postcal_flag.py — ms_postcal_flag

Post-calibration RFI flagging on the phase calibrator AND science target, after
the final applycal. The pre-cal pipeline flags calibrators only; this routine
extends flagging to the fields that were never cleaned, and bakes the SpW-triage
decision (from ms_spw_amp_severity, reasoned in skill 13) into the FLAG column.

One atomic flagdata(mode='list') pass over the CORRECTED column:
  1. clip      — optional ceiling on |CORRECTED| to kill egregious outliers
                 before the autoflaggers compute their statistics
  2. tfcrop    — on the SpWs being KEPT (salvage localized RFI, preserve bandwidth)
  3. rflag     — likewise
  4. manual    — fully flag the drop-tier SpWs (so all downstream steps respect it)

field is REQUIRED. Flagging CORRECTED on fields whose calibration is not valid
flags almost everything; the caller scopes this to the phase cal + target.

Script output:
  workdir/postcal_flag_cmds.txt — the flagcmd list (complete audit record)
  workdir/postcal_flag.py       — self-contained driver script
"""

from __future__ import annotations

from pathlib import Path

from ms_inspect.util.casa_context import validate_ms_path
from ms_inspect.util.formatting import field as fmt_field
from ms_inspect.util.formatting import normalize_field_sel, normalize_spw_sel, response_envelope

TOOL_NAME = "ms_postcal_flag"

_DATACOL_MAP = {
    "corrected": "CORRECTED_DATA",
    "data": "DATA",
    "model": "MODEL_DATA",
}


def _parse_spw_ids(spw_sel: str) -> list[int]:
    """Parse a comma-separated SpW selection into whole-SpW ints.

    Supports plain ids ('0,3,5') and inclusive ranges ('0~7', '20~28'); the two
    may be mixed ('16,20~28'). Channel syntax ('0:5~10') is NOT supported — the
    robust clip is computed per whole SpW, so any token carrying a ':channel'
    selection is skipped entirely rather than silently widened to the whole SpW.
    Returns sorted, de-duplicated ids.
    """
    ids: set[int] = set()
    for raw in spw_sel.split(","):
        tok = raw.strip()
        if not tok or ":" in tok:
            continue
        if "~" in tok:
            lo_s, _, hi_s = tok.partition("~")
            lo_s, hi_s = lo_s.strip(), hi_s.strip()
            if lo_s.isdigit() and hi_s.isdigit():
                lo, hi = int(lo_s), int(hi_s)
                if lo <= hi:
                    ids.update(range(lo, hi + 1))
        elif tok.isdigit():
            ids.add(int(tok))
    return sorted(ids)


def _robust_clip_thresholds(
    ms_str: str,
    field_sel: str,
    keep_spw_ids: list[int],
    datacolumn: str,
    clip_sigma: float,
    max_samples: int = 5000,
    row_chunk: int = 20_000,
) -> tuple[dict[int, float], list[str]]:
    """Per-SpW robust clip ceiling = median + clip_sigma * 1.4826 * MAD.

    Memory-bounded: one reservoir sample of |datacolumn| per SpW (pooled over
    channels/correlations), scoped to the selected fields and kept SpWs.
    Returns (thresholds_by_spw, warnings).
    """
    import numpy as np

    from ms_inspect.tools.spw_amp_severity import _ChanReservoir
    from ms_inspect.util.casa_context import open_table

    warnings: list[str] = []
    col = _DATACOL_MAP.get(datacolumn.lower(), datacolumn)
    rng = np.random.default_rng(1234)

    # field selection → ids
    with open_table(ms_str + "/FIELD") as tb:
        names = list(tb.getcol("NAME"))
    wanted = {n.strip() for n in field_sel.split(",") if n.strip()}
    field_ids: list[int] = []
    for sel in wanted:
        if sel.isdigit():
            field_ids.append(int(sel))
        else:
            field_ids.extend(i for i, nm in enumerate(names) if nm == sel)

    with open_table(ms_str + "/DATA_DESCRIPTION") as tb:
        dd_to_spw = [int(x) for x in tb.getcol("SPECTRAL_WINDOW_ID")]

    keep = set(keep_spw_ids)
    reservoirs: dict[int, _ChanReservoir] = {s: _ChanReservoir(max_samples) for s in keep}
    fid_clause = ""
    if field_ids:
        fid_clause = " && FIELD_ID IN [" + ",".join(str(i) for i in sorted(set(field_ids))) + "]"

    with open_table(ms_str) as tb:
        if col not in set(tb.colnames()):
            warnings.append(f"{col} not present; robust clip skipped.")
            return {}, warnings
        for ddid, spw in enumerate(dd_to_spw):
            if spw not in keep:
                continue
            sub = tb.query(f"DATA_DESC_ID == {ddid}{fid_clause}")
            try:
                n = int(sub.nrows())
                if n == 0:
                    continue
                for start in range(0, n, row_chunk):
                    nr = min(row_chunk, n - start)
                    amp = np.abs(sub.getcol(col, startrow=start, nrow=nr))
                    flg = sub.getcol("FLAG", startrow=start, nrow=nr).astype(bool)
                    vals = amp[(~flg) & (amp > 0)]
                    reservoirs[spw].add(vals.ravel(), rng)
            finally:
                sub.close()

    thresholds: dict[int, float] = {}
    for spw, res in reservoirs.items():
        st = res.stats()
        if st is None:
            warnings.append(f"SpW {spw} had no unflagged {col}; robust clip skipped for it.")
            continue
        thresholds[spw] = round(st["median"] + clip_sigma * st["robust_sigma"], 6)
    return thresholds, warnings


def _build_cmds_content(
    field: str,
    keep_spw: str,
    drop_spw: str,
    datacolumn: str,
    clipmax: float | None,
    clip_thresholds: dict[int, float] | None,
    uvrange: str,
    timedevscale: float,
    freqdevscale: float,
    timecutoff: float,
    freqcutoff: float,
) -> str:
    lines: list[str] = []
    uv_clause = f" uvrange={uvrange!r}" if uvrange else ""
    if clip_thresholds:
        # Per-SpW robust clip: median + clip_sigma*robust_sigma, computed per SpW.
        # Emitted first so the autoflaggers compute their statistics on clipped data.
        for spw_id in sorted(clip_thresholds):
            thr = clip_thresholds[spw_id]
            lines.append(
                f"mode='clip' field={field!r} spw={str(spw_id)!r}{uv_clause} "
                f"datacolumn={datacolumn!r} clipminmax=[0.0,{thr}] clipoutside=True"
            )
    elif clipmax is not None:
        lines.append(
            f"mode='clip' field={field!r}{uv_clause} datacolumn={datacolumn!r} "
            f"clipminmax=[0.0,{clipmax}] clipoutside=True"
        )
    spw_clause = f" spw={keep_spw!r}" if keep_spw else ""
    lines.append(
        f"mode='tfcrop' field={field!r}{spw_clause} datacolumn={datacolumn!r} "
        f"timecutoff={timecutoff} freqcutoff={freqcutoff}"
    )
    lines.append(
        f"mode='rflag' field={field!r}{spw_clause} datacolumn={datacolumn!r} "
        f"timedevscale={timedevscale} freqdevscale={freqdevscale}"
    )
    if drop_spw:
        lines.append(f"mode='manual' field={field!r} spw={drop_spw!r}")
    return "\n".join(lines) + "\n"


def _build_script(ms_str: str, cmds_path: str) -> str:
    return f"""\
#!/usr/bin/env python
\"\"\"
Auto-generated by ms_postcal_flag (ms_modify).
Run with: python postcal_flag.py

Requires: CORRECTED populated on the selected fields (final applycal done).
For the SpW-drop decisions to be honoured everywhere, run applycal with
applymode='calonly' so caltable flags do not pre-empt these decisions.
flagbackup=True saves a versioned backup automatically before flagging.
\"\"\"
from casatasks import flagdata

ms_path = {ms_str!r}
cmds_file = {cmds_path!r}

flagdata(
    vis=ms_path,
    mode="list",
    inpfile=cmds_file,
    flagbackup=True,
)
print("Post-calibration flagging complete.")
print("Use ms_flag_summary for the flag delta and ms_spw_amp_severity to re-measure.")
"""


def run(
    ms_path: str,
    workdir: str,
    field: str,
    keep_spw: str = "",
    drop_spw: str = "",
    datacolumn: str = "corrected",
    clip_sigma: float | None = 5.0,
    clipmax: float | None = None,
    uvrange: str = "",
    timedevscale: float = 5.0,
    freqdevscale: float = 5.0,
    timecutoff: float = 4.0,
    freqcutoff: float = 4.0,
    execute: bool = False,
) -> dict:
    """
    Generate (and optionally execute) post-calibration RFI flagging.

    Args:
        ms_path:      Path to the MS (CORRECTED populated on the selected fields).
        workdir:      Existing directory for the generated scripts.
        field:        REQUIRED. The field(s) to flag — the phase calibrator and/or
                      science target whose CORRECTED column is valid after the final
                      applycal. An all-field pass over fields without valid CORRECTED
                      flags almost everything.
        keep_spw:     CASA SpW selection for the SpWs being KEPT — tfcrop + rflag run
                      on these to salvage localized RFI. Empty = all SpWs.
        drop_spw:     CASA SpW selection for the drop-tier SpWs — fully flagged via a
                      manual command so downstream imaging/calibration respects it.
                      Empty = drop nothing.
        datacolumn:   Column to flag on (default 'corrected').
        clip_sigma:   Per-SpW robust clip: ceiling = median + clip_sigma*1.4826*MAD,
                      computed per kept SpW from the current data (default 5.0). This
                      is the principled, dataset-adaptive replacement for a flat clip.
                      Set None to disable (then clipmax is used if given).
        clipmax:      Flat |data| ceiling fallback, used only when clip_sigma is None.
        uvrange:      Optional CASA uvrange applied to the clip only (e.g. '>2klambda').
                      The robust clip is uv-blind; on an extended source scope it to
                      longer baselines so real short-spacing flux is not clipped.
        timedevscale: rflag time deviation threshold (default 5.0).
        freqdevscale: rflag frequency deviation threshold (default 5.0).
        timecutoff:   tfcrop time deviation threshold (default 4.0).
        freqcutoff:   tfcrop frequency deviation threshold (default 4.0).
        execute:      If False (default), write scripts and return.
                      If True, run flagdata(mode='list') in-process.

    Returns:
        Standard envelope. Always includes cmds_path and script_path.
    """
    field = normalize_field_sel(field)
    keep_spw = normalize_spw_sel(keep_spw)
    drop_spw = normalize_spw_sel(drop_spw)
    p = validate_ms_path(ms_path)
    ms_str = str(p)
    casa_calls: list[str] = []
    warnings: list[str] = []

    if not field or not str(field).strip():
        from ms_inspect.exceptions import ComputationError

        raise ComputationError(
            "field is required. Scope post-cal flagging to the phase calibrator and/or "
            "science target whose CORRECTED column is valid after the final applycal. "
            "An all-field pass over fields without valid CORRECTED flags almost everything.",
            ms_path=ms_path,
        )

    workdir_path = Path(workdir)
    if not workdir_path.exists():
        from ms_inspect.exceptions import ComputationError

        raise ComputationError(
            f"workdir does not exist: {workdir}. Create it before calling this tool.",
            ms_path=ms_path,
        )

    cmds_path = str(workdir_path / "postcal_flag_cmds.txt")
    script_path = str(workdir_path / "postcal_flag.py")

    # Per-SpW robust clip thresholds (median + clip_sigma*robust_sigma), computed
    # from the current data. Takes precedence over the flat clipmax fallback.
    clip_thresholds: dict[int, float] | None = None
    if clip_sigma is not None:
        keep_ids = _parse_spw_ids(keep_spw)
        if not keep_ids:
            warnings.append(
                "clip_sigma set but keep_spw is empty or not a plain SpW-id list; "
                "robust per-SpW clip skipped (use clipmax for a flat clip)."
            )
        else:
            try:
                clip_thresholds, clip_warn = _robust_clip_thresholds(
                    ms_str, field, keep_ids, datacolumn, clip_sigma
                )
                warnings.extend(clip_warn)
                casa_calls.append(
                    f"robust clip: per-SpW median + {clip_sigma}*1.4826*MAD over {field!r}"
                )
            except Exception as exc:  # noqa: BLE001 - surface, do not abort script write
                warnings.append(f"Robust clip computation failed ({exc}); no clip emitted.")

    cmds_content = _build_cmds_content(
        field,
        keep_spw,
        drop_spw,
        datacolumn,
        clipmax,
        clip_thresholds,
        uvrange,
        timedevscale,
        freqdevscale,
        timecutoff,
        freqcutoff,
    )
    Path(cmds_path).write_text(cmds_content)
    casa_calls.append(f"write_cmds → {cmds_path}")

    script_content = _build_script(ms_str, cmds_path)
    Path(script_path).write_text(script_content)
    casa_calls.append(f"write_script → {script_path}")

    base_data: dict = {
        "cmds_path": fmt_field(cmds_path),
        "script_path": fmt_field(script_path),
        "field": fmt_field(field),
        "keep_spw": keep_spw,
        "drop_spw": drop_spw,
        "datacolumn": datacolumn,
        "clip_sigma": clip_sigma,
        "clip_thresholds": clip_thresholds,
        "clipmax": clipmax,
        "uvrange": uvrange,
        "rflag_timedevscale": timedevscale,
        "rflag_freqdevscale": freqdevscale,
        "tfcrop_timecutoff": timecutoff,
        "tfcrop_freqcutoff": freqcutoff,
    }

    if not execute:
        warnings.append(
            f"Scripts written to {workdir}. Ensure the final applycal has populated "
            "CORRECTED on the selected fields (run applycal with applymode='calonly' so "
            "these SpW-drop decisions own the FLAG column). Then run postcal_flag.py "
            "externally and call ms_flag_summary to capture the delta."
        )
        return response_envelope(
            tool_name=TOOL_NAME,
            ms_path=ms_path,
            data=base_data,
            warnings=warnings,
            casa_calls=casa_calls,
        )

    try:
        from casatasks import flagdata  # type: ignore[import]
    except ImportError:
        from ms_inspect.exceptions import CASANotAvailableError

        raise CASANotAvailableError(
            "casatasks is not installed or cannot be imported.",
            ms_path=ms_path,
        ) from None

    casa_calls.append(f"casatasks.flagdata(mode='list', inpfile='{cmds_path}', flagbackup=True)")
    try:
        flagdata(
            vis=ms_str,
            mode="list",
            inpfile=cmds_path,
            flagbackup=True,
        )
    except Exception as exc:
        from ms_inspect.exceptions import ComputationError

        raise ComputationError(
            f"flagdata(mode='list') for post-cal flagging failed: {exc}",
            ms_path=ms_path,
        ) from exc

    base_data["flags_applied"] = fmt_field(True)
    return response_envelope(
        tool_name=TOOL_NAME,
        ms_path=ms_path,
        data=base_data,
        warnings=warnings,
        casa_calls=casa_calls,
    )
