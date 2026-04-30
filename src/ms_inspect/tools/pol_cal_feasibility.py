"""
tools/pol_cal_feasibility.py — ms_pol_cal_feasibility

Layer 2, Tool 7 (Phase 1 extension).

Answers a single question: Is full VLA polarisation calibration feasible
for this dataset?

Algorithm:
1. Opens FIELD subtable → field names.
2. Opens SPECTRAL_WINDOW subtable → band centre frequency (median of first SPW).
3. Matches each field against util/pol_calibrators.py (pure Python, no CASA).
4. For each matching pol cal field: computes sky-frame PA at scan midpoints
   using astropy (same formula as geometry.py) and returns Δ(max−min).
5. Interpolates pol properties at observed frequency from 2019 epoch table.
6. Emits structured verdict.

PA convention note:
  Δ(PA) is identical in sky-frame and feed-frame because the offset between
  them is a constant per mount type. Only the absolute zero-point shifts.
  The tool reports Δ only and annotates this in pa_spread_note.
"""

from __future__ import annotations

import math

from ms_inspect.util.casa_context import open_msmd, open_table, validate_ms_path
from ms_inspect.util.conversions import ecef_to_geodetic, mjd_seconds_to_unix
from ms_inspect.util.formatting import field, response_envelope
from ms_inspect.util.pol_calibrators import (
    PolCalEntry,
    lookup_pol,
    pol_properties_at_freq,
)

TOOL_NAME = "ms_pol_cal_feasibility"

# Default PA spread threshold for leakage calibration feasibility.
# 45° is sufficient for Df+QU (which recovers Q,U simultaneously).
# Below 45° the D-term solution becomes degenerate regardless of poltype.
DEFAULT_PA_SPREAD_THRESHOLD_DEG = 45.0

