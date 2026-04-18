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


def run(ms_path: str, field: str = "", spw: str = "", include_per_scan: bool = False) -> dict:
    """
    Return a complete flag statistics summary for the MS.

    Calls casatasks.flagdata(mode='summary') which returns per-field,
    per-scan, per-SpW, per-antenna, and total flag fractions.

    Args:
        ms_path:          Path to the Measurement Set.
        field:            CASA field selection (empty = all).
        spw:              CASA SpW selection (empty = all).
        include_per_scan: If True, return the full per-scan list. Default False
                          returns a compact scan summary (min/max/mean flag fraction
                          + list of fully-flagged scan IDs only). Use True only
                          when diagnosing a specific scan-level problem.

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
    # flagdata summary returns 'total' as a flat float (total visibility count)
    # and 'flagged' as a flat float at the top level.
    total_count = int(summary.get("total", 0))
    total_flagged = int(summary.get("flagged", 0))
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
    # Per-scan breakdown
    # Full list only when include_per_scan=True; otherwise compact summary.
    # Compact form avoids 16K+ token responses for long observations.
    # ------------------------------------------------------------------
    raw_scan_fracs: list[float] = []
    fully_flagged_scans: list[int] = []
    per_scan_full: list[dict] = []

    for scan_str, scan_data in summary.get("scan", {}).items():
        n_flagged = int(scan_data.get("flagged", 0))
        n_total = int(scan_data.get("total", 0))
        frac = n_flagged / n_total if n_total > 0 else 0.0
        raw_scan_fracs.append(frac)
        if frac >= 1.0:
            fully_flagged_scans.append(int(scan_str))
        if include_per_scan:
            per_scan_full.append(
                {
                    "scan": int(scan_str),
                    "flag_fraction": round(frac, 4),
                    "n_flagged": n_flagged,
                    "n_total": n_total,
                }
            )

    if include_per_scan:
        per_scan_full.sort(key=lambda x: x["scan"])

    fully_flagged_scans.sort()

    scan_summary = fmt_field(
        {
            "n_scans": len(raw_scan_fracs),
            "min_flag_fraction": round(min(raw_scan_fracs), 4) if raw_scan_fracs else None,
            "max_flag_fraction": round(max(raw_scan_fracs), 4) if raw_scan_fracs else None,
            "mean_flag_fraction": (
                round(sum(raw_scan_fracs) / len(raw_scan_fracs), 4) if raw_scan_fracs else None
            ),
            "n_fully_flagged": len(fully_flagged_scans),
            "fully_flagged_scan_ids": fully_flagged_scans,
        }
    )

    # Warn on any fully-flagged entities
    for ent in per_antenna:
        if ent["flag_fraction"]["value"] >= 1.0:
            warnings.append(f"Antenna '{ent['antenna_name']}' is 100% flagged.")
    for ent in per_spw:
        if ent["flag_fraction"]["value"] >= 1.0:
            warnings.append(f"SpW {ent['spw_id']} is 100% flagged.")
    if fully_flagged_scans:
        warnings.append(
            f"{len(fully_flagged_scans)} scan(s) are 100% flagged: {fully_flagged_scans}"
        )

    data: dict = {
        "total_flag_fraction": fmt_field(round(total_frac, 6)),
        "total_flagged": total_flagged,
        "total_visibilities": total_count,
        "per_field": per_field,
        "per_spw": per_spw,
        "per_antenna": per_antenna,
        "scan_summary": scan_summary,
        "flagdata_version": summary.get("flagversion", fmt_field(None, "UNAVAILABLE")),
    }

    if include_per_scan:
        data["per_scan"] = per_scan_full

    return response_envelope(
        tool_name=TOOL_NAME,
        ms_path=ms_path,
        data=data,
        warnings=warnings,
        casa_calls=casa_calls,
    )
