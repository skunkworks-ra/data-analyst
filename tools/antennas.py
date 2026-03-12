"""
tools/antennas.py — ms_antenna_list and ms_baseline_lengths

Layer 2, Tools 1 & 2.

ms_antenna_list:    Antenna names, ECEF positions, diameters, completeness check.
ms_baseline_lengths: Physical baseline length statistics and derived resolution.

CASA access: tb → ANTENNA subtable; tb → MAIN table for orphan check.
Raises InsufficientMetadataError on incomplete or numeric-only antenna tables.
"""

from __future__ import annotations

import itertools

import numpy as np

from ms_inspect.exceptions import InsufficientMetadataError
from ms_inspect.util.casa_context import open_table, validate_ms_path, validate_subtable
from ms_inspect.util.conversions import (
    angular_resolution_arcsec,
    baseline_length_klambda,
    baseline_length_m,
    ecef_to_geodetic,
    largest_angular_scale_arcsec,
)
from ms_inspect.util.formatting import field, response_envelope

TOOL_ANT     = "ms_antenna_list"
TOOL_BASELINES = "ms_baseline_lengths"


def _read_antenna_table(ms_str: str) -> tuple[dict, list[str]]:
    """
    Read ANTENNA subtable. Returns (antenna_data_dict, casa_calls).

    antenna_data_dict keys: names, stations, positions, diameters, mounts, n_rows
    """
    casa_calls = [f"tb.open('{ms_str}/ANTENNA')"]
    with open_table(ms_str + "/ANTENNA") as tb:
        n_rows     = tb.nrows()
        names      = list(tb.getcol("NAME"))
        stations   = list(tb.getcol("STATION"))
        positions  = tb.getcol("POSITION")   # shape [3, n_ant] ECEF metres
        diameters  = list(tb.getcol("DISH_DIAMETER"))

        try:
            mounts = list(tb.getcol("MOUNT"))
        except Exception:
            mounts = ["UNKNOWN"] * n_rows

    casa_calls.append("tb.getcol(NAME, STATION, POSITION, DISH_DIAMETER, MOUNT)")

    return {
        "names":     names,
        "stations":  stations,
        "positions": positions,  # numpy [3, n]
        "diameters": diameters,
        "mounts":    mounts,
        "n_rows":    n_rows,
    }, casa_calls


def _read_main_antenna_ids(ms_str: str) -> tuple[set[int], list[str]]:
    """
    Read unique antenna IDs from the MAIN table (ANTENNA1 and ANTENNA2 columns).
    Returns (antenna_id_set, casa_calls).
    """
    casa_calls = [f"tb.open('{ms_str}')"]
    with open_table(ms_str) as tb:
        ant1 = set(int(x) for x in tb.getcol("ANTENNA1"))
        ant2 = set(int(x) for x in tb.getcol("ANTENNA2"))
    casa_calls.append("tb.getcol(ANTENNA1), tb.getcol(ANTENNA2)")
    return ant1 | ant2, casa_calls


