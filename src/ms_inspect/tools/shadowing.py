"""
tools/shadowing.py — ms_shadowing_report

Layer 2, Tool 5.

Detects antenna shadowing events — one antenna physically blocking another
at low elevations. Shadowed data is corrupted and must be flagged.

Primary method: one read-only `flagdata(mode='list', action='calculate')` run
carrying three agents, a summary, the shadow agent, and a second summary:

    mode='summary' name='shadow_before'
    mode='shadow' tolerance=<tolerance_m>
    mode='summary' name='shadow_after'

The shadow contribution is the difference between the two summaries. Nothing is
written: `action='calculate'` computes the flags in memory, and the trailing
summary counts that in-memory state.

Also checks FLAG_CMD subtable for pre-existing online shadow flags.

Why not `flagdata(mode='shadow')` on its own, which is what this tool used to
call: verified 2026-07-31 against casatasks 6.7.5.18 and a real VLA MS (3C391,
D-config), that call returns an **empty dict**. It computes but reports nothing,
because with `action='calculate'` flagdata emits a report only when the run
includes a summary agent. The old code read that empty dict as
`shadow_flag_fraction = 0.0`, COMPLETE.

The three-agent form was verified on the same MS and CASA version. Control:
substituting `mode='manual' antenna='0'` for the shadow agent moves the delta
by 13,339,392 of 216,417,024, so the trailing summary demonstrably sees what the
middle agent did. With the shadow agent the delta is 0, which agrees with the
geometry (minimum projected baseline 28.0 m against 25 m dishes) and with
`action='apply'` on a scratch copy. Zero shadowing here is a real measurement,
not a silence.

`tolerance_m` is passed through but NOT verified: the delta stays 0 for
tolerances from 0 to 1e6 m, under both calculate and apply, which we could not
explain. Treat a non-default tolerance as unproven.
"""

from __future__ import annotations

from ms_inspect.util.casa_context import open_table, validate_ms_path
from ms_inspect.util.conversions import mjd_seconds_to_utc
from ms_inspect.util.formatting import field, response_envelope

TOOL_NAME = "ms_shadowing_report"


_SUMMARY_BEFORE = "shadow_before"
_SUMMARY_AFTER = "shadow_after"


def _summaries_by_name(result: object) -> dict[str, dict]:
    """
    Index the summary records of a flagdata(mode='list') return by their name.

    The return shape is arity-dependent, which is why the CASA docs appear to
    contradict themselves (settled 2026-07-31 against casatasks 6.7.5.18):
    a single summary agent yields a flat record whose 'name' is the one given,
    two or more yield {'report0': {...}, 'report1': {...}} with 'name' inside.
    Both are handled; anything else raises rather than degrading, because a
    `.get(key, {})` here is what made the previous per-SpW attempt fail
    silently (fix-plan Excluded item B).
    """
    if not isinstance(result, dict) or not result:
        raise ValueError(f"flagdata(mode='list') returned no report ({result!r})")

    if "name" in result and "flagged" in result:
        return {str(result["name"]): result}

    reports = {k: v for k, v in result.items() if k.startswith("report")}
    if not reports:
        raise ValueError(
            f"flagdata(mode='list') return has neither a flat summary nor reportN "
            f"records (keys: {sorted(result)})"
        )
    by_name: dict[str, dict] = {}
    for key, rec in reports.items():
        if not isinstance(rec, dict) or "name" not in rec:
            raise ValueError(f"flagdata(mode='list') record {key!r} has no 'name' field")
        by_name[str(rec["name"])] = rec
    return by_name


