"""
tools/online_flags.py — ms_online_flag_stats

Parses a CASA .flagonline.txt file (produced by importasdm) and returns
summary statistics: total command count, antennas flagged, reason code
breakdown, and approximate time range.

No CASA dependency — pure text parsing.
"""

from __future__ import annotations

import re
from pathlib import Path

from ms_inspect.util.formatting import field as fmt_field
from ms_inspect.util.formatting import response_envelope

TOOL_NAME = "ms_online_flag_stats"

# Regex patterns for parsing flagdata list-mode command lines
_RE_ANTENNA = re.compile(r"antenna\s*=\s*['\"]([^'\"]*)['\"]", re.IGNORECASE)
_RE_REASON = re.compile(r"reason\s*=\s*['\"]([^'\"]*)['\"]", re.IGNORECASE)
_RE_TIMERANGE = re.compile(r"timerange\s*=\s*['\"]([^'\"]*)['\"]", re.IGNORECASE)


def run(flag_file: str) -> dict:
    """
    Parse a .flagonline.txt and return summary statistics.

    Args:
        flag_file: Path to the .flagonline.txt produced by importasdm.

    Returns:
        Standard response envelope with n_commands, antennas_flagged,
        reason_breakdown, and time_range (first and last timerange seen).
    """
    # ms_path is not applicable here — use the flag file path as the subject
    ms_path = flag_file
    casa_calls: list[str] = [f"parse_text({flag_file!r})"]
    warnings: list[str] = []

    p = Path(flag_file)
    if not p.exists():
        from ms_inspect.exceptions import MSNotFoundError
        raise MSNotFoundError(
            f"Online flag file not found: {flag_file}",
            ms_path=flag_file,
        )

    raw = p.read_text()
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip() and not ln.strip().startswith("#")]

    n_commands = len(lines)
    antennas_seen: set[str] = set()
    reason_counts: dict[str, int] = {}
    timeranges: list[str] = []

    for line in lines:
        ant_match = _RE_ANTENNA.search(line)
        if ant_match:
            ant_val = ant_match.group(1).strip()
            if ant_val:
                # antenna field can be comma-separated
                for a in ant_val.split(","):
                    a = a.strip()
                    if a:
                        antennas_seen.add(a)

        reason_match = _RE_REASON.search(line)
        reason = reason_match.group(1).strip() if reason_match else "UNSPECIFIED"
        reason_counts[reason] = reason_counts.get(reason, 0) + 1

        tr_match = _RE_TIMERANGE.search(line)
        if tr_match:
            timeranges.append(tr_match.group(1).strip())

    time_range: dict = {}
    if timeranges:
        time_range = {
            "first": timeranges[0],
            "last": timeranges[-1],
            "n_with_timerange": len(timeranges),
        }
    else:
        warnings.append("No timerange fields found in flag commands.")

    data = {
        "flag_file": fmt_field(str(p.resolve())),
        "n_commands": fmt_field(n_commands),
        "n_antennas_flagged": fmt_field(len(antennas_seen)),
        "antennas_flagged": fmt_field(sorted(antennas_seen)) if antennas_seen else fmt_field([]),
        "reason_breakdown": fmt_field(reason_counts),
        "time_range": fmt_field(time_range) if time_range else fmt_field(None, flag="UNAVAILABLE"),
    }

    return response_envelope(
        tool_name=TOOL_NAME,
        ms_path=ms_path,
        data=data,
        warnings=warnings,
        casa_calls=casa_calls,
    )