def run_antenna_list(ms_path: str) -> dict:
    """
    Return the antenna list with ECEF positions, diameters, and completeness check.

    Raises InsufficientMetadataError if:
    - Antenna names are purely numeric (UVFITS artefact)
    - Antenna IDs in MAIN table are not all present in ANTENNA subtable
    """
    p = validate_ms_path(ms_path)
    ms_str = str(p)
    casa_calls: list[str] = []
    warnings:   list[str] = []

    validate_subtable(p, "ANTENNA")
    ant_data, ant_calls = _read_antenna_table(ms_str)
    casa_calls.extend(ant_calls)

    names     = ant_data["names"]
    n_ant     = ant_data["n_rows"]

    # ------------------------------------------------------------------
    # Fail loud check 1: numeric-only antenna names (DESIGN.md §3.3)
    # ------------------------------------------------------------------
    if n_ant > 0 and all(str(n).strip().isdigit() for n in names):
        raise InsufficientMetadataError(
            f"All {n_ant} antenna names are purely numeric "
            f"(e.g. '{names[0]}', '{names[1] if n_ant > 1 else ''}', ...). "
            "This is a known artefact of UVFITS-converted Measurement Sets — "
            "the original station metadata was not preserved.\n\n"
            "Cannot compute baseline lengths, array configuration, shadowing, "
            "or flag fractions without meaningful antenna names and positions.\n\n"
            "To fix: populate the ANTENNA subtable with correct names, stations, "
            "and ECEF positions (ITRF). Contact the observatory archive or use "
            "the original telescope-format data before UVFITS conversion.",
            ms_path=ms_path,
        )

    # ------------------------------------------------------------------
    # Fail loud check 2: orphaned antenna IDs (DESIGN.md §3.3)
    # ------------------------------------------------------------------
    main_ids, main_calls = _read_main_antenna_ids(ms_str)
    casa_calls.extend(main_calls)

    antenna_table_ids = set(range(n_ant))
    orphaned = main_ids - antenna_table_ids

    if orphaned:
        raise InsufficientMetadataError(
            f"Antenna IDs found in MAIN table but absent from ANTENNA subtable: "
            f"{sorted(orphaned)}.\n\n"
            "Cannot compute baseline statistics or per-antenna quantities with "
            "an incomplete antenna table — results would be silently biased "
            "(missing outer antennas would make baseline statistics shorter than reality).\n\n"
            "To fix: add the missing antennas to the ANTENNA subtable with correct "
            "ECEF positions, or re-export from the original telescope format.",
            ms_path=ms_path,
        )

    # ------------------------------------------------------------------
    # Build antenna records
    # ------------------------------------------------------------------
    positions = ant_data["positions"]  # [3, n_ant]
    stations  = ant_data["stations"]
    diameters = ant_data["diameters"]
    mounts    = ant_data["mounts"]

    # Array centre (mean position → geodetic)
    mean_xyz  = tuple(float(positions[i].mean()) for i in range(3))
    lat, lon, height = ecef_to_geodetic(*mean_xyz)

    ants_out: list[dict] = []
    for i in range(n_ant):
        x, y, z = float(positions[0, i]), float(positions[1, i]), float(positions[2, i])

        # Flag positions that are suspiciously at origin
        pos_flag = "COMPLETE"
        pos_note = None
        if abs(x) < 1.0 and abs(y) < 1.0 and abs(z) < 1.0:
            pos_flag = "SUSPECT"
            pos_note = "Position is (0,0,0) — placeholder or corrupt value"
            warnings.append(f"Antenna {names[i]}: position is (0,0,0) — likely a placeholder.")

        diam = float(diameters[i])
        diam_flag = "COMPLETE" if diam > 0 else "SUSPECT"

        ants_out.append({
            "antenna_id":   i,
            "name":         str(names[i]),
            "station":      str(stations[i]),
            "x_m":          field(round(x, 3), flag=pos_flag, note=pos_note),
            "y_m":          field(round(y, 3), flag=pos_flag),
            "z_m":          field(round(z, 3), flag=pos_flag),
            "diameter_m":   field(round(diam, 2), flag=diam_flag),
            "mount":        str(mounts[i]),
        })

    data = {
        "n_antennas":                  n_ant,
        "n_antennas_in_main_table":    len(main_ids - {a for a in main_ids if a == a and False}),
        "n_baselines_cross":           n_ant * (n_ant - 1) // 2,
        "orphaned_antenna_ids":        [],
        "antenna_table_completeness":  "COMPLETE",
        "array_centre_lat_deg":        round(lat, 6),
        "array_centre_lon_deg":        round(lon, 6),
        "array_centre_height_m":       round(height, 1),
        "antennas":                    ants_out,
    }

    return response_envelope(
        tool_name=TOOL_ANT,
        ms_path=ms_path,
        data=data,
        warnings=warnings,
        casa_calls=casa_calls,
    )


