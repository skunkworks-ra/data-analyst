"""
tools/shadowing.py — ms_shadowing_report

Layer 2, Tool 5.

Detects antenna shadowing events — one antenna physically blocking another
at low elevations. Shadowed data is corrupted and must be flagged.

Primary method: casatasks.flagdata(mode='shadow', action='calculate')
  Returns per-antenna shadow flag fractions without modifying the MS.

Also checks FLAG_CMD subtable for pre-existing online shadow flags.
"""

from __future__ import annotations

from ms_inspect.util.casa_context import open_table, validate_ms_path
from ms_inspect.util.conversions import mjd_seconds_to_utc
from ms_inspect.util.formatting import field, response_envelope

TOOL_NAME = "ms_shadowing_report"


def run(ms_path: str, tolerance_m: float = 0.0) -> dict:
    """
    Report antenna shadowing in the MS.

    Args:
        ms_path:      Path to the Measurement Set.
        tolerance_m:  Shadowing tolerance in metres (default 0.0 = strict).

    Returns:
        Shadow flag fraction, per-antenna breakdown, and FLAG_CMD shadow entries.
    """
    p = validate_ms_path(ms_path)
    ms_str = str(p)
    casa_calls: list[str] = []
    warnings: list[str] = []

    n_shadow_flagged = 0
    n_total = 0
    shadowed_antennas: list[dict] = []
    method_flag = "COMPLETE"
    method_value = "flagdata(mode='shadow')"

    # ------------------------------------------------------------------
    # Primary: casatasks.flagdata(mode='shadow', action='calculate')
    # Read-only — computes shadow geometry from antenna positions.
    # ------------------------------------------------------------------
    try:
        from casatasks import flagdata as _flagdata  # type: ignore[import]
    except ImportError:
        _flagdata = None
        warnings.append("casatasks not available — shadow detection unavailable.")
        method_flag = "INFERRED"
        method_value = "casatasks unavailable"

    if _flagdata is not None:
        casa_calls.append("flagdata(vis=..., mode='shadow', action='calculate')")
        try:
            shadow_result = _flagdata(
                vis=ms_str,
                mode="shadow",
                tolerance=tolerance_m,
                action="calculate",
                savepars=False,
                flagbackup=False,
            )
            # flagdata returns {'total': {'total': N, 'flagged': M}, 'antenna': {...}}
            top = shadow_result.get("total", {})
            if isinstance(top, dict):
                n_total = int(top.get("total", 0))
                n_shadow_flagged = int(top.get("flagged", 0))
            else:
                n_total = int(top or 0)
                n_shadow_flagged = int(shadow_result.get("flagged", 0))

            for ant_name, ant_data in shadow_result.get("antenna", {}).items():
                if not isinstance(ant_data, dict):
                    continue
                n_ant_flagged = int(ant_data.get("flagged", 0))
                n_ant_total = int(ant_data.get("total", 0))
                if n_ant_flagged > 0:
                    shadowed_antennas.append(
                        {
                            "antenna_name": ant_name,
                            "shadow_flag_fraction": round(
                                n_ant_flagged / max(n_ant_total, 1), 4
                            ),
                            "n_flagged": n_ant_flagged,
                            "n_total": n_ant_total,
                        }
                    )

        except Exception as e:
            warnings.append(f"flagdata(mode='shadow') failed: {e}")
            method_flag = "INFERRED"
            method_value = "flagdata(mode='shadow') failed"

    # ------------------------------------------------------------------
    # FLAG_CMD subtable — pre-existing online shadow flags
    # ------------------------------------------------------------------
    flag_cmd_shadows: list[dict] = []
    try:
        with open_table(ms_str + "/FLAG_CMD") as tb:
            casa_calls.append("tb.open(FLAG_CMD)")
            n_rows = tb.nrows()
            if n_rows > 0:
                reasons = tb.getcol("REASON") if tb.iscelldefined("REASON", 0) else []
                commands = tb.getcol("COMMAND") if tb.iscelldefined("COMMAND", 0) else []
                times = tb.getcol("TIME") if tb.iscelldefined("TIME", 0) else []

                for i in range(n_rows):
                    reason = str(reasons[i]) if i < len(reasons) else ""
                    cmd = str(commands[i]) if i < len(commands) else ""
                    if "shadow" in reason.lower() or "shadow" in cmd.lower():
                        flag_cmd_shadows.append(
                            {
                                "row": i,
                                "reason": reason,
                                "command": cmd,
                                "time": mjd_seconds_to_utc(float(times[i]))
                                if i < len(times)
                                else "UNKNOWN",
                            }
                        )
    except Exception as e:
        warnings.append(f"Could not read FLAG_CMD subtable: {e}")

    if flag_cmd_shadows:
        warnings.append(
            f"{len(flag_cmd_shadows)} pre-existing shadow flag command(s) found in FLAG_CMD subtable."
        )

    # ------------------------------------------------------------------
    # Summarise
    # ------------------------------------------------------------------
    shadow_fraction = n_shadow_flagged / max(n_total, 1) if n_total > 0 else 0.0
    shadowing_detected = n_shadow_flagged > 0 or len(flag_cmd_shadows) > 0

    data = {
        "shadowing_detected": shadowing_detected,
        "shadow_flag_fraction": field(round(shadow_fraction, 4)),
        "n_shadow_flagged": n_shadow_flagged,
        "n_total_rows": n_total,
        "tolerance_m": tolerance_m,
        "method": field(method_value, flag=method_flag),
        "shadowed_antennas": shadowed_antennas,
        "flag_cmd_shadow_entries": flag_cmd_shadows,
        "n_flag_cmd_shadow_entries": len(flag_cmd_shadows),
    }

    return response_envelope(
        tool_name=TOOL_NAME,
        ms_path=ms_path,
        data=data,
        warnings=warnings,
        casa_calls=casa_calls,
    )
