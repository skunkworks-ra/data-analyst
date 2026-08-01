"""
tools/pol_cal_conditions.py — ms_pol_cal_conditions

Measures the polarisation-calibration *conditions* of a dataset. It does not
decide whether polarisation calibration should proceed: that depends on the
science goal (how small a fractional polarisation is being claimed) and on the
risk tolerance, neither of which is visible from the MS.

What it reports:
1. Band centre frequency (median of SPW 0 channel frequencies).
2. Which observed fields match the bundled VLA pol calibrator catalogue, and
   their polarisation properties interpolated to the observed frequency.
3. The effective role of each catalogued source AT the observing band — a
   source's role is frequency-dependent, not fixed.
4. Parallactic-angle spread and scan count for every field, ranked, so the
   skill can pick a leakage calibrator under its own tolerance.
5. The PA reference levels in circulation, as labelled constants with
   provenance, so a caller can apply whichever one it wants.

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
    LOW_POL_FRAC_PCT,
    POL_DATA_EPOCH,
    PolCalEntry,
    effective_role_at_band,
    lookup_pol,
    pol_properties_at_freq,
)

TOOL_NAME = "ms_pol_cal_conditions"

# PA-spread reference levels for the Df+QU (unknown-pol) leakage path only.
# Reported as constants, never applied as a gate: PA coverage determines how
# well constrained a Df+QU solve is, on a continuum, and the consequence of
# thin coverage is a wider uncertainty on the D-terms, not a failure.
# Irrelevant to Xf and to known-pol / zero-pol Df, which need no PA coverage.
PA_SPREAD_NRAO_RECOMMENDED_DEG = 60.0
PA_SPREAD_PRACTICAL_FLOOR_DEG = 30.0
PA_SPREAD_PROVENANCE = (
    "NRAO VLA polarisation guide recommends >=60 deg of parallactic coverage "
    "for a Df+QU solve. ~30 deg is the practical floor below which the "
    "D-term / source-QU separation becomes strongly degenerate even on a bright "
    "source. Both are reference levels on a continuum, not thresholds: see "
    "09-polcal-execution.md for the consequence at each coverage level."
)

# LOW_POL_FRAC_PCT and POL_DATA_EPOCH are re-exported from util.pol_calibrators,
# alongside effective_role_at_band which applies them.
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


def _read_fields(
    ms_str: str,
) -> tuple[list[str], list[tuple[float, float]], dict[int, list[float]], list[str]]:
    """
    One msmd pass over the field table.

    Returns (field_names, coords, scan_midpoints_by_field, casa_calls) where
    coords is per-field J2000 (ra_rad, dec_rad) — (nan, nan) when unreadable —
    and scan_midpoints_by_field maps field_id to the midpoint time (MJD
    seconds) of every scan that observed it.
    """
    casa_calls = [
        "msmd.fieldnames()",
        "msmd.phasecenter(field_id)",
        "msmd.scannumbers()",
        "msmd.fieldsforscan()",
        "msmd.timesforscans()",
    ]
    coords: list[tuple[float, float]] = []
    midpoints: dict[int, list[float]] = {}

    with open_msmd(ms_str) as msmd:
        names = list(msmd.fieldnames())
        for fid in range(len(names)):
            try:
                pc = msmd.phasecenter(fid)
                ra = float(pc["m0"]["value"]) % (2 * math.pi)
                dec = float(pc["m1"]["value"])
                coords.append((ra, dec))
            except Exception:
                coords.append((float("nan"), float("nan")))
            midpoints[fid] = []

        for snum in sorted(msmd.scannumbers()):
            try:
                raw_times = msmd.timesforscans([snum])
                t_mid = (float(min(raw_times)) + float(max(raw_times))) / 2.0
                for fid in msmd.fieldsforscan(snum):
                    midpoints.setdefault(int(fid), []).append(t_mid)
            except Exception:
                continue

    return names, coords, midpoints, casa_calls


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


def _pa_spread_deg(
    ra_rad: float,
    dec_rad: float,
    t_mid_mjd_s_list: list[float],
    lat_deg: float,
    lon_deg: float,
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


def _df_poltype_from_source_knowledge(role_at_band: str) -> tuple[str | None, str]:
    """
    Which Df poltype the leakage source's *polarisation knowledge* implies.

    Derived from source knowledge only, never from PA coverage: coverage
    determines how well constrained a Df+QU solve is, not which poltype applies.
    Returns (poltype | None, basis string) — the basis is the checkable reason.
    """
    if role_at_band == "leakage_zero_pol":
        return "Df", (
            f"leakage source has fractional polarisation < {LOW_POL_FRAC_PCT}% at the "
            "observed band (effective_role_at_band='leakage_zero_pol'), so its Q and U "
            "are known to be ~0 and need not be solved for"
        )
    if role_at_band in ("angle_known_pol", "known_pol"):
        return "Df", (
            "leakage source has a catalogued polarisation at the observed band "
            f"(effective_role_at_band='{role_at_band}'), so Q and U come from the "
            "model rather than the solve"
        )
    if role_at_band == "unknown":
        return "Df+QU", (
            "leakage source polarisation is not known at the observed band "
            "(effective_role_at_band='unknown'), so Q and U must be solved jointly "
            "with the D-terms; see pa_spread_deg for how well constrained that is"
        )
    return None, f"unrecognised effective_role_at_band='{role_at_band}'"


# ---------------------------------------------------------------------------
# Main tool entry point
# ---------------------------------------------------------------------------


def run(ms_path: str) -> dict:
    """
    Measure the polarisation-calibration conditions of this dataset.

    Inputs:
        ms_path: Path to the Measurement Set.

    Returns:
        Standard response envelope with data fields:
          band_centre_ghz, pol_angle_calibrator, leakage_calibrator,
          leakage_cal_candidates, recommended_df_poltype,
          recommended_df_poltype_basis, pa_spread_reference_levels,
          pol_cal_data_epoch, pol_cal_data_source.
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

    # --- Fields, coordinates, scan midpoints, array centre ---
    field_names, field_coords, scan_midpoints, fld_calls = _read_fields(ms_str)
    casa_calls.extend(fld_calls)
    field_ids = list(range(len(field_names)))

    lat, lon, _height, arr_calls = _read_array_centre(ms_str)
    casa_calls.extend(arr_calls)

    # --- Match fields against pol calibrator catalogue ---
    angle_cal_entry: PolCalEntry | None = None
    angle_cal_name: str | None = None
    angle_cal_field_id: int | None = None

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
            angle_cal_field_id = fid
        # A *dedicated* leakage calibrator only: role carries "leakage" and not
        # "angle". Every Category A angle standard also lists "leakage", so
        # accepting the first role match promotes 3C286 the moment it precedes
        # the real leakage cal in field order, which is what happened on
        # 3c391_ctm_mosaic (3C286 at field 0, 3C84 at field 9).
        if "leakage" in entry.role and "angle" not in entry.role and leakage_cal_entry is None:
            leakage_cal_entry = entry
            leakage_cal_field_id = fid
            leakage_cal_name = fname

    # Do NOT auto-promote the angle cal to leakage cal even if its catalogue
    # role includes "leakage". 3C286 and 3C138 are bookend-observed (1-2 scans)
    # so their PA spread is almost always thin. The reported leakage calibrator
    # is the separately identified source; every field's PA spread is enumerated
    # in leakage_cal_candidates regardless, for the skill to choose from.
    # The angle cal remains a legitimate Df source through its known model —
    # that is the skill's call from 09-polcal-execution.md, not a default here.

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
                if angle_cal_entry is None and any("POL_ANGLE" in i for i in intents):
                    # Field has CALIBRATE_POL_ANGLE intent — treat as angle cal.
                    # Look up in the pol catalogue using a substring match on the name.
                    entry = lookup_pol(fname)
                    if entry is not None and "angle" in entry.role:
                        angle_cal_entry = entry
                        angle_cal_name = fname
                        angle_cal_field_id = fid
                    else:
                        warnings.append(
                            f"Field '{fname}' has CALIBRATE_POL_ANGLE intent but is not in "
                            "the pol calibrator catalogue — pol angle properties unavailable."
                        )
                if leakage_cal_field_id is None and any("POL_LEAKAGE" in i for i in intents):
                    # Field has CALIBRATE_POL_LEAKAGE intent — use as leakage cal.
                    # leakage_cal_entry stays None (no catalogue properties), but
                    # PA spread is still computed from scan times.
                    leakage_cal_field_id = fid
                    leakage_cal_name = fname
                    warnings.append(
                        f"Leakage calibrator '{fname}' identified from CALIBRATE_POL_LEAKAGE "
                        "intent; not in catalogue — using PA spread from scan times only."
                    )

    # --- Pol properties of the angle calibrator at the observed frequency ---
    angle_frac_field = field(None, flag="UNAVAILABLE")
    angle_pa_field = field(None, flag="UNAVAILABLE")
    angle_stable_pa = False
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
                note=(
                    f"Frequency {band_ghz:.2f} GHz out of tabulated range for "
                    f"{angle_cal_entry.b1950_name}"
                ),
            )

        angle_stable_pa = angle_cal_entry.stable_pa
        if angle_cal_entry.variability_note:
            variability_warn = angle_cal_entry.variability_note
            warnings.append(
                f"Pol angle calibrator {angle_cal_entry.b1950_name}: "
                f"{angle_cal_entry.variability_note}"
            )

    # --- PA spread for every field, ranked, changing nothing ---
    # Enumerated unconditionally: the identified leakage cal is one row among
    # them, marked as such. Selecting a leakage calibrator is the skill's call.
    candidates: list[dict] = []
    for fid, fname in zip(field_ids, field_names, strict=False):
        t_mids = scan_midpoints.get(fid, [])
        ra_c, dec_c = field_coords[fid] if fid < len(field_coords) else (float("nan"), float("nan"))
        spread_c: float | None = None
        if t_mids and not (math.isnan(ra_c) or math.isnan(dec_c)):
            try:
                spread_c = _pa_spread_deg(ra_c, dec_c, t_mids, lat, lon)
            except Exception as e:
                warnings.append(f"PA spread computation failed for field '{fname}': {e}")
        cat_entry = lookup_pol(fname)
        candidates.append(
            {
                "field_id": fid,
                "name": fname,
                "pa_spread_deg": round(spread_c, 2) if spread_c is not None else None,
                "n_scans": len(t_mids),
                "effective_role_at_band": effective_role_at_band(cat_entry, band_ghz),
                "in_pol_catalogue": cat_entry is not None,
                "is_identified_leakage_cal": fid == leakage_cal_field_id,
                "is_identified_angle_cal": fid == angle_cal_field_id,
            }
        )
    # Rank by PA spread, most coverage first; fields with no computable spread last.
    candidates.sort(key=lambda c: (c["pa_spread_deg"] is None, -(c["pa_spread_deg"] or 0.0)))

    # --- The identified leakage calibrator's own numbers ---
    leakage_row = next((c for c in candidates if c["is_identified_leakage_cal"]), None)
    pa_spread_val = leakage_row["pa_spread_deg"] if leakage_row else None
    n_cal_scans = leakage_row["n_scans"] if leakage_row else 0

    leakage_role_at_band = effective_role_at_band(leakage_cal_entry, band_ghz)

    if pa_spread_val is not None:
        pa_spread_field = field(pa_spread_val, flag="COMPLETE")
    else:
        pa_spread_field = field(
            None,
            flag="UNAVAILABLE",
            note=(
                "No leakage calibrator identified, fewer than two scans on it, "
                "or its coordinates are invalid"
            ),
        )

    # --- Df poltype, from source knowledge only ---
    if leakage_cal_field_id is None:
        recommended_df_poltype = None
        recommended_df_poltype_basis = (
            "no leakage calibrator identified from the catalogue or from "
            "CALIBRATE_POL_LEAKAGE intents; see leakage_cal_candidates"
        )
    else:
        recommended_df_poltype, recommended_df_poltype_basis = _df_poltype_from_source_knowledge(
            leakage_role_at_band
        )

    data = {
        "band_centre_ghz": band_ghz_field,
        "pol_angle_calibrator": {
            "available": angle_cal_entry is not None,
            "field_id": angle_cal_field_id,
            "field_name": angle_cal_name,
            "source": angle_cal_entry.b1950_name if angle_cal_entry else None,
            "j2000": angle_cal_entry.j2000_name if angle_cal_entry else None,
            "category": angle_cal_entry.category if angle_cal_entry else None,
            "frac_pol_pct": angle_frac_field,
            "pol_angle_deg": angle_pa_field,
            "stable_pa": angle_stable_pa,
            "variability_warning": variability_warn,
        },
        "leakage_calibrator": {
            "available": leakage_cal_field_id is not None,
            "field_id": leakage_cal_field_id,
            "source": leakage_cal_name,
            "category": leakage_cal_entry.category if leakage_cal_entry else None,
            "effective_role_at_band": leakage_role_at_band,
            "low_pol_frac_pct": LOW_POL_FRAC_PCT,
            "pa_spread_deg": pa_spread_field,
            "pa_spread_note": (
                "Delta computed via astropy sky-frame PA; "
                "CASA feed-frame differs by -90 deg for ALT-AZ mounts "
                "but delta is identical in both conventions. "
                "PA spread bears on the Df+QU (unknown-pol) path only; for a "
                "zero-pol or known-pol source Q and U come from the model."
            ),
            "n_calibrator_scans": n_cal_scans,
        },
        "leakage_cal_candidates": candidates,
        "recommended_df_poltype": recommended_df_poltype,
        "recommended_df_poltype_basis": recommended_df_poltype_basis,
        "pa_spread_reference_levels": {
            "nrao_recommended_deg": PA_SPREAD_NRAO_RECOMMENDED_DEG,
            "practical_floor_deg": PA_SPREAD_PRACTICAL_FLOOR_DEG,
            "applies_to": "Df+QU (unknown-pol) leakage solve only",
            "provenance": PA_SPREAD_PROVENANCE,
        },
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