def run_baseline_lengths(ms_path: str, spw_centre_freqs_hz: list[float] | None = None) -> dict:
    """
    Compute physical baseline length statistics and derived angular scales.

    Baseline lengths are computed from ANTENNA subtable ECEF positions —
    these are the maximum possible baselines, not the projected UV lengths
    (which depend on hour angle and declination; that is a Layer 3 tool).

    Args:
        spw_centre_freqs_hz: If provided, compute resolution in kλ and arcsec
                             for each frequency. If None, reads from SpW table.
    """
    p = validate_ms_path(ms_path)
    ms_str = str(p)
    casa_calls: list[str] = []
    warnings:   list[str] = []

    validate_subtable(p, "ANTENNA")
    ant_data, ant_calls = _read_antenna_table(ms_str)
    casa_calls.extend(ant_calls)

    names     = ant_data["names"]
    n_ant     = ant_data["n_rows"]
    positions = ant_data["positions"]  # [3, n_ant]

    # Same fail-loud checks as antenna_list
    if n_ant > 0 and all(str(n).strip().isdigit() for n in names):
        raise InsufficientMetadataError(
            "Antenna names are purely numeric — cannot compute baseline lengths. "
            "See ms_antenna_list for details and repair instructions.",
            ms_path=ms_path,
        )

    # Compute all pairwise baseline lengths
    lengths_m: list[tuple[int, int, float]] = []  # (ant_i, ant_j, length_m)

    for i, j in itertools.combinations(range(n_ant), 2):
        pos_i = (float(positions[0, i]), float(positions[1, i]), float(positions[2, i]))
        pos_j = (float(positions[0, j]), float(positions[1, j]), float(positions[2, j]))
        length = baseline_length_m(pos_i, pos_j)
        lengths_m.append((i, j, length))

    if not lengths_m:
        warnings.append("Only one antenna present — no baselines can be formed.")
        return response_envelope(
            tool_name=TOOL_BASELINES,
            ms_path=ms_path,
            data={"n_baselines": 0},
            warnings=warnings,
            casa_calls=casa_calls,
        )

    all_lengths = np.array([l for _, _, l in lengths_m])

    min_len = float(all_lengths.min())
    max_len = float(all_lengths.max())
    med_len = float(np.median(all_lengths))
    mean_len = float(all_lengths.mean())

    # Shortest and longest baseline antenna pairs
    min_idx = int(all_lengths.argmin())
    max_idx = int(all_lengths.argmax())
    min_pair = (names[lengths_m[min_idx][0]], names[lengths_m[min_idx][1]])
    max_pair = (names[lengths_m[max_idx][0]], names[lengths_m[max_idx][1]])

    # Get SpW frequencies if not provided
    if spw_centre_freqs_hz is None:
        spw_centre_freqs_hz = _read_spw_freqs(ms_str, casa_calls)

    # Per-SpW derived quantities
    per_spw: list[dict] = []
    for idx, freq_hz in enumerate(spw_centre_freqs_hz):
        if freq_hz <= 0:
            continue

        max_bl_kl = baseline_length_klambda(max_len, freq_hz)
        min_bl_kl = baseline_length_klambda(min_len, freq_hz)
        res_arcsec = angular_resolution_arcsec(max_len, freq_hz)
        las_arcsec = largest_angular_scale_arcsec(min_len, freq_hz)

        per_spw.append({
            "spw_id":               idx,
            "centre_freq_hz":       freq_hz,
            "max_baseline_klambda": field(round(max_bl_kl, 2)),
            "min_baseline_klambda": field(round(min_bl_kl, 4)),
            "resolution_arcsec":    field(round(res_arcsec, 3), flag="COMPLETE",
                                           note="θ ≈ λ/B_max; ignores weighting and taper"),
            "las_arcsec":           field(round(las_arcsec, 1), flag="COMPLETE",
                                           note="θ_LAS ≈ λ/B_min; maximum recoverable scale"),
        })

    data = {
        "n_baselines":          len(lengths_m),
        "min_baseline_m":       field(round(min_len, 2)),
        "max_baseline_m":       field(round(max_len, 2)),
        "median_baseline_m":    field(round(med_len, 2)),
        "mean_baseline_m":      field(round(mean_len, 2)),
        "shortest_baseline_antennas": list(min_pair),
        "longest_baseline_antennas":  list(max_pair),
        "per_spw_derived":      per_spw,
        "note": (
            "Baseline lengths computed from ANTENNA ECEF positions — "
            "these are physical (not projected) lengths. "
            "Actual UV coverage depends on source declination and hour angle coverage."
        ),
    }

    return response_envelope(
        tool_name=TOOL_BASELINES,
        ms_path=ms_path,
        data=data,
        warnings=warnings,
        casa_calls=casa_calls,
    )


def _read_spw_freqs(ms_str: str, casa_calls: list[str]) -> list[float]:
    """Read SpW centre frequencies from msmetadata."""
    from ms_inspect.util.casa_context import open_msmd
    freqs: list[float] = []
    try:
        with open_msmd(ms_str) as msmd:
            n_spw = msmd.nspw()
            for spw_id in range(n_spw):
                chan_freqs = msmd.chanfreqs(spw_id)
                freqs.append(float(chan_freqs.mean()))
        casa_calls.append("msmd.chanfreqs(spw_id) for each SpW (for kλ conversion)")
    except Exception as e:
        casa_calls.append(f"msmd SpW read failed: {e}")
    return freqs
