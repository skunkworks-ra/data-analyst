"""
tools/corrected_stats.py — ms_corrected_stats

Per-field amplitude and phase statistics of a visibility data column
(CORRECTED_DATA by default) on the parallel-hand correlations over a chosen
channel range. The standard "is the calibration clean?" sanity check: after
applycal, a well-calibrated point-source calibrator should sit at its flux
density with low amplitude scatter and near-zero phase.

Measures only — returns numbers and flags, no verdicts. The skill compares the
returned amplitude against the expected flux density and the phase RMS against
its thresholds.

Reads in row chunks and samples uniformly above max_rows to bound memory.
Band-edge channels are excluded via chan_start/chan_end (the bandpass divides
out the edge roll-off and inflates noise there), so the caller passes the same
in-band range used for the gain/delay solves.
"""

from __future__ import annotations

import math

import numpy as np

from ms_inspect.util.casa_context import open_table, validate_ms_path
from ms_inspect.util.formatting import field as fmt_field
from ms_inspect.util.formatting import normalize_field_sel, response_envelope

TOOL_NAME = "ms_corrected_stats"

_DEFAULT_MAX_ROWS = 500_000
# CASA Stokes codes for parallel-hand correlations: RR, LL, XX, YY.
_PARALLEL_CODES = {5, 8, 9, 12}


def _parallel_indices(corr_codes: list[int]) -> list[int]:
    """Return the correlation-axis indices that are parallel-hand (RR/LL/XX/YY)."""
    idx = [i for i, c in enumerate(corr_codes) if int(c) in _PARALLEL_CODES]
    # Fall back to all correlations if none matched (unknown basis).
    return idx if idx else list(range(len(corr_codes)))


def _field_stats(
    data: np.ndarray,
    flag: np.ndarray,
    par_idx: list[int],
    chan_start: int | None,
    chan_end: int | None,
) -> dict:
    """
    Compute amplitude/phase stats for one field's visibility block.

    Args:
        data: complex array [n_corr, n_chan, n_rows].
        flag: bool array, same shape (True = flagged).
        par_idx: parallel-hand correlation indices.
        chan_start, chan_end: inclusive/exclusive channel slice bounds
            (Python slice semantics); None means open-ended.

    The visibilities are vector-averaged over the channel range per
    (correlation, row) BEFORE computing statistics. This is essential: a single
    narrow channel on a faint calibrator has SNR ~ 1, so raw per-visibility
    amplitude is Rician-biased high and phase is near-random. Averaging the
    in-band channels boosts SNR by ~sqrt(n_chan) so the amplitude approaches the
    true flux density and the phase scatter reflects calibration, not noise.

    Returns a dict of plain Python floats/ints (no field() wrappers).
    """
    cs = 0 if chan_start is None else chan_start
    ce = data.shape[1] if chan_end is None else chan_end
    d = data[par_idx, cs:ce, :]
    good = ~flag[par_idx, cs:ce, :]
    # Vector-average over the channel axis, ignoring flagged channels.
    cnt = good.sum(axis=1)  # [n_par, n_rows]
    dsum = np.where(good, d, 0).sum(axis=1)  # complex sum over channels
    vavg = np.divide(dsum, cnt, out=np.full(dsum.shape, np.nan, dtype=complex), where=cnt > 0)
    vis = vavg.ravel()
    valid = np.isfinite(vis.real) & np.isfinite(vis.imag) & (np.abs(vis) > 0)
    vis = vis[valid]
    n_samples = int(valid.sum())
    if vis.size == 0:
        return {
            "n_samples": n_samples,
            "amp_median": None,
            "amp_robust_std": None,
            "amp_p95": None,
            "phase_rms_deg": None,
        }
    amp = np.abs(vis)
    phase = np.angle(vis) * (180.0 / math.pi)
    median = float(np.median(amp))
    mad = float(np.median(np.abs(amp - median)))
    return {
        "n_samples": n_samples,
        "amp_median": round(median, 6),
        "amp_robust_std": round(1.4826 * mad, 6),
        "amp_p95": round(float(np.percentile(amp, 95)), 6),
        "phase_rms_deg": round(float(np.sqrt(np.mean(phase**2))), 3),
    }


def _corr_codes(ms_str: str) -> list[int]:
    """Read CORR_TYPE for the first polarization setup."""
    from pathlib import Path

    with open_table(str(Path(ms_str) / "POLARIZATION")) as tb:
        return [int(x) for x in tb.getcell("CORR_TYPE", 0)]


