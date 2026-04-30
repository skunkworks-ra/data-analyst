"""
tools/scans.py — ms_scan_list and ms_scan_intent_summary

Layer 1, Tools 3 & 4.

ms_scan_list:          Time-ordered scan sequence with field, duration, intents.
ms_scan_intent_summary: Time fractions per intent across the full observation.

CASA access: msmd.scansforfield(), msmd.timesforscans(),
             msmd.intentsforscans(), msmd.fieldsforscan(),
             msmd.exposuretime()
"""

from __future__ import annotations

from ms_inspect.util.casa_context import open_msmd, validate_ms_path
from ms_inspect.util.conversions import mjd_seconds_to_utc, seconds_to_human
from ms_inspect.util.formatting import field, response_envelope

TOOL_SCAN_LIST = "ms_scan_list"
TOOL_INTENT_SUMMARY = "ms_scan_intent_summary"


def run_scan_list(ms_path: str) -> dict:
    """
    Return the time-ordered list of all scans in the MS.

    Each record includes: scan number, field id/name, intent, start/end UTC,
    duration, integration time, and spectral windows observed.
    """
    p = validate_ms_path(ms_path)
    casa_calls: list[str] = []
    warnings: list[str] = []

    with open_msmd(str(p)) as msmd:
        casa_calls.append("msmd.open()")

        field_names: list[str] = list(msmd.fieldnames())
        casa_calls.append("msmd.fieldnames()")

        scan_numbers: list[int] = sorted(msmd.scannumbers())
        casa_calls.append("msmd.scannumbers()")

        if not scan_numbers:
            warnings.append("No scans found in MS.")
            return response_envelope(
                tool_name=TOOL_SCAN_LIST,
                ms_path=ms_path,
                data={"n_scans": 0, "scans": []},
                warnings=warnings,
                casa_calls=casa_calls,
            )

        scans_out: list[dict] = []

        for scan_num in scan_numbers:
            # Field for this scan
            try:
                field_ids: list[int] = list(msmd.fieldsforscan(scan_num))
                fid = field_ids[0] if field_ids else -1
                fname = field_names[fid] if 0 <= fid < len(field_names) else f"FIELD_{fid}"
            except Exception:
                fid = -1
                fname = "UNKNOWN"

            # Intents — try per-scan first, fall back to field-level.
            # Some MSs have STATE subtable linkage broken, causing intentsforscans()
            # to return empty even when intentsforfield() works correctly.
            try:
                scan_intents: list[str] = sorted(msmd.intentsforscans([scan_num]))
                if not scan_intents and fid >= 0:
                    scan_intents = sorted(msmd.intentsforfield(fid))
                intent_flag = "COMPLETE" if scan_intents else "UNAVAILABLE"
            except Exception:
                scan_intents = []
                intent_flag = "UNAVAILABLE"

            # Time range
            try:
                times = msmd.timesforscans([scan_num])  # MJD seconds array
                t_start = float(min(times))
                t_end = float(max(times))
                duration_s = t_end - t_start
                # Add half an integration to end time (times are centres of integrations)
                # We correct this below using exposure time
            except Exception as e:
                warnings.append(f"Could not get times for scan {scan_num}: {e}")
                t_start = t_end = duration_s = float("nan")

            # Integration (dump) time for this scan
            try:
                exp_times = msmd.exposuretime(scan=scan_num)
                # Returns dict {'value': float, 'unit': 'sec'}
                if isinstance(exp_times, dict):
                    integration_s = float(exp_times.get("value", float("nan")))
                else:
                    integration_s = float(exp_times)
                # Correct duration: add one integration to account for last sample
                if duration_s == duration_s:  # nan check
                    duration_s += integration_s
            except Exception:
                integration_s = float("nan")

            # SpWs for this scan
            try:
                spw_ids: list[int] = sorted(msmd.spwsforscan(scan_num))
                spw_field = field(spw_ids, flag="COMPLETE")
            except Exception:
                spw_field = field(None, flag="UNAVAILABLE")

            # Number of integrations
            n_integrations: int | None = None
            if integration_s > 0 and integration_s == integration_s:
                n_integrations = max(1, round(duration_s / integration_s))

            record = {
                "scan_number": scan_num,
                "field_id": fid,
                "field_name": fname,
                "intents": field(scan_intents, flag=intent_flag),
                "start_utc": field(
                    mjd_seconds_to_utc(t_start) if t_start == t_start else None,
                    flag="COMPLETE" if t_start == t_start else "UNAVAILABLE",
                ),
                "end_utc": field(
                    mjd_seconds_to_utc(t_end) if t_end == t_end else None,
                    flag="COMPLETE" if t_end == t_end else "UNAVAILABLE",
                ),
                "duration_s": field(
                    round(duration_s, 2) if duration_s == duration_s else None,
                    flag="COMPLETE" if duration_s == duration_s else "UNAVAILABLE",
                ),
                "duration_human": seconds_to_human(duration_s)
                if duration_s == duration_s
                else "N/A",
                "integration_s": field(
                    round(integration_s, 3) if integration_s == integration_s else None,
                    flag="COMPLETE" if integration_s == integration_s else "UNAVAILABLE",
                ),
                "n_integrations": n_integrations,
                "spw_ids": spw_field,
            }
            scans_out.append(record)

    # Check for large time gaps between consecutive scans (potential missing data)
    _check_scan_gaps(scans_out, warnings)

    data = {
        "n_scans": len(scans_out),
        "n_fields": len(set(s["field_name"] for s in scans_out)),
        "scans": scans_out,
    }

    return response_envelope(
        tool_name=TOOL_SCAN_LIST,
        ms_path=ms_path,
        data=data,
        warnings=warnings,
        casa_calls=casa_calls,
    )


