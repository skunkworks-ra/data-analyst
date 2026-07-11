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

# Default PA spread threshold for the Df+QU (unknown-pol) leakage path only.
# NRAO recommends ≥60°, but that is conservative: a Df+QU solve can succeed with
# as little as ~30° of parallactic coverage on a bright source. 30° is the
# practical floor; below it the D-term/QU separation becomes degenerate.
# (Irrelevant to Xf and to known-pol / zero-pol Df, which need no PA coverage.)
DEFAULT_PA_SPREAD_THRESHOLD_DEG = 30.0

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
# Verdict logic
# ---------------------------------------------------------------------------


def _compute_verdict(
    has_angle_cal: bool,
    angle_cal_degraded: bool,
    xf_feasible: bool,
    df_feasible: bool,
) -> tuple[str, str | None]:
    """
    Return (verdict_str, blocker_str | None).

    Feasibility model (NRAO VLA polarisation guide):
      - Xf (absolute angle) needs only a Category A pol standard with a known
        EVPA model. It is ALWAYS feasible when such a standard is present —
        parallactic-angle coverage is irrelevant to Xf.
      - Df (leakage) is feasible via any of: a zero-pol primary leakage cal
        (single scan), a known-pol source incl. the angle cal itself (≥2 scans),
        or an unknown-pol source with sufficient PA coverage (Df+QU). PA coverage gates
        ONLY the unknown-pol (Df+QU) path.

    Verdicts:
      FULL         — angle cal present → Xf always feasible, and Df feasible
                     (the angle cal's known model makes it a valid Df source)
      DEGRADED     — angle cal present but flagged variable / in flare
      LEAKAGE_ONLY — no angle cal (no Xf), but a Df-capable leakage cal exists
      NOT_FEASIBLE — neither Xf nor Df feasible
    """
    if has_angle_cal and angle_cal_degraded:
        return "DEGRADED", (
            "Angle calibrator flagged as variable or in active flare — "
            "verify current monitoring data before proceeding. Xf and Df remain "
            "feasible but annotate outputs with the variability warning."
        )

    if has_angle_cal:
        # Xf always feasible on the primary standard; Df feasible on it via its
        # known model even without separate PA coverage.
        return "FULL", None

    if df_feasible:
        return "LEAKAGE_ONLY", (
            "No pol angle calibrator observed — absolute angle (Xf) calibration "
            "not possible. Leakage (D-term) calibration may proceed."
        )

    return "NOT_FEASIBLE", (
        "No usable polarisation calibrator: no Category A angle standard for Xf, "
        "and no Df-capable leakage source (need a zero-pol primary leakage cal, a "
        "known-pol source, or an unknown-pol source with ≥30° parallactic coverage)."
    )


# ---------------------------------------------------------------------------
# Main tool entry point
# ---------------------------------------------------------------------------


def run(ms_path: str, pa_spread_threshold_deg: float = DEFAULT_PA_SPREAD_THRESHOLD_DEG) -> dict:
    """
    Assess VLA polarisation calibration feasibility for this dataset.

    Inputs:
        ms_path:                  Path to the Measurement Set.
        pa_spread_threshold_deg:  Minimum PA spread (deg) needed for D-term
                                  calibration (default 30°; NRAO suggests 60°).

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

        if leakage_role_at_band == "leakage_zero_pol":
            # Low polarization (<1%) at this band — one scan is enough for Df.
            meets_threshold = n_cal_scans >= 1
        else:
            # Polarized or unknown at this band — needs PA coverage for Df+QU.
            meets_threshold = pa_spread_val is not None and pa_spread_val >= pa_spread_threshold_deg

    # --- Fallback: if primary leakage cal fails PA threshold, search other fields ---
    leakage_cal_alternatives: list[dict] = []
    if leakage_cal_field_id is not None and not meets_threshold:
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
                leakage_cal_alternatives.append(
                    {
                        "field_id": fid,
                        "name": fname,
                        "pa_spread_deg": round(spread_c, 2),
                        "n_scans": len(t_mids_c),
                        "meets_threshold": spread_c >= pa_spread_threshold_deg,
                    }
                )
            except Exception as e:
                warnings.append(f"PA spread computation failed for candidate field '{fname}': {e}")

        leakage_cal_alternatives.sort(key=lambda x: x["pa_spread_deg"], reverse=True)

        if leakage_cal_alternatives and leakage_cal_alternatives[0]["meets_threshold"]:
            best = leakage_cal_alternatives[0]
            primary_spread_str = f"{pa_spread_val:.1f}°" if pa_spread_val is not None else "unknown"
            warnings.append(
                f"Primary leakage calibrator '{leakage_cal_name}' has insufficient PA spread "
                f"({primary_spread_str} < {pa_spread_threshold_deg}°). "
                f"Falling back to '{best['name']}' "
                f"(PA spread {best['pa_spread_deg']}°, {best['n_scans']} scans)."
            )
            leakage_cal_field_id = best["field_id"]
            leakage_cal_name = best["name"]
            leakage_cal_entry = None
            leakage_source_name = best["name"]
            leakage_source_entry = None
            pa_spread_val = best["pa_spread_deg"]
            n_cal_scans = best["n_scans"]
            meets_threshold = True

    # --- Effective role of the leakage source at the observing band ---
    # Recompute after the fallback may have reassigned the leakage source.
    leakage_role_at_band = _effective_role_at_band(leakage_source_entry, band_ghz)
    has_low_pol_source = leakage_role_at_band == "leakage_zero_pol"

    # --- Feasibility booleans ---
    has_angle_cal = angle_cal_entry is not None

    # Xf needs only a known-EVPA Category A standard; PA coverage is irrelevant.
    xf_feasible = has_angle_cal

    # Df strategies (NRAO pol guide):
    #   'Df'    — known-pol source: a zero-pol (<1%) leakage cal (single scan),
    #             or the angle cal via its known model (≥2 scans).
    #   'Df+QU' — unknown-pol source with ≥ threshold PA coverage.
    df_known_pol = has_angle_cal or has_low_pol_source
    df_qu_unknown = (not df_known_pol) and meets_threshold
    df_feasible = df_known_pol or df_qu_unknown
    if df_known_pol:
        recommended_df_poltype = "Df"
    elif df_qu_unknown:
        recommended_df_poltype = "Df+QU"
    else:
        recommended_df_poltype = None

    # --- Verdict ---
    verdict, blocker = _compute_verdict(
        has_angle_cal=has_angle_cal,
        angle_cal_degraded=angle_degraded,
        xf_feasible=xf_feasible,
        df_feasible=df_feasible,
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
            "single_scan_sufficient": leakage_role_at_band == "leakage_zero_pol",
            "pa_spread_deg": pa_spread_field,
            "pa_spread_note": (
                "Delta computed via astropy sky-frame PA; "
                "CASA feed-frame differs by -90° for ALT-AZ mounts "
                "but delta is identical in both conventions. "
                "PA spread is irrelevant for a zero-pol (<1%) leakage cal at this "
                "band (effective_role_at_band='leakage_zero_pol'); it gates only the "
                "Df+QU path for polarized/unknown sources."
            ),
            "n_calibrator_scans": n_cal_scans,
            "meets_threshold": meets_threshold,
            "threshold_deg": pa_spread_threshold_deg,
            "leakage_cal_alternatives": leakage_cal_alternatives,
        },
        "xf_feasible": xf_feasible,
        "df_feasible": df_feasible,
        "recommended_df_poltype": recommended_df_poltype,
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
