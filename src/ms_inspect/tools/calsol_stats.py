"""
calsol_stats.py — ms_calsol_stats

Inspects a CASA calibration table and returns structured numerical diagnostics
sufficient for the skill to make go/no-go decisions after a calibration solve.

Supports G Jones (complex gain), B Jones (bandpass), and K Jones (delay) tables.
Returns per-(antenna, SPW, field) arrays. Reads one (SPW, field) slice at a time
to bound memory use on large tables.

No interpretation — numbers and flags only.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from ms_inspect.util.casa_context import open_table
from ms_inspect.util.formatting import field as fmt_field
from ms_inspect.util.formatting import response_envelope

TOOL_NAME = "ms_calsol_stats"

# Jones types supported in this tool
_SUPPORTED_TYPES = {"G", "B", "K"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _nan_list(shape: tuple[int, ...]) -> list:
    """Return a nested list of NaN with the given shape."""
    arr = np.full(shape, np.nan)
    return arr.tolist()


def _phase_rms_deg(phase_rad: np.ndarray) -> float:
    """RMS of phase values in degrees, ignoring NaN."""
    valid = phase_rad[~np.isnan(phase_rad)]
    if valid.size == 0:
        return math.nan
    return float(np.sqrt(np.mean(valid**2))) * (180.0 / math.pi)


def _phase_mean_deg(phase_rad: np.ndarray) -> float:
    valid = phase_rad[~np.isnan(phase_rad)]
    if valid.size == 0:
        return math.nan
    return float(np.mean(valid)) * (180.0 / math.pi)


def _safe_mean(arr: np.ndarray) -> float:
    valid = arr[~np.isnan(arr)]
    return float(np.mean(valid)) if valid.size > 0 else math.nan


def _safe_std(arr: np.ndarray) -> float:
    valid = arr[~np.isnan(arr)]
    return float(np.std(valid)) if valid.size > 1 else math.nan


# ---------------------------------------------------------------------------
# Metadata readers
# ---------------------------------------------------------------------------


def _read_ant_names(caltable_path: str) -> list[str]:
    ant_sub = str(Path(caltable_path) / "ANTENNA")
    with open_table(ant_sub) as tb:
        return list(tb.getcol("NAME"))


def _read_field_names(caltable_path: str) -> dict[int, str]:
    """Return {field_id: field_name} from the FIELD subtable."""
    field_sub = str(Path(caltable_path) / "FIELD")
    with open_table(field_sub) as tb:
        names = list(tb.getcol("NAME"))
    return {i: n for i, n in enumerate(names)}


def _read_table_type(caltable_path: str) -> str:
    """Read VisCal keyword and strip ' Jones' suffix."""
    with open_table(caltable_path) as tb:
        keywords = tb.getkeywords()
    viscal = keywords.get("VisCal", "")
    return viscal.replace(" Jones", "").strip()


def _read_axis_ids(caltable_path: str) -> tuple[list[int], list[int]]:
    """Return (sorted spw_ids, sorted field_ids) present in the main table."""
    with open_table(caltable_path) as tb:
        spw_ids = sorted(set(int(x) for x in tb.getcol("SPECTRAL_WINDOW_ID")))
        field_ids = sorted(set(int(x) for x in tb.getcol("FIELD_ID")))
    return spw_ids, field_ids


# ---------------------------------------------------------------------------
# Per-(spw, field) slice processor
# ---------------------------------------------------------------------------


def _process_slice(
    caltable_path: str,
    spw: int,
    field: int,
    ant_names: list[str],
    table_type: str,
    n_chan_max: int,
) -> dict:
    """
    Read one (SPW, field) slice and compute per-antenna stats.

    Returns a dict keyed by antenna index with sub-dicts containing:
        flagged_frac, snr_mean, amp_mean, amp_std, phase_mean_deg,
        phase_rms_deg, amp_array (shape [n_chan_max]), delay_ns (K only),
        n_rows.
    """
    n_ant = len(ant_names)
    result: dict[int, dict] = {}

    with open_table(caltable_path) as tb:
        sub = tb.query(f"SPECTRAL_WINDOW_ID == {spw} AND FIELD_ID == {field}")
        try:
            if sub.nrows() == 0:
                return {}

            ant1 = sub.getcol("ANTENNA1").astype(int)
            flag = sub.getcol("FLAG")  # [n_corr, n_chan, n_rows]
            snr = sub.getcol("SNR")  # [n_corr, n_chan, n_rows]

            param_col = "FPARAM" if table_type == "K" else "CPARAM"
            param = sub.getcol(param_col)  # [n_corr, n_chan, n_rows]
        finally:
            sub.close()

    # pivot: per antenna
    for a_idx in range(n_ant):
        mask = ant1 == a_idx
        if not np.any(mask):
            continue

        p_ant = param[:, :, mask]  # [n_corr, n_chan, n_ant_rows]
        f_ant = flag[:, :, mask]  # bool
        s_ant = snr[:, :, mask]

        n_total = f_ant.size
        n_flagged = int(np.sum(f_ant))
        flagged_frac = n_flagged / n_total if n_total > 0 else math.nan

        snr_all = s_ant.ravel().astype(float)
        snr_all[np.isnan(snr_all)] = math.nan

        entry: dict = {
            "n_rows": int(np.sum(mask)),
            "flagged_frac": flagged_frac,
            "snr_mean": _safe_mean(snr_all),
        }

        if table_type == "K":
            # FPARAM shape [n_corr, 1, n_rows] — delay in nanoseconds
            delay = p_ant[:, 0, :].astype(float)  # [n_corr, n_rows]
            delay[f_ant[:, 0, :]] = math.nan
            entry["delay_ns"] = delay.tolist()  # [n_corr, n_rows] → averaged later
        else:
            # CPARAM — complex
            p_f = p_ant.astype(complex)
            p_f[f_ant] = complex(math.nan, math.nan)

            amp = np.abs(p_f)  # [n_corr, n_chan, n_rows]
            phase = np.angle(p_f)  # radians

            amp_flat = amp.ravel()
            phase_flat = phase.ravel()

            entry["amp_mean"] = _safe_mean(amp_flat)
            entry["amp_std"] = _safe_std(amp_flat)
            entry["phase_mean_deg"] = _phase_mean_deg(phase_flat)
            entry["phase_rms_deg"] = _phase_rms_deg(phase_flat)

            # full amplitude array averaged over corr axis → [n_chan]
            if np.all(np.isnan(amp)):
                amp_chan = np.full(amp.shape[1], math.nan)
            else:
                amp_chan = np.nanmean(amp, axis=(0, 2))  # [n_chan]
            # pad to n_chan_max
            padded = np.full(n_chan_max, math.nan)
            padded[: len(amp_chan)] = amp_chan
            entry["amp_array"] = padded.tolist()

        result[a_idx] = entry

    return result


# ---------------------------------------------------------------------------
# Main run()
# ---------------------------------------------------------------------------


def _compute_outliers(
    snr_mean_arr: np.ndarray | None,
    amp_mean_arr: np.ndarray | None,
    ant_names: list[str],
    spw_ids: list[int],
    field_names: list[str],
    snr_min: float,
    amp_sigma_thresh: float,
) -> dict:
    """Compute low_snr and amp_outliers lists from solution arrays."""
    low_snr: list[dict] = []
    if snr_mean_arr is not None:
        flat = snr_mean_arr.reshape(-1)
        shape = snr_mean_arr.shape
        for flat_idx, val in enumerate(flat):
            if np.isfinite(val) and val < snr_min:
                idx = np.unravel_index(flat_idx, shape)
                low_snr.append(
                    {
                        "antenna": ant_names[idx[0]],
                        "spw": spw_ids[idx[1]] if len(shape) > 1 else 0,
                        "field": field_names[idx[2]] if len(shape) > 2 else "",
                        "snr": round(float(val), 3),
                    }
                )

    amp_outliers: list[dict] = []
    if amp_mean_arr is not None:
        median = float(np.nanmedian(amp_mean_arr))
        mad = float(np.nanmedian(np.abs(amp_mean_arr - median)))
        sigma = 1.4826 * mad if mad > 0 else 0.0
        if sigma > 0:
            flat = amp_mean_arr.reshape(-1)
            shape = amp_mean_arr.shape
            for flat_idx, val in enumerate(flat):
                if np.isfinite(val):
                    n_sigma = abs(val - median) / sigma
                    if n_sigma > amp_sigma_thresh:
                        idx = np.unravel_index(flat_idx, shape)
                        amp_outliers.append(
                            {
                                "antenna": ant_names[idx[0]],
                                "spw": spw_ids[idx[1]] if len(shape) > 1 else 0,
                                "field": field_names[idx[2]] if len(shape) > 2 else "",
                                "amp": round(float(val), 4),
                                "n_sigma": round(float(n_sigma), 2),
                            }
                        )
    return {
        "low_snr": low_snr,
        "amp_outliers": amp_outliers,
        "thresholds": {
            "snr_min": snr_min,
            "amp_sigma": amp_sigma_thresh,
        },
    }


def run(
    caltable_path: str, snr_min: float = 3.0, amp_sigma: float = 5.0, verbosity: str = "full"
) -> dict:
    """
    Inspect a CASA calibration table and return per-(antenna, SPW, field) stats.

    Args:
        caltable_path: Path to the caltable directory (e.g. gain.g, BP0.b, delay.k).

    Returns:
        Standard response envelope. ms_path field contains caltable_path.
    """
    p = Path(caltable_path).expanduser().resolve()
    if not p.exists() or not p.is_dir():
        from ms_inspect.util.formatting import error_envelope

        return error_envelope(
            TOOL_NAME,
            caltable_path,
            "CALTABLE_NOT_FOUND",
            f"Calibration table not found: {p}",
        )

    casa_calls: list[str] = []
    warnings: list[str] = []

    # --- metadata ---
    table_type = _read_table_type(caltable_path)
    casa_calls.append(f"tb.getkeywords() → VisCal='{table_type} Jones'")

    if table_type not in _SUPPORTED_TYPES:
        warnings.append(
            f"VisCal type '{table_type}' is not fully supported; "
            "only G, B, K stats are computed. Returning structural metadata only."
        )

    ant_names = _read_ant_names(caltable_path)
    casa_calls.append("tb.open(ANTENNA) → NAME")

    field_name_map = _read_field_names(caltable_path)
    casa_calls.append("tb.open(FIELD) → NAME")

    spw_ids, field_ids = _read_axis_ids(caltable_path)
    casa_calls.append("tb.getcol(SPECTRAL_WINDOW_ID, FIELD_ID) → axis ids")

    n_ant = len(ant_names)
    n_spw = len(spw_ids)
    n_field = len(field_ids)
    field_names = [field_name_map.get(f, f"FIELD_{f}") for f in field_ids]

    # spw_id → index and field_id → index lookups
    spw_idx = {s: i for i, s in enumerate(spw_ids)}
    field_idx = {f: i for i, f in enumerate(field_ids)}

    # determine n_chan_max for B tables (needed for amp_array padding)
    n_chan_max = 1
    if table_type == "B":
        with open_table(caltable_path) as tb:
            sub0 = tb.query(f"SPECTRAL_WINDOW_ID == {spw_ids[0]}")
            try:
                if sub0.nrows() > 0:
                    n_chan_max = sub0.getcol("CPARAM").shape[1]
            finally:
                sub0.close()
        # refine: find global max channels across all SPWs
        n_chan_max_global = n_chan_max
        for spw in spw_ids[1:]:
            with open_table(caltable_path) as tb:
                sub = tb.query(f"SPECTRAL_WINDOW_ID == {spw}")
                try:
                    if sub.nrows() > 0:
                        nc = sub.getcol("CPARAM").shape[1]
                        n_chan_max_global = max(n_chan_max_global, nc)
                finally:
                    sub.close()
        n_chan_max = n_chan_max_global
        casa_calls.append(f"tb.query per SPW → n_chan_max={n_chan_max}")

    # --- allocate output arrays ---
    shape = (n_ant, n_spw, n_field)
    flagged_frac_arr = np.full(shape, math.nan)
    snr_mean_arr = np.full(shape, math.nan)

    amp_mean_arr = np.full(shape, math.nan) if table_type in ("G", "B") else None
    amp_std_arr = np.full(shape, math.nan) if table_type in ("G", "B") else None
    phase_mean_arr = np.full(shape, math.nan) if table_type in ("G", "B") else None
    phase_rms_arr = np.full(shape, math.nan) if table_type in ("G", "B") else None
    amp_array_4d = (
        np.full((n_ant, n_spw, n_field, n_chan_max), math.nan) if table_type == "B" else None
    )

    # delay: store mean delay per (ant, spw, field, n_corr) — inferred from first slice
    delay_arr: np.ndarray | None = None

    # --- iterate (spw, field) slices ---
    for spw in spw_ids:
        si = spw_idx[spw]
        for fid in field_ids:
            fi = field_idx[fid]

            slice_data = _process_slice(caltable_path, spw, fid, ant_names, table_type, n_chan_max)
            casa_calls.append(
                f"tb.query(SPECTRAL_WINDOW_ID=={spw} AND FIELD_ID=={fid}) → {len(slice_data)} antennas"
            )

            for a_idx, entry in slice_data.items():
                flagged_frac_arr[a_idx, si, fi] = entry["flagged_frac"]
                snr_mean_arr[a_idx, si, fi] = entry["snr_mean"]

                if table_type in ("G", "B"):
                    amp_mean_arr[a_idx, si, fi] = entry["amp_mean"]
                    amp_std_arr[a_idx, si, fi] = entry["amp_std"]
                    phase_mean_arr[a_idx, si, fi] = entry["phase_mean_deg"]
                    phase_rms_arr[a_idx, si, fi] = entry["phase_rms_deg"]
                    if table_type == "B" and amp_array_4d is not None:
                        amp_array_4d[a_idx, si, fi, :] = entry["amp_array"]

                if table_type == "K":
                    delay_data = np.array(entry["delay_ns"])  # [n_corr, n_rows]
                    n_corr = delay_data.shape[0]
                    if delay_arr is None:
                        delay_arr = np.full((n_ant, n_spw, n_field, n_corr), math.nan)
                    delay_arr[a_idx, si, fi, :] = np.nanmean(delay_data, axis=1)

    # --- scalar summaries ---
    overall_flagged_frac = float(np.nanmean(flagged_frac_arr))
    lost_mask = np.all(flagged_frac_arr == 1.0, axis=(1, 2))  # [n_ant]
    antennas_lost = [ant_names[i] for i in range(n_ant) if lost_mask[i]]
    n_antennas_lost = len(antennas_lost)

    delay_rms_ns = None
    if table_type == "K" and delay_arr is not None:
        # RMS across antennas per (spw, field) → [n_spw, n_field]
        delay_rms_ns = np.sqrt(np.nanmean(delay_arr**2, axis=(0, 3))).tolist()  # [n_spw, n_field]

    # --- build response data ---
    def _flag(arr: np.ndarray | None) -> str:
        if arr is None:
            return "UNAVAILABLE"
        return "PARTIAL" if np.any(np.isnan(arr)) else "COMPLETE"

    data: dict = {
        "table_type": fmt_field(
            table_type, flag="COMPLETE" if table_type in _SUPPORTED_TYPES else "UNAVAILABLE"
        ),
        "n_antennas": fmt_field(n_ant),
        "n_spw": fmt_field(n_spw),
        "n_field": fmt_field(n_field),
        "ant_names": fmt_field(ant_names),
        "spw_ids": fmt_field(spw_ids),
        "field_ids": fmt_field(field_ids),
        "field_names": fmt_field(field_names),
        "flagged_frac": fmt_field(flagged_frac_arr.tolist(), flag=_flag(flagged_frac_arr)),
        "snr_mean": fmt_field(snr_mean_arr.tolist(), flag=_flag(snr_mean_arr)),
        "overall_flagged_frac": fmt_field(overall_flagged_frac),
        "n_antennas_lost": fmt_field(n_antennas_lost),
        "antennas_lost": fmt_field(antennas_lost),
    }

    if table_type in ("G", "B"):
        data["amp_mean"] = fmt_field(amp_mean_arr.tolist(), flag=_flag(amp_mean_arr))
        data["amp_std"] = fmt_field(amp_std_arr.tolist(), flag=_flag(amp_std_arr))
        data["phase_mean_deg"] = fmt_field(phase_mean_arr.tolist(), flag=_flag(phase_mean_arr))
        data["phase_rms_deg"] = fmt_field(phase_rms_arr.tolist(), flag=_flag(phase_rms_arr))

    if table_type == "B" and amp_array_4d is not None:
        data["amp_array"] = fmt_field(
            amp_array_4d.tolist(),
            flag=_flag(amp_array_4d),
            note=f"Shape [n_ant={n_ant}, n_spw={n_spw}, n_field={n_field}, n_chan_max={n_chan_max}]. NaN where channel count < n_chan_max or solution absent.",
        )

    if table_type == "K":
        if delay_arr is not None:
            data["delay_ns"] = fmt_field(
                delay_arr.tolist(),
                flag=_flag(delay_arr),
                note=f"Shape [n_ant={n_ant}, n_spw={n_spw}, n_field={n_field}, n_corr]. Mean delay per antenna/SPW/field/corr.",
            )
            data["delay_rms_ns"] = fmt_field(
                delay_rms_ns,
                flag="COMPLETE",
                note=f"Shape [n_spw={n_spw}, n_field={n_field}]. RMS across antennas per SPW/field.",
            )
        else:
            data["delay_ns"] = fmt_field(None, flag="UNAVAILABLE", note="No K solutions found.")
            data["delay_rms_ns"] = fmt_field(None, flag="UNAVAILABLE")

    # --- outliers block (always present) ---
    data["outliers"] = _compute_outliers(
        snr_mean_arr,
        amp_mean_arr if table_type in ("G", "B") else None,
        ant_names,
        spw_ids,
        field_names,
        snr_min,
        amp_sigma,
    )

    # --- compact verbosity: strip field() wrappers, roll up incomplete ---
    if verbosity == "compact":
        incomplete_fields: list[dict] = []
        compact_data: dict = {}
        for k, v in data.items():
            if k == "outliers":
                compact_data[k] = v
                continue
            if isinstance(v, dict) and "value" in v and "flag" in v:
                if v["flag"] != "COMPLETE":
                    incomplete_fields.append({"path": k, "flag": v["flag"], "note": v.get("note")})
                compact_data[k] = v["value"]
            else:
                compact_data[k] = v
        compact_data["incomplete_fields"] = incomplete_fields
        data = compact_data

    return response_envelope(
        tool_name=TOOL_NAME,
        ms_path=caltable_path,
        data=data,
        warnings=warnings,
        casa_calls=casa_calls,
    )