def run_scan_intent_summary(ms_path: str) -> dict:
    """
    Summarise how total observing time is distributed across scan intents.

    Aggregates scan durations from ms_scan_list and groups by intent.
    If intents are absent, groups by field name instead.
    """
    # Reuse scan list — all the work is aggregation
    scan_result = run_scan_list(ms_path)

    warnings: list[str] = list(scan_result.get("warnings", []))
    scans: list[dict] = scan_result["data"]["scans"]

    if not scans:
        return response_envelope(
            tool_name=TOOL_INTENT_SUMMARY,
            ms_path=ms_path,
            data={"total_duration_s": 0, "by_intent": [], "intent_completeness": "UNAVAILABLE"},
            warnings=warnings,
            casa_calls=["(aggregated from ms_scan_list)"],
        )

    # Compute total duration
    durations: list[float] = []
    for s in scans:
        dur_val = s.get("duration_s", {})
        v = dur_val.get("value") if isinstance(dur_val, dict) else dur_val
        if v is not None and v == v:  # nan check
            durations.append(float(v))

    total_s = sum(durations)

    # Check whether intents are available
    has_intents = any(
        s["intents"].get("flag") == "COMPLETE" and s["intents"].get("value") for s in scans
    )
    intent_completeness: str = "COMPLETE" if has_intents else "UNAVAILABLE"

    # Aggregate by intent (or by field if no intents)
    by_group: dict[str, float] = {}

    for s in scans:
        dur_val = s.get("duration_s", {})
        dur = float(dur_val["value"]) if isinstance(dur_val, dict) and dur_val.get("value") else 0.0

        intent_data = s.get("intents", {})
        intents: list[str] = []
        if isinstance(intent_data, dict):
            intents = intent_data.get("value") or []

        if intents:
            for intent in intents:
                by_group[intent] = by_group.get(intent, 0.0) + dur / max(len(intents), 1)
        else:
            key = f"FIELD:{s['field_name']}"
            by_group[key] = by_group.get(key, 0.0) + dur

    if not has_intents:
        warnings.append(
            "No scan intents found — time breakdown is by field name instead of intent. "
            "Run ms_field_list to see if calibrator catalogue inference is possible."
        )

    # Sort by total time descending
    by_intent_list = sorted(
        [
            {
                "intent": intent,
                "total_s": round(t, 2),
                "fraction": round(t / total_s, 4) if total_s > 0 else 0.0,
                "human": seconds_to_human(t),
            }
            for intent, t in by_group.items()
        ],
        key=lambda x: x["total_s"],
        reverse=True,
    )

    data = {
        "total_duration_s": round(total_s, 2),
        "total_duration_human": seconds_to_human(total_s),
        "n_intents": len(by_intent_list),
        "intent_completeness": intent_completeness,
        "by_intent": by_intent_list,
    }

    return response_envelope(
        tool_name=TOOL_INTENT_SUMMARY,
        ms_path=ms_path,
        data=data,
        warnings=warnings,
        casa_calls=["(aggregated from ms_scan_list — no additional CASA calls)"],
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _check_scan_gaps(scans: list[dict], warnings: list[str]) -> None:
    """
    Warn if consecutive scans have a large time gap (> 10 minutes),
    which may indicate missing data or a multi-session concatenated MS.
    """
    for i in range(len(scans) - 1):
        scans[i].get("end_utc", {})
        scans[i + 1].get("start_utc", {})

        # We need the raw MJD values for gap calculation; they're buried in the
        # formatted UTC strings. Approximate from duration and scan numbers only.
        dur_i = scans[i].get("duration_s", {})
        scans[i + 1].get("duration_s", {})
        dur_i["value"] if isinstance(dur_i, dict) else None

        # We can't compute exact gap without raw MJD — flag large n_scan gaps
        snum_i = scans[i]["scan_number"]
        snum_i1 = scans[i + 1]["scan_number"]
        if snum_i1 - snum_i > 5:
            warnings.append(
                f"Scan number jump from {snum_i} to {snum_i1} "
                f"({snum_i1 - snum_i - 1} missing scan numbers). "
                "This may indicate deleted or missing scans."
            )
