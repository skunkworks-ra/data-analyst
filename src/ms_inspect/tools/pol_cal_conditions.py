"""
tools/pol_cal_conditions.py — ms_pol_cal_conditions

Layer 2, Tool 7 (Phase 1 extension).

Answers a single question: what are the measured polarisation-calibration
conditions for this dataset?

It reports conditions. It does NOT decide whether to proceed. Whether 24 degrees
of parallactic coverage is enough depends on the science goal and the risk
tolerance for this observation, which only the Skill knows. See DESIGN.md 1.1.1
and skill 09-polcal-execution.md.

This tool previously returned `verdict`, `blocker`, `xf_feasible`, `df_feasible`,
`meets_threshold`, and `single_scan_sufficient`, and it silently reassigned the
leakage calibrator to a different field when a threshold test failed. Those are
gone. The reason is recorded because it is easy to reintroduce: a NOT_FEASIBLE on
D-terms, believed, means the polarization is never imaged, and nothing in the
output records what was forgone. The threshold behind it was one constant chosen
for a typical case. The continuous measurement supports a better answer than the
boolean did: "24 degrees against a 45 degree reference, proceed but limit
fractional-polarization claims to the few percent level" cannot be expressed as
GO or NO-GO.

Algorithm:
1. Opens FIELD subtable → field names.
2. Opens SPECTRAL_WINDOW subtable → band centre frequency (median of first SPW).
3. Matches each field against util/pol_calibrators.py (pure Python, no CASA).
4. For each matching pol cal field: computes sky-frame PA at scan midpoints
   using astropy (same formula as geometry.py) and returns Δ(max−min).
5. Interpolates pol properties at observed frequency from 2019 epoch table.
6. Returns the measurements, the catalogue facts, the reference thresholds as
   labelled constants, and every candidate leakage field ranked by PA spread.

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

TOOL_NAME = "ms_pol_cal_conditions"

# Reference value, not a test. Returned as a labelled constant with provenance so
# the Skill can compare the measured PA spread against it and decide.
#
# Relevant ONLY to the Df+QU (unknown-pol) leakage path, where D-term and source
# Q,U must be separated from each other and parallactic rotation is what breaks
# the degeneracy. Irrelevant to Xf, and to known-pol or zero-pol Df, which need
# no PA coverage at all.
#
# NRAO recommends >=60 degrees. That is conservative: a Df+QU solve can work with
# as little as ~30 degrees on a bright source, below which the separation becomes
# degenerate. Both numbers are returned; neither is applied here.
PA_SPREAD_REFERENCE_DEG = 60.0
PA_SPREAD_PRACTICAL_FLOOR_DEG = 30.0
PA_SPREAD_REFERENCE_SOURCE = (
    "NRAO VLA polarimetry guide (>=60 deg recommended); ~30 deg practical "
    "degeneracy floor for Df+QU on a bright source"
)

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


# Polarisation calibrators are "low polarization" — and thus usable as zero-pol
# leakage calibrators where a single scan suffices — only where their fractional
# polarization is below this level. NRAO VLA polarisation guide wording:
# 3C84 "low polarization (<1%)"; 3C147 "low polarization below 10 GHz".
LOW_POL_FRAC_PCT = 1.0


def _effective_role_at_band(
    entry: PolCalEntry | None,
    band_ghz: float,
    epoch: str = POL_DATA_EPOCH,
) -> str:
    """Effective polcal role of a source AT the observing band (frequency-dependent).

    A source's role is not fixed: it depends on its polarization where you observe.
      'leakage_zero_pol' — frac_pol < 1% (NRAO low-pol): usable as a zero-pol
                           leakage cal, single scan suffices for Df.
      'angle_known_pol'  — frac_pol >= 1% with a defined PA: it has crossed into
                           the angle-calibrator regime (known polarization); not a
                           zero-pol leakage cal here. e.g. 3C147/3C84 above ~10 GHz.
      'known_pol'        — polarized with frac known but PA undefined at this band.
      'unknown'          — not in the catalogue or out of the tabulated range.
    """
    if entry is None or math.isnan(band_ghz):
        return "unknown"
    props = pol_properties_at_freq(entry, band_ghz, epoch=epoch)
    if props is None or props.frac_pol_pct is None:
        return "unknown"
    if props.frac_pol_upper_limit or props.frac_pol_pct < LOW_POL_FRAC_PCT:
        return "leakage_zero_pol"
    if props.pol_angle_deg is not None:
        return "angle_known_pol"
    return "known_pol"


# ---------------------------------------------------------------------------
# Main tool entry point
# ---------------------------------------------------------------------------


def run(ms_path: str) -> dict:
    """
    Measure the polarisation-calibration conditions for this dataset.

    Reports conditions; does not decide whether to proceed. There is deliberately
    no threshold argument: the reference values are returned as labelled
    constants for the Skill to compare against, and a tunable threshold argument
    invites the caller to push a decision back into this tool.

    Inputs:
        ms_path: Path to the Measurement Set.

    Returns:
        Standard response envelope with data fields:
          band_centre_ghz, pol_angle_calibrator, leakage_calibrator (including
          pa_spread_deg, n_calibrator_scans, effective_role_at_band, and
          leakage_cal_candidates ranked by PA spread), recommended_df_poltype
          with recommended_df_poltype_basis, pa_spread_reference_deg,
          pa_spread_practical_floor_deg, pa_spread_reference_source,
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
                if angle_cal_entry is None and any("POL_ANGLE" in i for i in intents):
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

    # --- Pol properties at observed frequency ---
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
                note=f"Frequency {band_ghz:.2f} GHz out of tabulated range for {angle_cal_entry.b1950_name}",
            )

        angle_stable_pa = angle_cal_entry.stable_pa
        if angle_cal_entry.variability_note:
            variability_warn = angle_cal_entry.variability_note
            warnings.append(
                f"Pol angle calibrator {angle_cal_entry.b1950_name}: "
                f"{angle_cal_entry.variability_note}"
            )

    # --- PA spread for leakage calibrator ---
    pa_spread_val: float | None = None
    n_cal_scans: int = 0

    leakage_source_name = leakage_cal_name or angle_cal_name  # fallback
    leakage_source_entry = leakage_cal_entry or angle_cal_entry

    # Effective role of the leakage source AT the observing band — this, not the
    # static catalogue role, decides whether a single scan suffices. 3C147/3C84
    # are zero-pol leakage cals only where frac_pol < 1% (below ~10 GHz); above
    # that they become polarized (angle-cal regime) and need PA coverage / a model.
    leakage_role_at_band = _effective_role_at_band(leakage_source_entry, band_ghz)

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

    # --- Every other field as a candidate leakage calibrator, ranked ---
    # Enumerated unconditionally. Previously this ran only when the primary
    # calibrator failed a threshold test, which meant the Skill could not see the
    # alternatives in the ordinary case and could not second-guess the primary.
    leakage_cal_candidates: list[dict] = []
    if leakage_cal_field_id is not None:
        angle_cal_field_id = next(
            (fid for fid, fn in zip(field_ids, field_names, strict=False) if fn == angle_cal_name),
            None,
        )
        skip_ids: set[int] = {leakage_cal_field_id}
        if angle_cal_field_id is not None:
            skip_ids.add(angle_cal_field_id)

        for fid, fname in zip(field_ids, field_names, strict=False):
            if fid in skip_ids:
                continue
            ra_c, dec_c = (
                field_coords[fid] if fid < len(field_coords) else (float("nan"), float("nan"))
            )
            if math.isnan(ra_c) or math.isnan(dec_c):
                continue
            try:
                t_mids_c, sc_calls_c = _scan_times_for_field(ms_str, fid)
                casa_calls.extend(sc_calls_c)
                if len(t_mids_c) < 2:
                    continue
                spread_c = _pa_spread_deg(ra_c, dec_c, t_mids_c, lat, lon, height)
                leakage_cal_candidates.append(
                    {
                        "field_id": fid,
                        "name": fname,
                        "pa_spread_deg": round(spread_c, 2),
                        "n_scans": len(t_mids_c),
                    }
                )
            except Exception as e:
                warnings.append(f"PA spread computation failed for candidate field '{fname}': {e}")

        # Ranked by PA spread, descending. A ranking with its per-item inputs is
        # permitted; acting on it is not. The identified leakage calibrator is
        # NOT reassigned here even when a candidate has more coverage, because
        # choosing a different calibrator is a calibration-strategy decision that
        # depends on the source's brightness, its polarization, and the science
        # goal. The Skill picks from this list. Previously this block silently
        # swapped the calibrator and recorded it only in `warnings`.
        leakage_cal_candidates.sort(key=lambda x: x["pa_spread_deg"], reverse=True)

    # --- Effective role of the leakage source at the observing band ---
    leakage_role_at_band = _effective_role_at_band(leakage_source_entry, band_ghz)

    has_angle_cal = angle_cal_entry is not None

    # --- Which Df poltype the source knowledge implies -------------------------
    # A derived label, permitted under DESIGN.md 1.1.1 because it ships the inputs
    # that produced it (see recommended_df_poltype_basis) and is falsifiable from
    # the output alone.
    #
    # Deliberately NOT coupled to the PA spread. The earlier version computed
    # `(not df_known_pol) and meets_threshold`, which made the poltype depend on a
    # threshold test and was the part that behaved like a gate: below the
    # threshold it returned None, i.e. "no strategy available", when the honest
    # answer is "Df+QU, and here is how well constrained it will be".
    #
    # Coverage determines the QUALITY of a Df+QU solve, not WHICH poltype applies.
    # The poltype follows from what is known about the source, which is what
    # 09-polcal-execution.md Step B says. Judge the quality in Step C from
    # pa_spread_deg against the returned reference constants.
    has_low_pol_source = leakage_role_at_band == "leakage_zero_pol"
    df_known_pol = has_angle_cal or has_low_pol_source
    if leakage_cal_field_id is None and not has_angle_cal:
        # No leakage source and no angle cal to fall back on: nothing to solve.
        recommended_df_poltype: str | None = None
        df_basis = "no leakage calibrator and no angle calibrator identified"
    elif df_known_pol:
        recommended_df_poltype = "Df"
        df_basis = (
            "angle calibrator present (known model)"
            if has_angle_cal
            else f"leakage source is zero-pol at this band (< {LOW_POL_FRAC_PCT}% frac pol)"
        )
    else:
        recommended_df_poltype = "Df+QU"
        df_basis = (
            "leakage source polarization is unknown or non-negligible at this band, "
            "so Q,U must be solved alongside the D-terms; PA coverage constrains how "
            "well, see pa_spread_deg against pa_spread_reference_deg"
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
            "available": leakage_cal_field_id is not None,
            "source": leakage_source_name,
            "category": leakage_source_entry.category if leakage_source_entry else None,
            "effective_role_at_band": leakage_role_at_band,
            "frac_pol_low_reference_pct": LOW_POL_FRAC_PCT,
            "pa_spread_deg": pa_spread_field,
            "pa_spread_note": (
                "Delta computed via astropy sky-frame PA; "
                "CASA feed-frame differs by -90° for ALT-AZ mounts "
                "but delta is identical in both conventions. "
                "PA spread constrains only the Df+QU path, where D-term and source "
                "Q,U must be separated from each other. It is irrelevant to Xf and "
                "to a known-pol or zero-pol Df."
            ),
            "n_calibrator_scans": n_cal_scans,
            "leakage_cal_candidates": leakage_cal_candidates,
        },
        # Which Df poltype the source knowledge implies, with the inputs that
        # produced it. Follows from what is KNOWN about the source, not from PA
        # coverage: see Step B vs Step C in 09-polcal-execution.md.
        "recommended_df_poltype": recommended_df_poltype,
        "recommended_df_poltype_basis": df_basis,
        # Reference values, returned as labelled constants with provenance. This
        # tool does not apply them: compare pa_spread_deg against these in the
        # Skill, where the science goal and risk tolerance are known.
        "pa_spread_reference_deg": PA_SPREAD_REFERENCE_DEG,
        "pa_spread_practical_floor_deg": PA_SPREAD_PRACTICAL_FLOOR_DEG,
        "pa_spread_reference_source": PA_SPREAD_REFERENCE_SOURCE,
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