def _field_id_map(ms_str: str) -> dict[int, str]:
    from pathlib import Path

    with open_table(str(Path(ms_str) / "FIELD")) as tb:
        names = list(tb.getcol("NAME"))
    return {i: n for i, n in enumerate(names)}


def run(
    ms_path: str,
    field: str = "",
    chan_start: int | None = None,
    chan_end: int | None = None,
    datacolumn: str = "CORRECTED_DATA",
    max_rows: int = _DEFAULT_MAX_ROWS,
) -> dict:
    """
    Per-field parallel-hand amplitude/phase stats of a data column.

    Args:
        ms_path:    Path to the MS (with the requested data column populated).
        field:      CASA field-name selection (comma-separated) or '' for all.
        chan_start: First channel to include (default 0). Use to drop band edges.
        chan_end:   One past the last channel (default n_chan).
        datacolumn: 'CORRECTED_DATA' (default), 'DATA', or 'MODEL_DATA'.
        max_rows:   Per-field row cap; rows are uniformly sampled above this.

    Returns:
        Standard envelope with per_field amplitude median / robust std / p95 and
        phase RMS over the parallel-hand correlations and chosen channel range.
    """
    field = normalize_field_sel(field)
    p = validate_ms_path(ms_path)
    ms_str = str(p)
    casa_calls: list[str] = []
    warnings: list[str] = []

    name_map = _field_id_map(ms_str)
    casa_calls.append("tb.open(FIELD) → NAME")
    wanted_names = {n.strip() for n in field.split(",") if n.strip()}
    target_ids = [fid for fid, nm in name_map.items() if (not wanted_names or nm in wanted_names)]
    if not target_ids:
        warnings.append(f"No fields matched selection '{field}'.")
        return response_envelope(
            tool_name=TOOL_NAME,
            ms_path=ms_path,
            data={"per_field": [], "datacolumn": datacolumn},
            warnings=warnings,
            casa_calls=casa_calls,
        )

    corr_codes = _corr_codes(ms_str)
    par_idx = _parallel_indices(corr_codes)
    casa_calls.append(f"tb.open(POLARIZATION) → CORR_TYPE, parallel idx={par_idx}")

    per_field: list[dict] = []
    with open_table(ms_str) as tb:
        col_names = set(tb.colnames())
        if datacolumn not in col_names:
            from ms_inspect.exceptions import ComputationError

            raise ComputationError(
                f"{datacolumn} column not present in {ms_path}.",
                ms_path=ms_path,
            )
        for fid in target_ids:
            sub = tb.query(f"FIELD_ID == {fid}")
            try:
                n_rows = int(sub.nrows())
                if n_rows == 0:
                    continue
                step = max(1, n_rows // max_rows)
                data = sub.getcol(datacolumn)
                flag = sub.getcol("FLAG")
            finally:
                sub.close()
            if step > 1:
                data = data[:, :, ::step]
                flag = flag[:, :, ::step]
                warnings.append(f"Field {name_map[fid]}: {n_rows} rows sampled every {step}th.")
            stats = _field_stats(data, flag, par_idx, chan_start, chan_end)
            casa_calls.append(f"tb.query(FIELD_ID=={fid}) → {datacolumn}, FLAG")
            flagged = stats["amp_median"] is None
            per_field.append(
                {
                    "field_id": fid,
                    "field_name": name_map[fid],
                    "n_samples": fmt_field(stats["n_samples"]),
                    "amp_median": fmt_field(
                        stats["amp_median"],
                        flag="UNAVAILABLE" if flagged else "COMPLETE",
                        note="all data flagged" if flagged else None,
                    ),
                    "amp_robust_std": fmt_field(stats["amp_robust_std"]),
                    "amp_p95": fmt_field(stats["amp_p95"]),
                    "phase_rms_deg": fmt_field(stats["phase_rms_deg"]),
                }
            )

    data_out = {
        "datacolumn": datacolumn,
        "chan_start": chan_start,
        "chan_end": chan_end,
        "parallel_corr_indices": par_idx,
        "averaging": "vector-averaged over the channel range per (corr, row) before stats",
        "per_field": per_field,
    }
    return response_envelope(
        tool_name=TOOL_NAME,
        ms_path=ms_path,
        data=data_out,
        warnings=warnings,
        casa_calls=casa_calls,
    )