def _shadow_delta(result: object) -> tuple[int, int, list[dict]]:
    """
    Return (n_total, n_shadow_flagged, per_antenna) from the before/after pair.

    The shadow contribution is after − before: the leading summary establishes
    the flags already in the MS, so the difference isolates what the shadow
    agent would add. Per-antenna counts are the same difference, taken from the
    'antenna' sub-records.

    Raises if either named summary is missing — that is a broken call, not a
    finding of zero shadowing.
    """
    by_name = _summaries_by_name(result)
    for wanted in (_SUMMARY_BEFORE, _SUMMARY_AFTER):
        if wanted not in by_name:
            raise ValueError(
                f"flagdata(mode='list') report has no summary named {wanted!r} "
                f"(got {sorted(by_name)})"
            )
    before, after = by_name[_SUMMARY_BEFORE], by_name[_SUMMARY_AFTER]

    for rec, label in ((before, _SUMMARY_BEFORE), (after, _SUMMARY_AFTER)):
        if "flagged" not in rec or "total" not in rec:
            raise ValueError(f"summary {label!r} has no 'flagged'/'total' (keys: {sorted(rec)})")

    n_total = int(after["total"])
    n_shadow = int(after["flagged"]) - int(before["flagged"])

    per_antenna: list[dict] = []
    ants_before = before.get("antenna") or {}
    ants_after = after.get("antenna") or {}
    if isinstance(ants_after, dict) and isinstance(ants_before, dict):
        for ant_name, rec_after in sorted(ants_after.items()):
            if not isinstance(rec_after, dict):
                continue
            rec_before = ants_before.get(ant_name)
            flagged_before = (
                int(rec_before["flagged"])
                if isinstance(rec_before, dict) and "flagged" in rec_before
                else 0
            )
            n_ant_shadow = int(rec_after.get("flagged", 0)) - flagged_before
            n_ant_total = int(rec_after.get("total", 0))
            if n_ant_shadow > 0:
                per_antenna.append(
                    {
                        "antenna_name": ant_name,
                        "shadow_flag_fraction": round(n_ant_shadow / max(n_ant_total, 1), 4),
                        "n_flagged": n_ant_shadow,
                        "n_total": n_ant_total,
                    }
                )

    return n_total, n_shadow, per_antenna


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
    # and must not share a representation. See _shadow_delta.
    n_shadow_flagged: int | None = None
    n_total: int | None = None
    shadowed_antennas: list[dict] = []
    method_flag = "COMPLETE"
    method_value = "flagdata(mode='list', action='calculate') [summary, shadow, summary]"

    # ------------------------------------------------------------------
    # Primary: one flagdata(mode='list', action='calculate') run,
    # [summary, shadow, summary]. Read-only; the delta is the shadow answer.
    # ------------------------------------------------------------------
    try:
        from casatasks import flagdata as _flagdata  # type: ignore[import]
    except ImportError:
        _flagdata = None
        warnings.append("casatasks not available — shadow detection unavailable.")
        method_flag = "INFERRED"
        method_value = "casatasks unavailable"

    if _flagdata is not None:
        inpfile = [
            f"mode='summary' name='{_SUMMARY_BEFORE}'",
            f"mode='shadow' tolerance={tolerance_m}",
            f"mode='summary' name='{_SUMMARY_AFTER}'",
        ]
        casa_calls.append(f"flagdata(vis=..., mode='list', action='calculate', inpfile={inpfile})")
        try:
            shadow_result = _flagdata(
                vis=ms_str,
                mode="list",
                inpfile=inpfile,
                action="calculate",
                savepars=False,
                flagbackup=False,
            )
            n_total, n_shadow_flagged, shadowed_antennas = _shadow_delta(shadow_result)
        except ValueError as e:
            # Malformed or missing report: not a finding of zero shadowing.
            warnings.append(
                f"Could not read the shadow measurement: {e}. Shadow fractions are "
                "NOT measured; this is not a finding of zero shadowing."
            )
            method_flag = "UNAVAILABLE"
            method_value = "flagdata(mode='list') returned no usable summary pair"
        except Exception as e:
            warnings.append(f"flagdata(mode='list') shadow run failed: {e}")
            method_flag = "INFERRED"
            method_value = "flagdata(mode='list') shadow run failed"

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
        "tolerance_m": field(
            tolerance_m,
            flag="COMPLETE" if tolerance_m == 0.0 else "SUSPECT",
            note=None
            if tolerance_m == 0.0
            else (
                "Passed to the shadow agent but unverified: on casatasks 6.7.5.18 "
                "the shadow contribution did not respond to tolerance over the "
                "range 0 to 1e6 m. Do not read a non-default tolerance as having "
                "taken effect."
            ),
        ),
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