# Pol epoch used for property lookup
POL_DATA_EPOCH = "2019"
POL_DATA_SOURCE = (
    "NRAO VLA Observing Guide Table 8.2.7 + evlapolcal/index.html (scraped March 2026)"
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _read_band_centre_ghz(ms_str: str) -> tuple[float, list[str]]:
    """
    Return the band centre frequency in GHz from the first spectral window.
    Uses the median of all channel frequencies in SPW 0.
    """
    casa_calls = [f"tb.open('{ms_str}/SPECTRAL_WINDOW')", "tb.getcell(CHAN_FREQ, 0)"]
    with open_table(ms_str + "/SPECTRAL_WINDOW") as tb:
        chan_freqs = tb.getcell("CHAN_FREQ", 0)  # Hz, shape (n_chan,)

    import numpy as _np

    centre_hz = float(_np.median(chan_freqs))
    return centre_hz / 1e9, casa_calls


def _read_field_names_and_ids(ms_str: str) -> tuple[list[int], list[str], list[str]]:
    """Return (field_ids, field_names, casa_calls)."""
    casa_calls = ["msmd.fieldnames()"]
    with open_msmd(ms_str) as msmd:
        names = list(msmd.fieldnames())
    return list(range(len(names))), names, casa_calls


def _read_field_coords(ms_str: str) -> tuple[list[tuple[float, float]], list[str]]:
    """
    Return per-field J2000 (ra_rad, dec_rad) list and casa_calls.
    Missing coordinates become (nan, nan).
    """
    import math as _math

    casa_calls = ["msmd.phasecenter(field_id)"]
    coords: list[tuple[float, float]] = []
    with open_msmd(ms_str) as msmd:
        n = len(msmd.fieldnames())
        for fid in range(n):
            try:
                pc = msmd.phasecenter(fid)
                ra = float(pc["m0"]["value"]) % (2 * _math.pi)
                dec = float(pc["m1"]["value"])
                coords.append((ra, dec))
            except Exception:
                coords.append((float("nan"), float("nan")))
    return coords, casa_calls


def _read_array_centre(ms_str: str) -> tuple[float, float, float, list[str]]:
    """Return (lat_deg, lon_deg, height_m, casa_calls)."""
    casa_calls = [f"tb.open('{ms_str}/ANTENNA')", "tb.getcol(POSITION)"]
    with open_table(ms_str + "/ANTENNA") as tb:
        positions = tb.getcol("POSITION")  # [3, n_ant]
    mean_x = float(positions[0].mean())
    mean_y = float(positions[1].mean())
    mean_z = float(positions[2].mean())
    lat, lon, height = ecef_to_geodetic(mean_x, mean_y, mean_z)
    return lat, lon, height, casa_calls


def _scan_times_for_field(ms_str: str, field_id: int) -> tuple[list[float], list[str]]:
    """
    Return list of scan midpoint Unix times for the given field_id.
    casa_calls describes what CASA functions were used.
    """
    casa_calls = ["msmd.scannumbers()", "msmd.timesforscans()", "msmd.fieldsforscan()"]
    times: list[float] = []
    with open_msmd(ms_str) as msmd:
        scan_nums = sorted(msmd.scannumbers())
        for snum in scan_nums:
            try:
                fids = list(msmd.fieldsforscan(snum))
                if field_id not in fids:
                    continue
                raw_times = msmd.timesforscans([snum])
                t_start = float(min(raw_times))
                t_end = float(max(raw_times))
                times.append((t_start + t_end) / 2.0)
            except Exception:
                continue
    return times, casa_calls


def _pa_spread_deg(
    ra_rad: float,
    dec_rad: float,
    t_mid_mjd_s_list: list[float],
    lat_deg: float,
    lon_deg: float,
    height_m: float,
) -> float | None:
    """
    Compute Δ(PA_sky) = max(PA) − min(PA) across the supplied midpoint times.

    Returns None if fewer than 2 valid PA values can be computed.
    Uses the same atan2 formula as geometry.py::_compute_el_pa.
    """
    import astropy.units as u
    from astropy.time import Time

    pa_values: list[float] = []
    lat_rad = math.radians(lat_deg)

    for t_mjd_s in t_mid_mjd_s_list:
        try:
            t_unix = mjd_seconds_to_unix(t_mjd_s)
            t = Time(t_unix, format="unix", scale="utc")
            ha_rad = float(t.sidereal_time("apparent", lon_deg * u.deg).rad) - ra_rad
            pa_sky = math.degrees(
                math.atan2(
                    math.cos(lat_rad) * math.sin(ha_rad),
                    math.sin(lat_rad) * math.cos(dec_rad)
                    - math.cos(lat_rad) * math.sin(dec_rad) * math.cos(ha_rad),
                )
            )
            pa_values.append(pa_sky)
        except Exception:
            continue

    if len(pa_values) < 2:
        return None
    return max(pa_values) - min(pa_values)


# ---------------------------------------------------------------------------
# Verdict logic
# ---------------------------------------------------------------------------


def _compute_verdict(
    has_angle_cal: bool,
    angle_cal_degraded: bool,
    leakage_meets_threshold: bool,
    has_low_pol_source: bool,
) -> tuple[str, str | None]:
    """
    Return (verdict_str, blocker_str | None).

    Verdicts:
      FULL         — angle cal + leakage cal meets PA threshold
      ANGLE_ONLY   — angle cal present (Kcross+Xf feasible), no leakage cal with PA coverage
      LEAKAGE_ONLY — no usable angle cal, but leakage cal present
      DEGRADED     — angle cal present but variability warning
      NOT_FEASIBLE — no pol cal sources found at all
    """
    if has_angle_cal and not angle_cal_degraded and leakage_meets_threshold:
        return "FULL", None

    if has_angle_cal and angle_cal_degraded and leakage_meets_threshold:
        return "DEGRADED", (
            "Angle calibrator flagged as variable or in active flare — "
            "verify current monitoring data before proceeding"
        )

    if not has_angle_cal and (leakage_meets_threshold or has_low_pol_source):
        return "LEAKAGE_ONLY", (
            "No pol angle calibrator observed — R-L phase calibration not possible. "
            "Leakage (D-term) calibration may proceed."
        )

    if has_angle_cal and not leakage_meets_threshold:
        return "ANGLE_ONLY", (
            "Pol angle calibrator present — Kcross and Xf (R-L delay + angle) "
            "calibration is feasible. "
            "However no leakage calibrator with sufficient PA spread was found for D-term "
            "calibration. The phase calibrator (if observed throughout the track) is the "
            "natural leakage cal candidate — verify its PA coverage with "
            "ms_parallactic_angle_vs_time."
        )

    if not has_angle_cal and not leakage_meets_threshold and not has_low_pol_source:
        # Leakage cal may be present but PA spread is too small, or no pol cals at all
        return "NOT_FEASIBLE", (
            "Leakage calibrator found but PA spread is insufficient "
            "for a reliable D-term solution, and no low-polarisation source "
            "with catalogued properties at this frequency was found. "
            "Observe the calibrator at more hour angles."
        )

    return "NOT_FEASIBLE", ("No recognised polarisation calibrators found in the field list.")


# ---------------------------------------------------------------------------
# Main tool entry point
# ---------------------------------------------------------------------------


def run(ms_path: str, pa_spread_threshold_deg: float = DEFAULT_PA_SPREAD_THRESHOLD_DEG) -> dict:
    """
    Assess VLA polarisation calibration feasibility for this dataset.

    Inputs:
        ms_path:                  Path to the Measurement Set.
        pa_spread_threshold_deg:  Minimum PA spread (deg) needed for D-term
                                  calibration (default 60°).

    Returns:
        Standard response envelope with data fields:
          band_centre_ghz, pol_angle_calibrator, leakage_calibrator,
          verdict, blocker, pol_cal_data_epoch, pol_cal_data_source.
    """
    p = validate_ms_path(ms_path)
    ms_str = str(p)
    casa_calls: list[str] = []
    warnings: list[str] = []

    # --- Band centre frequency ---
    try:
        band_ghz, bw_calls = _read_band_centre_ghz(ms_str)
        casa_calls.extend(bw_calls)
        band_ghz_field = field(round(band_ghz, 4), flag="COMPLETE")
    except Exception as e:
        warnings.append(f"Could not read band centre frequency: {e}")
        band_ghz = float("nan")
        band_ghz_field = field(None, flag="UNAVAILABLE", note=str(e))

    # --- Field names ---
    field_ids, field_names, fn_calls = _read_field_names_and_ids(ms_str)
    casa_calls.extend(fn_calls)

    # --- Array centre ---
    lat, lon, height, arr_calls = _read_array_centre(ms_str)
    casa_calls.extend(arr_calls)

    # --- Field coordinates ---
    field_coords, fc_calls = _read_field_coords(ms_str)
    casa_calls.extend(fc_calls)

    # --- Match fields against pol calibrator catalogue ---
    angle_cal_entry: PolCalEntry | None = None
    angle_cal_name: str | None = None

    leakage_cal_entry: PolCalEntry | None = None
    leakage_cal_field_id: int | None = None
    leakage_cal_name: str | None = None

    for fid, fname in zip(field_ids, field_names, strict=False):
        entry = lookup_pol(fname)
        if entry is None:
            continue
        if "angle" in entry.role and (
            angle_cal_entry is None or entry.category < angle_cal_entry.category
        ):
            angle_cal_entry = entry
            angle_cal_name = fname
        if "leakage" in entry.role and leakage_cal_entry is None:
            leakage_cal_entry = entry
            leakage_cal_field_id = fid
            leakage_cal_name = fname

    # Do NOT auto-promote the angle cal to leakage cal even if its catalogue
    # role includes "leakage". 3C286 and 3C138 are bookend-observed (1-2 scans)
    # so their PA spread is almost always insufficient for D-terms. The leakage
    # calibrator must be a separately identified source with adequate PA coverage
    # (typically the phase cal observed throughout the track).

    # --- Fallback: use scan intents to identify pol cals not in the catalogue ---
    # msmd.intentsforfield() returns the complete intent set for a field, populated
    # even when per-scan STATE-ID linkage is broken. Use it to rescue pol cal
    # identification when catalogue lookup finds nothing.
    if angle_cal_entry is None or leakage_cal_field_id is None:
        with open_msmd(ms_str) as msmd:
            casa_calls.append("msmd.intentsforfield() (pol cal intent fallback)")
            for fid, fname in zip(field_ids, field_names, strict=False):
                try:
                    intents = set(msmd.intentsforfield(fid))
                except Exception:
                    intents = set()
                if not intents:
                    continue
                if angle_cal_entry is None and any(
                    "POL_ANGLE" in i for i in intents
                ):
                    # Field has CALIBRATE_POL_ANGLE intent — treat as angle cal.
                    # Look up in the pol catalogue using a substring match on the name.
                    entry = lookup_pol(fname)
                    if entry is not None and "angle" in entry.role:
                        angle_cal_entry = entry
                        angle_cal_name = fname
                    else:
                        warnings.append(
                            f"Field '{fname}' has CALIBRATE_POL_ANGLE intent but is not in "
                            "the pol calibrator catalogue — pol angle properties unavailable."
                        )
                if leakage_cal_field_id is None and any(
                    "POL_LEAKAGE" in i for i in intents
                ):
                    # Field has CALIBRATE_POL_LEAKAGE intent — use as leakage cal.
                    # leakage_cal_entry stays None (no catalogue properties), but
                    # PA spread is still computed from scan times.
                    leakage_cal_field_id = fid
                    leakage_cal_name = fname
                    warnings.append(
                        f"Leakage calibrator '{fname}' identified from CALIBRATE_POL_LEAKAGE "
                        "intent; not in catalogue — using PA spread from scan times only."
                    )

    # --- Pol properties at observed frequency ---
    angle_frac_field = field(None, flag="UNAVAILABLE")
    angle_pa_field = field(None, flag="UNAVAILABLE")
    angle_stable_pa = False
    angle_degraded = False
    variability_warn: str | None = None

    if angle_cal_entry is not None and not math.isnan(band_ghz):
        props = pol_properties_at_freq(angle_cal_entry, band_ghz, epoch=POL_DATA_EPOCH)
        if props is not None:
            frac_flag = "INFERRED" if props.frac_pol_upper_limit else "COMPLETE"
            angle_frac_field = field(
                round(props.frac_pol_pct, 2) if props.frac_pol_pct is not None else None,
                flag=frac_flag,
                note="Upper limit only" if props.frac_pol_upper_limit else None,
            )
            pa_flag = "COMPLETE" if props.pol_angle_deg is not None else "UNAVAILABLE"
            angle_pa_field = field(
                round(props.pol_angle_deg, 1) if props.pol_angle_deg is not None else None,
                flag=pa_flag,
                note="PA unstable or unmeasurable at this frequency"
                if props.pol_angle_deg is None
                else None,
            )
        else:
            angle_frac_field = field(
                None,
                flag="UNAVAILABLE",
                note=f"Frequency {band_ghz:.2f} GHz out of tabulated range for {angle_cal_entry.b1950_name}",
            )

        angle_stable_pa = angle_cal_entry.stable_pa
        if angle_cal_entry.variability_note:
            angle_degraded = True
            variability_warn = angle_cal_entry.variability_note
            warnings.append(
                f"Pol angle calibrator {angle_cal_entry.b1950_name}: "
                f"{angle_cal_entry.variability_note}"
            )

    # --- PA spread for leakage calibrator ---
    pa_spread_val: float | None = None
    n_cal_scans: int = 0
    meets_threshold: bool = False

    leakage_source_name = leakage_cal_name or angle_cal_name  # fallback
    leakage_source_entry = leakage_cal_entry or angle_cal_entry

    if leakage_cal_field_id is not None:
        t_mids, sc_calls = _scan_times_for_field(ms_str, leakage_cal_field_id)
        casa_calls.extend(sc_calls)
        n_cal_scans = len(t_mids)

        ra_rad, dec_rad = (
            field_coords[leakage_cal_field_id]
            if leakage_cal_field_id < len(field_coords)
            else (float("nan"), float("nan"))
        )

        if not (math.isnan(ra_rad) or math.isnan(dec_rad)):
            try:
                spread = _pa_spread_deg(ra_rad, dec_rad, t_mids, lat, lon, height)
                pa_spread_val = spread
            except Exception as e:
                warnings.append(f"PA spread computation failed: {e}")

        if leakage_source_entry and leakage_source_entry.single_scan_sufficient:
            # Category C: known low-pol source, one scan is enough for D-terms
            meets_threshold = n_cal_scans >= 1
        else:
            meets_threshold = pa_spread_val is not None and pa_spread_val >= pa_spread_threshold_deg

    # --- Leakage cal pol properties ---
    has_low_pol_source = False
    if leakage_source_entry is not None and not math.isnan(band_ghz):
        lp = pol_properties_at_freq(leakage_source_entry, band_ghz, epoch=POL_DATA_EPOCH)
        if lp is not None and lp.frac_pol_pct is not None:
            has_low_pol_source = lp.frac_pol_pct < 1.0 or lp.frac_pol_upper_limit

    # --- Verdict ---
    has_angle_cal = angle_cal_entry is not None
    verdict, blocker = _compute_verdict(
        has_angle_cal=has_angle_cal,
        angle_cal_degraded=angle_degraded,
        leakage_meets_threshold=meets_threshold,
        has_low_pol_source=has_low_pol_source,
    )

    # --- Build output ---
    if pa_spread_val is not None:
        pa_spread_field = field(round(pa_spread_val, 2), flag="COMPLETE")
    else:
        pa_spread_field = field(
            None,
            flag="UNAVAILABLE",
            note="No leakage calibrator scans found or coordinates invalid",
        )

    data = {
        "band_centre_ghz": band_ghz_field,
        "pol_angle_calibrator": {
            "available": has_angle_cal,
            "source": angle_cal_entry.b1950_name if angle_cal_entry else None,
            "j2000": angle_cal_entry.j2000_name if angle_cal_entry else None,
            "category": angle_cal_entry.category if angle_cal_entry else None,
            "frac_pol_pct": angle_frac_field,
            "pol_angle_deg": angle_pa_field,
            "stable_pa": angle_stable_pa,
            "variability_warning": variability_warn,
        },
        "leakage_calibrator": {
            "available": leakage_cal_entry is not None,
            "source": leakage_source_name,
            "category": leakage_source_entry.category if leakage_source_entry else None,
            "single_scan_sufficient": leakage_source_entry.single_scan_sufficient if leakage_source_entry else False,
            "pa_spread_deg": pa_spread_field,
            "pa_spread_note": (
                "Delta computed via astropy sky-frame PA; "
                "CASA feed-frame differs by -90° for ALT-AZ mounts "
                "but delta is identical in both conventions. "
                "PA spread is irrelevant for Category C sources (single_scan_sufficient=true)."
            ),
            "n_calibrator_scans": n_cal_scans,
            "meets_threshold": meets_threshold,
            "threshold_deg": pa_spread_threshold_deg,
        },
        "verdict": verdict,
        "blocker": blocker,
        "pol_cal_data_epoch": POL_DATA_EPOCH,
        "pol_cal_data_source": POL_DATA_SOURCE,
    }

    return response_envelope(
        tool_name=TOOL_NAME,
        ms_path=ms_path,
        data=data,
        warnings=warnings,
        casa_calls=casa_calls,
    )
