"""
tools/observation.py — ms_observation_info

Layer 1, Tool 1: Who observed this, when, with what telescope, for how long?

CASA access: tb → OBSERVATION subtable
Raises InsufficientMetadataError if TELESCOPE_NAME is missing/unknown.
"""

from __future__ import annotations

from ms_inspect.exceptions import InsufficientMetadataError
from ms_inspect.util.casa_context import open_table, validate_ms_path, validate_subtable
from ms_inspect.util.conversions import mjd_seconds_to_utc, seconds_to_human
from ms_inspect.util.formatting import field, response_envelope

TOOL_NAME = "ms_observation_info"

_UNKNOWN_TELESCOPE_NAMES = {"", "unknown", "n/a", "none", "undefined"}


def run(ms_path: str) -> dict:
    """
    Retrieve observation-level metadata from the OBSERVATION subtable.

    Returns telescope name, observer, project code, UTC time range,
    total duration, and a count of HISTORY table entries.

    Raises InsufficientMetadataError if TELESCOPE_NAME is absent or unrecognised.
    """
    p = validate_ms_path(ms_path)
    validate_subtable(p, "OBSERVATION")

    casa_calls: list[str] = []
    warnings: list[str] = []

    # ------------------------------------------------------------------
    # Read OBSERVATION subtable
    # ------------------------------------------------------------------
    obs_table = str(p / "OBSERVATION")
    casa_calls.append(f"tb.open('{obs_table}')")
    casa_calls.append("tb.getcol('TELESCOPE_NAME', 'OBSERVER', 'PROJECT', 'TIME_RANGE')")

    rows: list[dict] = []

    with open_table(obs_table) as tb:
        n_rows = tb.nrows()
        if n_rows == 0:
            raise InsufficientMetadataError(
                "OBSERVATION subtable is empty — cannot determine telescope or time range. "
                "The MS may be corrupted or produced by a non-standard converter.",
                ms_path=ms_path,
            )

        telescope_names: list[str] = list(tb.getcol("TELESCOPE_NAME"))
        observers:       list[str] = list(tb.getcol("OBSERVER"))
        # PROJECT may not exist in all MSs
        try:
            projects: list[str] = list(tb.getcol("PROJECT"))
        except Exception:
            projects = [""] * n_rows
            warnings.append("PROJECT column absent from OBSERVATION subtable.")

        time_ranges = tb.getcol("TIME_RANGE")  # shape [2, n_rows] MJD seconds

    # ------------------------------------------------------------------
    # Telescope name validation — fail loud (DESIGN.md §3.2)
    # ------------------------------------------------------------------
    primary_telescope = telescope_names[0].strip()

    if primary_telescope.lower() in _UNKNOWN_TELESCOPE_NAMES:
        raise InsufficientMetadataError(
            f"TELESCOPE_NAME is '{primary_telescope}' — cannot identify the telescope.\n\n"
            "Cannot determine band, primary beam, array configuration, or any "
            "telescope-specific quantity without a valid telescope name.\n\n"
            "To fix, set the telescope name in the OBSERVATION subtable:\n"
            "    from casatools import table as tb\n"
            "    tb.open('<ms>/OBSERVATION', nomodify=False)\n"
            "    tb.putcell('TELESCOPE_NAME', 0, 'VLA')   # or 'MeerKAT', 'GMRT'\n"
            "    tb.close()\n\n"
            "Then re-run this tool.",
            ms_path=ms_path,
        )

    # Multiple OBSERVATION rows = concatenated MSs
    if n_rows > 1:
        unique_telescopes = set(t.strip() for t in telescope_names)
        warnings.append(
            f"OBSERVATION subtable has {n_rows} rows (concatenated MS). "
            f"Telescopes present: {sorted(unique_telescopes)}. "
            "Reporting time range across all rows."
        )

    # ------------------------------------------------------------------
    # Time range
    # ------------------------------------------------------------------
    # time_ranges shape: [2, n_rows] where row 0 = start, row 1 = end
    all_starts = time_ranges[0]
    all_ends   = time_ranges[1]
    obs_start  = float(min(all_starts))
    obs_end    = float(max(all_ends))
    total_s    = obs_end - obs_start

    # Sanity check: non-contiguous rows
    if n_rows > 1:
        for i in range(n_rows - 1):
            gap = all_starts[i + 1] - all_ends[i]
            if gap > 300:  # > 5 minutes gap between rows
                warnings.append(
                    f"Non-contiguous time ranges between OBSERVATION rows "
                    f"{i} and {i+1}: gap of {seconds_to_human(gap)}. "
                    "This may indicate separate observing sessions concatenated together."
                )

    # ------------------------------------------------------------------
    # HISTORY entry count (provenance indicator)
    # ------------------------------------------------------------------
    history_count: int | None = None
    history_flag = "COMPLETE"
    hist_table = str(p / "HISTORY")
    try:
        validate_subtable(p, "HISTORY")
        with open_table(hist_table) as tb:
            history_count = tb.nrows()
        casa_calls.append(f"tb.open('{hist_table}') → nrows()")
    except Exception:
        history_flag = "UNAVAILABLE"
        warnings.append("HISTORY subtable absent or unreadable.")

    # ------------------------------------------------------------------
    # Assemble result
    # ------------------------------------------------------------------
    data = {
        "telescope_name": field(primary_telescope),
        "observer":       field(observers[0].strip() if observers[0].strip() else None,
                                flag="UNAVAILABLE" if not observers[0].strip() else "COMPLETE"),
        "project_code":   field(projects[0].strip() if projects[0].strip() else None,
                                flag="UNAVAILABLE" if not projects[0].strip() else "COMPLETE"),
        "obs_start_utc":  field(mjd_seconds_to_utc(obs_start)),
        "obs_end_utc":    field(mjd_seconds_to_utc(obs_end)),
        "total_duration_s":      field(round(total_s, 2)),
        "total_duration_human":  seconds_to_human(total_s),
        "n_observation_rows":    n_rows,
        "history_entries":       field(history_count, flag=history_flag),
    }

    if n_rows > 1:
        data["all_telescopes"] = field(sorted(set(t.strip() for t in telescope_names)))

    return response_envelope(
        tool_name=TOOL_NAME,
        ms_path=ms_path,
        data=data,
        warnings=warnings,
        casa_calls=casa_calls,
    )
