"""
tools/shadowing.py — ms_shadowing_report

Layer 2, Tool 5.

Detects antenna shadowing events — one antenna physically blocking another
at low elevations. Shadowed data is corrupted and must be flagged.

Primary method: msmd.shadowedAntennas() (CASA 6.x)
Fallback: geometric computation from antenna positions + elevation/azimuth
          (flagged as INFERRED)

Also checks FLAG_CMD subtable for pre-existing online shadow flags.
"""

from __future__ import annotations

import math

from ms_inspect.util.casa_context import open_msmd, open_table, validate_ms_path
from ms_inspect.util.conversions import mjd_seconds_to_utc, seconds_to_human
from ms_inspect.util.formatting import field, response_envelope

TOOL_NAME = "ms_shadowing_report"


def run(ms_path: str, tolerance_m: float = 0.0) -> dict:
    """
    Report antenna shadowing events in the MS.

    Args:
        ms_path:      Path to the Measurement Set.
        tolerance_m:  Shadowing tolerance in metres (default 0.0 = strict).
                      Positive values mean the antenna must be shadowed by
                      more than tolerance_m before it is flagged.

    Returns:
        Shadowing events list, total shadowed duration, and method used.
    """
    p = validate_ms_path(ms_path)
    ms_str = str(p)
    casa_calls: list[str] = []
    warnings: list[str] = []

    # ------------------------------------------------------------------
    # Primary: msmd.shadowedAntennas()
    # ------------------------------------------------------------------
    shadow_events: list[dict] = []
    method_flag   = "COMPLETE"
    method_value  = "msmd.shadowedAntennas"

    with open_msmd(ms_str) as msmd:
        casa_calls.append("msmd.open()")

        field_names = list(msmd.fieldnames())
        scan_nums   = sorted(msmd.scannumbers())

        has_shadow_method = hasattr(msmd, "shadowedAntennas")

        if has_shadow_method:
            casa_calls.append("msmd.shadowedAntennas(tolerance)")
            try:
                shadow_events = _query_shadowed_antennas(
                    msmd, scan_nums, field_names, tolerance_m
                )
            except Exception as e:
                warnings.append(
                    f"msmd.shadowedAntennas() failed: {e}. "
                    "Falling back to geometric shadow computation."
                )
                has_shadow_method = False

        if not has_shadow_method:
            method_flag  = "INFERRED"
            method_value = "geometric (msmd.shadowedAntennas unavailable)"
            warnings.append(
                "msmd.shadowedAntennas() is not available in this CASA version. "
                "Geometric shadow detection is not yet implemented in Phase 1. "
                "Shadow flags from FLAG_CMD subtable are still reported below."
            )

    # ------------------------------------------------------------------
    # FLAG_CMD subtable — pre-existing online shadow flags
    # ------------------------------------------------------------------
    flag_cmd_shadows: list[dict] = []
    try:
        with open_table(ms_str + "/FLAG_CMD") as tb:
            casa_calls.append("tb.open(FLAG_CMD)")
            n_rows = tb.nrows()
            if n_rows > 0:
                reasons  = tb.getcol("REASON")   if tb.iscelldefined("REASON", 0)  else []
                commands = tb.getcol("COMMAND")  if tb.iscelldefined("COMMAND", 0) else []
                times    = tb.getcol("TIME")     if tb.iscelldefined("TIME", 0)    else []

                for i in range(n_rows):
                    reason = str(reasons[i]) if i < len(reasons) else ""
                    cmd    = str(commands[i]) if i < len(commands) else ""
                    if "shadow" in reason.lower() or "shadow" in cmd.lower():
                        flag_cmd_shadows.append({
                            "row":     i,
                            "reason":  reason,
                            "command": cmd,
                            "time":    mjd_seconds_to_utc(float(times[i])) if i < len(times) else "UNKNOWN",
                        })
    except Exception as e:
        warnings.append(f"Could not read FLAG_CMD subtable: {e}")

    if flag_cmd_shadows:
        warnings.append(
            f"{len(flag_cmd_shadows)} pre-existing shadow flag command(s) found in FLAG_CMD subtable."
        )

    # ------------------------------------------------------------------
    # Summarise
    # ------------------------------------------------------------------
    total_shadowed_s = sum(e.get("duration_s", 0) for e in shadow_events)
    shadowing_detected = len(shadow_events) > 0 or len(flag_cmd_shadows) > 0

    data = {
        "shadowing_detected":       shadowing_detected,
        "n_shadow_events":          len(shadow_events),
        "total_shadowed_seconds":   field(round(total_shadowed_s, 2)),
        "total_shadowed_human":     seconds_to_human(total_shadowed_s),
        "tolerance_m":              tolerance_m,
        "method":                   field(method_value, flag=method_flag),
        "shadowed_events":          shadow_events,
        "flag_cmd_shadow_entries":  flag_cmd_shadows,
        "n_flag_cmd_shadow_entries": len(flag_cmd_shadows),
    }

    return response_envelope(
        tool_name=TOOL_NAME,
        ms_path=ms_path,
        data=data,
        warnings=warnings,
        casa_calls=casa_calls,
    )


def _query_shadowed_antennas(
    msmd: object,
    scan_nums: list[int],
    field_names: list[str],
    tolerance_m: float,
) -> list[dict]:
    """
    Query msmd.shadowedAntennas() for each scan and build event records.

    Note: msmd.shadowedAntennas() API varies between CASA versions.
    In CASA 6.5+, it accepts a tolerance parameter.
    We call it per-scan to get time-resolved shadowing information.
    """
    events: list[dict] = []

    for scan_num in scan_nums:
        try:
            # Get time range for this scan
            times = msmd.timesforscans([scan_num])
            t_start = float(min(times))
            t_end   = float(max(times))

            # Get field for this scan
            fids = list(msmd.fieldsforscan(scan_num))
            fid  = fids[0] if fids else 0
            fname = field_names[fid] if 0 <= fid < len(field_names) else f"FIELD_{fid}"

            # Query shadowed antennas — API: returns list of antenna IDs
            # that are shadowed during this scan
            try:
                shadowed = msmd.shadowedAntennas(
                    scan=scan_num, tolerance=tolerance_m
                )
            except TypeError:
                # Older API without tolerance parameter
                shadowed = msmd.shadowedAntennas(scan=scan_num)

            if shadowed is None:
                continue

            shadowed_ids = list(shadowed) if hasattr(shadowed, "__iter__") else []

            for ant_id in shadowed_ids:
                # msmd.shadowedAntennas doesn't return which antenna is doing
                # the shadowing — that requires geometric computation.
                events.append({
                    "antenna_id":              int(ant_id),
                    "shadowing_antenna_id":    field(None, flag="UNAVAILABLE",
                                                      note="Shadowing antenna ID requires geometric computation"),
                    "scan_number":             scan_num,
                    "field_name":              fname,
                    "start_utc":               mjd_seconds_to_utc(t_start),
                    "end_utc":                 mjd_seconds_to_utc(t_end),
                    "duration_s":              round(t_end - t_start, 2),
                })

        except Exception:
            # Per-scan failure — skip rather than abort entire report
            continue

    return events
