"""
tools/flag_summary.py — ms_flag_summary

Layer 3a, Tool 2.

Calls casatasks.flagdata(mode='summary') to get the complete flag state
of the MS broken down by field, scan, SpW, and antenna.

This is a READ-ONLY audit tool. Use it:
  - BEFORE ms_apply_flags: to capture the baseline flag state
  - AFTER  ms_apply_flags: to verify how much was flagged

The output is the canonical "flag report" that should be included in
any data reduction log.

CASA access:
  - casatasks.flagdata(vis=ms, mode='summary') — the standard CASA flag audit
"""

from __future__ import annotations

from ms_inspect.util.casa_context import validate_ms_path
from ms_inspect.util.formatting import field as fmt_field
from ms_inspect.util.formatting import response_envelope

TOOL_NAME = "ms_flag_summary"


def run(ms_path: str, field: str = "", spw: str = "") -> dict:
    """
    Return a complete flag statistics summary for the MS.

    Calls casatasks.flagdata(mode='summary') which returns per-field,
    per-scan, per-SpW, per-antenna, and total flag fractions.

    Args:
        ms_path: Path to the Measurement Set.
        field:   CASA field selection (empty = all).
        spw:     CASA SpW selection (empty = all).

    This is read-only. No data is modified.
    """
    p = validate_ms_path(ms_path)
    ms_str = str(p)
    casa_calls: list[str] = []
    warnings: list[str] = []

    try:
        import casatasks  # type: ignore[import]
    except ImportError:
        from ms_inspect.exceptions import CASANotAvailableError

        raise CASANotAvailableError(
            "casatasks is not installed. Install with: pip install casatasks",
            ms_path=ms_path,
        ) from None
    casa_calls.append("casatasks.flagdata(vis=..., mode='summary')")

    try:
        summary = casatasks.flagdata(vis=ms_str, mode="summary", field=field, spw=spw)
    except Exception as e:
        from ms_inspect.util.formatting import error_envelope

        return error_envelope(
            tool_name=TOOL_NAME,
            ms_path=ms_path,
            error_type="COMPUTATION_ERROR",
            message=f"flagdata(mode='summary') failed: {e}",
        )

    # ------------------------------------------------------------------
    # Extract top-level total
    # ------------------------------------------------------------------
    total_block = summary.get("total", {})
    total_flagged = int(total_block.get("flagged", 0))
    total_count = int(total_block.get("total", 0))
    total_frac = total_flagged / total_count if total_count > 0 else 0.0

    # ------------------------------------------------------------------
    # Per-field breakdown
    # ------------------------------------------------------------------
    per_field = []
    for fname, fdata in summary.get("field", {}).items():
        n_flagged = int(fdata.get("flagged", 0))
        n_total = int(fdata.get("total", 0))
        frac = n_flagged / n_total if n_total > 0 else 0.0
        per_field.append(
            {
                "field_name": fname,
                "flag_fraction": fmt_field(round(frac, 6)),
                "n_flagged": n_flagged,
                "n_total": n_total,
            }
        )

    # ------------------------------------------------------------------
    # Per-SpW breakdown
    # ------------------------------------------------------------------
    per_spw = []
    for spw_id_str, spw_data in summary.get("spw", {}).items():
        n_flagged = int(spw_data.get("flagged", 0))
        n_total = int(spw_data.get("total", 0))
        frac = n_flagged / n_total if n_total > 0 else 0.0
        per_spw.append(
            {
                "spw_id": int(spw_id_str),
                "flag_fraction": fmt_field(round(frac, 6)),
                "n_flagged": n_flagged,
                "n_total": n_total,
            }
        )
    per_spw.sort(key=lambda x: x["spw_id"])

    # ------------------------------------------------------------------
    # Per-antenna breakdown
    # ------------------------------------------------------------------
    per_antenna = []
    for ant_name, ant_data in summary.get("antenna", {}).items():
        n_flagged = int(ant_data.get("flagged", 0))
        n_total = int(ant_data.get("total", 0))
        frac = n_flagged / n_total if n_total > 0 else 0.0
        per_antenna.append(
            {
                "antenna_name": ant_name,
                "flag_fraction": fmt_field(round(frac, 6)),
                "n_flagged": n_flagged,
                "n_total": n_total,
            }
        )

    # ------------------------------------------------------------------
    # Per-scan breakdown (abbreviated — scan count can be large)
    # ------------------------------------------------------------------
    per_scan = []
    for scan_str, scan_data in summary.get("scan", {}).items():
        n_flagged = int(scan_data.get("flagged", 0))
        n_total = int(scan_data.get("total", 0))
        frac = n_flagged / n_total if n_total > 0 else 0.0
        per_scan.append(
            {
                "scan": int(scan_str),
                "flag_fraction": round(frac, 4),
                "n_flagged": n_flagged,
                "n_total": n_total,
            }
        )
    per_scan.sort(key=lambda x: x["scan"])

    # Warn on any fully-flagged entities
    for ent in per_antenna:
        if ent["flag_fraction"]["value"] >= 1.0:
            warnings.append(f"Antenna '{ent['antenna_name']}' is 100% flagged.")
    for ent in per_spw:
        if ent["flag_fraction"]["value"] >= 1.0:
            warnings.append(f"SpW {ent['spw_id']} is 100% flagged.")

    data = {
        "total_flag_fraction": fmt_field(round(total_frac, 6)),
        "total_flagged": total_flagged,
        "total_visibilities": total_count,
        "per_field": per_field,
        "per_spw": per_spw,
        "per_antenna": per_antenna,
        "per_scan": per_scan,
        "flagdata_version": summary.get("flagversion", fmt_field(None, "UNAVAILABLE")),
    }

    return response_envelope(
        tool_name=TOOL_NAME,
        ms_path=ms_path,
        data=data,
        warnings=warnings,
        casa_calls=casa_calls,
    )
