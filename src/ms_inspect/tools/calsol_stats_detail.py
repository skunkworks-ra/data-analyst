"""
calsol_stats_detail.py — ms_calsol_stats_detail

Deep-dive reader over the raw NPZ sidecar written by ms_calsol_stats. The stats
tool's response is intentionally bounded (worst-N rows + per-antenna rollups);
when a gate needs the full per-(antenna, SPW, field) enumeration, this tool
returns just the requested slice from disk — no re-solve, no regenerated script.

This is the committed escape hatch behind the summarised response: the stats tool
measures and writes the complete arrays; this tool slices them on demand. No
interpretation — numbers and flags only.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ms_inspect.util.formatting import field as fmt_field
from ms_inspect.util.formatting import response_envelope

TOOL_NAME = "ms_calsol_stats_detail"

# Hard cap on returned rows so the deep-dive tool can never itself flood the
# response. A caller wanting more should narrow the filter (antenna/spw/field).
_MAX_ROWS = 300

_VALID_KINDS = {"low_snr", "amp_outliers", "antenna"}


def _enumerate_low_snr(snr: np.ndarray, ants, spws, fields, snr_min: float) -> list[dict]:
    rows: list[dict] = []
    shape = snr.shape
    flat = snr.reshape(-1)
    for fi, val in enumerate(flat):
        if np.isfinite(val) and val < snr_min:
            idx = np.unravel_index(fi, shape)
            rows.append(
                {
                    "antenna": str(ants[idx[0]]),
                    "spw": int(spws[idx[1]]) if len(shape) > 1 else 0,
                    "field": str(fields[idx[2]]) if len(shape) > 2 else "",
                    "snr": round(float(val), 3),
                }
            )
    rows.sort(key=lambda e: e["snr"])
    return rows


def _enumerate_amp_outliers(amp: np.ndarray, ants, spws, fields, amp_sigma: float) -> list[dict]:
    rows: list[dict] = []
    median = float(np.nanmedian(amp))
    mad = float(np.nanmedian(np.abs(amp - median)))
    sigma = 1.4826 * mad if mad > 0 else 0.0
    if sigma <= 0:
        return rows
    shape = amp.shape
    flat = amp.reshape(-1)
    for fi, val in enumerate(flat):
        if np.isfinite(val):
            n_sigma = abs(val - median) / sigma
            if n_sigma > amp_sigma:
                idx = np.unravel_index(fi, shape)
                rows.append(
                    {
                        "antenna": str(ants[idx[0]]),
                        "spw": int(spws[idx[1]]) if len(shape) > 1 else 0,
                        "field": str(fields[idx[2]]) if len(shape) > 2 else "",
                        "amp": round(float(val), 4),
                        "n_sigma": round(float(n_sigma), 2),
                    }
                )
    rows.sort(key=lambda e: e["n_sigma"], reverse=True)
    return rows


def _antenna_slice(npz, ant_idx: int, spws, fields) -> list[dict]:
    """Every stored quantity for one antenna, per (SPW, field)."""
    quantities = [
        "flagged_frac",
        "snr_mean",
        "amp_mean",
        "amp_std",
        "phase_mean_deg",
        "phase_rms_deg",
    ]
    rows: list[dict] = []
    for si, spw in enumerate(spws):
        for fi, fld in enumerate(fields):
            row = {"spw": int(spw), "field": str(fld)}
            for q in quantities:
                if q in npz.files:
                    arr = npz[q]
                    if arr.ndim >= 3:
                        v = float(arr[ant_idx, si, fi])
                        row[q] = round(v, 4) if np.isfinite(v) else None
            rows.append(row)
    return rows


def run(
    npz_path: str,
    kind: str = "low_snr",
    antenna: str = "",
    spw: int | None = None,
    field: str = "",
    max_rows: int = _MAX_ROWS,
) -> dict:
    """
    Slice the raw ms_calsol_stats NPZ sidecar.

    Args:
        npz_path: Path to the {caltable}.calsol_stats.npz written by ms_calsol_stats.
        kind: 'low_snr', 'amp_outliers', or 'antenna' (all quantities for one antenna).
        antenna: Restrict to this antenna name (required for kind='antenna').
        spw: Restrict to this SPW id.
        field: Restrict to this field name.
        max_rows: Row cap (hard-limited to 300).

    Returns:
        Standard response envelope. ms_path field contains npz_path.
    """
    p = Path(npz_path).expanduser().resolve()
    if not p.exists() or not p.is_file():
        from ms_inspect.util.formatting import error_envelope

        return error_envelope(
            TOOL_NAME, npz_path, "NPZ_NOT_FOUND", f"Raw stats NPZ not found: {p}"
        )

    if kind not in _VALID_KINDS:
        from ms_inspect.util.formatting import error_envelope

        return error_envelope(
            TOOL_NAME,
            npz_path,
            "INVALID_KIND",
            f"kind must be one of {sorted(_VALID_KINDS)}; got '{kind}'.",
        )

    npz = np.load(p, allow_pickle=True)
    ants = [str(a) for a in npz["ant_names"]]
    spws = [int(s) for s in npz["spw_ids"]]
    fields = [str(f) for f in npz["field_names"]]
    snr_min = float(npz["snr_min"]) if "snr_min" in npz.files else 3.0
    amp_sigma = float(npz["amp_sigma"]) if "amp_sigma" in npz.files else 5.0
    cap = max(1, min(int(max_rows), _MAX_ROWS))
    casa_calls = [f"np.load({p})"]

    if kind == "antenna":
        if not antenna or antenna not in ants:
            from ms_inspect.util.formatting import error_envelope

            return error_envelope(
                TOOL_NAME,
                npz_path,
                "ANTENNA_NOT_FOUND",
                f"kind='antenna' requires a valid antenna name; '{antenna}' not in {ants}.",
            )
        rows = _antenna_slice(npz, ants.index(antenna), spws, fields)
    elif kind == "low_snr":
        if "snr_mean" not in npz.files:
            rows = []
        else:
            rows = _enumerate_low_snr(npz["snr_mean"], ants, spws, fields, snr_min)
    else:  # amp_outliers
        if "amp_mean" not in npz.files:
            rows = []
        else:
            rows = _enumerate_amp_outliers(npz["amp_mean"], ants, spws, fields, amp_sigma)

    # apply filters
    if antenna and kind != "antenna":
        rows = [r for r in rows if r["antenna"] == antenna]
    if spw is not None:
        rows = [r for r in rows if r.get("spw") == int(spw)]
    if field:
        rows = [r for r in rows if r.get("field") == field]

    n_total = len(rows)
    data = {
        "kind": fmt_field(kind),
        "filters": fmt_field(
            {"antenna": antenna or None, "spw": spw, "field": field or None}
        ),
        "n_total": fmt_field(n_total),
        "n_returned": fmt_field(min(n_total, cap)),
        "truncated": fmt_field(n_total > cap),
        "rows": fmt_field(rows[:cap]),
        "thresholds": fmt_field({"snr_min": snr_min, "amp_sigma": amp_sigma}),
    }
    return response_envelope(
        tool_name=TOOL_NAME, ms_path=npz_path, data=data, warnings=[], casa_calls=casa_calls
    )
