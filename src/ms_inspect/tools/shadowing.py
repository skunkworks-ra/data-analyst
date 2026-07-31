"""
tools/shadowing.py — ms_shadowing_report

Layer 2, Tool 5.

Detects antenna shadowing events — one antenna physically blocking another
at low elevations. Shadowed data is corrupted and must be flagged.

Primary method: casatasks.flagdata(mode='shadow', action='calculate')
  Read-only: action='calculate' computes without applying.

Also checks FLAG_CMD subtable for pre-existing online shadow flags.

KNOWN LIMITATION, verified 2026-07-31 against casatasks 6.7.5.18 and a real
VLA MS (3C391, D-config): that call returns an **empty dict**. It applies
nothing, and it also reports nothing, because with action='calculate' flagdata
emits a report only when the run includes a summary agent. So on this CASA
version the tool measures no shadowing at all and returns UNAVAILABLE for
`shadowing_detected` and `shadow_flag_fraction`. It previously returned
`False` / `0.0` flagged COMPLETE, which was a tool that measured nothing
reporting no shadowing.

Only the FLAG_CMD path is functional today. Making the measurement work —
probably mode='list' with a shadow command plus a named summary — has not been
attempted or tested.
"""

from __future__ import annotations

from ms_inspect.util.casa_context import open_table, validate_ms_path
from ms_inspect.util.conversions import mjd_seconds_to_utc
from ms_inspect.util.formatting import field, response_envelope

TOOL_NAME = "ms_shadowing_report"


def _parse_shadow_report(result: object) -> tuple[int | None, int | None, str | None]:
    """
    Extract (n_total, n_flagged) from a flagdata(mode='shadow') return value.

    Returns (None, None, reason) when the return carries no report, so the
    caller can flag UNAVAILABLE instead of publishing a confident zero.

    Verified against casatasks 6.7.5.18 and a real VLA MS: with
    `action='calculate'` and no summary agent in the run,
    `flagdata(mode='shadow', ...)` returns an **empty dict**. The previous code
    read `result.get("total", {})`, took the dict branch, and produced
    n_total = 0, n_flagged = 0, reported as COMPLETE with no warning — a tool
    that measured nothing claiming no shadowing. `action='calculate'` emits a
    report only when the run includes a summary agent, which `mode='shadow'`
    alone does not.

    Whether `mode='list'` with a shadow command plus a named summary recovers
    the counts is NOT established; nothing here has tested it.
    """
    if not isinstance(result, dict) or not result:
        return (
            None,
            None,
            (
                "flagdata(mode='shadow', action='calculate') returned no report "
                f"({result!r}). Shadow fractions are NOT measured — this is not a "
                "finding of zero shadowing. With action='calculate' flagdata emits "
                "a report only when the run includes a summary agent."
            ),
        )

    top = result.get("total")
    if isinstance(top, dict):
        if "total" not in top or "flagged" not in top:
            return (
                None,
                None,
                (
                    "flagdata(mode='shadow') returned a 'total' record without "
                    f"'total'/'flagged' keys (got {sorted(top)}). Shadow fractions "
                    "are NOT measured."
                ),
            )
        return int(top["total"]), int(top["flagged"]), None

    if top is not None and "flagged" in result:
        return int(top), int(result["flagged"]), None

    return (
        None,
        None,
        (
            f"flagdata(mode='shadow') return has no usable 'total' (keys: "
            f"{sorted(result)}). Shadow fractions are NOT measured."
        ),
    )


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

    # None, not 0 — "not measured" and "measured as zero" are different answers
    # and must not share a representation. See _parse_shadow_report.
    n_shadow_flagged: int | None = None
    n_total: int | None = None
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
            n_total, n_shadow_flagged, parse_note = _parse_shadow_report(shadow_result)
            if parse_note is not None:
                warnings.append(parse_note)
                method_flag = "UNAVAILABLE"
                method_value = "flagdata(mode='shadow') returned no report"

            for ant_name, ant_data in (shadow_result or {}).get("antenna", {}).items():
                if not isinstance(ant_data, dict):
                    continue
                n_ant_flagged = int(ant_data.get("flagged", 0))
                n_ant_total = int(ant_data.get("total", 0))
                if n_ant_flagged > 0:
                    shadowed_antennas.append(
                        {
                            "antenna_name": ant_name,
                            "shadow_flag_fraction": round(n_ant_flagged / max(n_ant_total, 1), 4),
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
    measured = n_total is not None and n_shadow_flagged is not None

    if measured and n_total > 0:
        fraction_field = field(round(n_shadow_flagged / n_total, 4))
    elif measured:
        # Genuinely zero rows in the selection: a real answer, but not a
        # fraction. Distinguished from "not measured" below.
        fraction_field = field(
            None, "UNAVAILABLE", note="flagdata reported 0 total rows; no fraction to compute"
        )
    else:
        fraction_field = field(
            None,
            "UNAVAILABLE",
            note=(
                "shadow calculation produced no report; see warnings. Absence "
                "of a measurement is not a measurement of absence."
            ),
        )

    # Never a bare False when nothing was measured. FLAG_CMD alone can still
    # establish that shadowing was flagged online, but it cannot establish the
    # negative.
    if flag_cmd_shadows:
        detected_field = field(
            True,
            "COMPLETE" if measured else "PARTIAL",
            note=None if measured else "from FLAG_CMD entries only; shadow calculation unavailable",
        )
    elif measured:
        detected_field = field(n_shadow_flagged > 0)
    else:
        detected_field = field(
            None,
            "UNAVAILABLE",
            note="shadow calculation unavailable and no FLAG_CMD shadow entries",
        )

    data = {
        "shadowing_detected": detected_field,
        "shadow_flag_fraction": fraction_field,
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
