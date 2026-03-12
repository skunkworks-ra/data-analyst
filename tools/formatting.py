"""
util/formatting.py — Response envelope construction and output formatting.

Defines the completeness flag schema and the standard JSON response envelope
described in DESIGN.md §4 and §7.1.

No CASA dependency.
"""

from __future__ import annotations

import importlib.metadata
from typing import Any, Literal

# ---------------------------------------------------------------------------
# Completeness flag literals
# ---------------------------------------------------------------------------
CompletionFlag = Literal["COMPLETE", "INFERRED", "PARTIAL", "SUSPECT", "UNAVAILABLE"]

_FLAG_SEVERITY: dict[str, int] = {
    "COMPLETE":    0,
    "INFERRED":    1,
    "PARTIAL":     2,
    "SUSPECT":     3,
    "UNAVAILABLE": 4,
}


def field(
    value: Any,
    flag: CompletionFlag = "COMPLETE",
    note: str | None = None,
) -> dict:
    """
    Wrap a value with its completeness flag.

    Used to construct every data field in a tool response per DESIGN.md §4.

    Example:
        field(1.4e9, "COMPLETE")
        → {"value": 1400000000.0, "flag": "COMPLETE"}

        field(None, "UNAVAILABLE", note="Telescope name unknown")
        → {"value": None, "flag": "UNAVAILABLE", "note": "..."}
    """
    result: dict[str, Any] = {"value": value, "flag": flag}
    if note is not None:
        result["note"] = note
    return result


def worst_flag(flags: list[CompletionFlag]) -> CompletionFlag:
    """Return the worst-case completeness flag from a list."""
    if not flags:
        return "COMPLETE"
    return max(flags, key=lambda f: _FLAG_SEVERITY.get(f, 0))  # type: ignore[return-value]


def _casa_version() -> str:
    """Return casatools version string, or 'unavailable' if not installed."""
    try:
        return importlib.metadata.version("casatools")
    except importlib.metadata.PackageNotFoundError:
        return "unavailable"


def response_envelope(
    tool_name: str,
    ms_path: str,
    data: dict,
    warnings: list[str] | None = None,
    casa_calls: list[str] | None = None,
    extra_flags: list[CompletionFlag] | None = None,
) -> dict:
    """
    Wrap tool output in the standard response envelope (DESIGN.md §7.1).

    Computes completeness_summary as the worst flag found anywhere in `data`
    (recursively) combined with any flags in `extra_flags`.

    Args:
        tool_name:    Name of the calling tool (e.g. 'ms_observation_info').
        ms_path:      Absolute path to the Measurement Set.
        data:         The tool's result dictionary.
        warnings:     Non-fatal warning strings.
        casa_calls:   List of CASA API calls made (for provenance).
        extra_flags:  Additional flags to fold into completeness_summary.

    Returns:
        Standard envelope dict.
    """
    found_flags = _collect_flags(data)
    if extra_flags:
        found_flags.extend(extra_flags)

    return {
        "tool": tool_name,
        "ms_path": ms_path,
        "status": "ok",
        "completeness_summary": worst_flag(found_flags) if found_flags else "COMPLETE",
        "data": data,
        "warnings": warnings or [],
        "provenance": {
            "casa_calls": casa_calls or [],
            "casatools_version": _casa_version(),
        },
    }


def error_envelope(
    tool_name: str,
    ms_path: str | None,
    error_type: str,
    message: str,
) -> dict:
    """
    Construct a standard error response envelope (DESIGN.md §7.1).
    """
    return {
        "tool": tool_name,
        "ms_path": ms_path,
        "status": "error",
        "error_type": error_type,
        "message": message,
        "data": None,
    }


def _collect_flags(obj: Any) -> list[CompletionFlag]:
    """
    Recursively collect all 'flag' values from a nested dict/list structure.
    """
    flags: list[CompletionFlag] = []
    if isinstance(obj, dict):
        if "flag" in obj and isinstance(obj["flag"], str):
            flags.append(obj["flag"])  # type: ignore[arg-type]
        for v in obj.values():
            flags.extend(_collect_flags(v))
    elif isinstance(obj, list):
        for item in obj:
            flags.extend(_collect_flags(item))
    return flags


# ---------------------------------------------------------------------------
# Numeric formatting helpers
# ---------------------------------------------------------------------------

def round_dict(d: dict, decimals: int = 4) -> dict:
    """
    Recursively round all float values in a nested dict to `decimals` places.
    Leaves non-float values untouched.
    """
    result = {}
    for k, v in d.items():
        if isinstance(v, float):
            result[k] = round(v, decimals)
        elif isinstance(v, dict):
            result[k] = round_dict(v, decimals)
        elif isinstance(v, list):
            result[k] = [
                round(i, decimals) if isinstance(i, float) else i for i in v
            ]
        else:
            result[k] = v
    return result


def truncate_list(items: list, max_items: int = 50) -> tuple[list, bool]:
    """
    Truncate a list to `max_items` entries.
    Returns (truncated_list, was_truncated).
    """
    if len(items) <= max_items:
        return items, False
    return items[:max_items], True
