"""
tools/geometry.py — ms_elevation_vs_time and ms_parallactic_angle_vs_time

Layer 2, Tools 3 & 4.

Uses astropy for all coordinate computations (not CASA measures daemon).

PA convention (DESIGN.md §6.4):
  pa_sky_deg  = astropy sky-frame PA (North through East)
  pa_feed_deg = feed-frame PA = pa_sky - 90° for ALT-AZ mounts (CASA convention)

  Validation status: PENDING until cross-checked against CASA measures output
  for a known reference observation.

IMPORTANT: validation_status='PENDING' is returned in all PA output until
the cross-validation test case (ref VLA observation) confirms the offset.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from ms_inspect.util.casa_context import open_msmd, open_table, validate_ms_path
from ms_inspect.util.conversions import ecef_to_geodetic, mjd_seconds_to_unix, rad_to_deg
from ms_inspect.util.formatting import field, response_envelope

TOOL_EL  = "ms_elevation_vs_time"
TOOL_PA  = "ms_parallactic_angle_vs_time"

# Default low-elevation warning threshold
DEFAULT_EL_THRESHOLD_DEG = 20.0

# Feed-frame PA offset from sky-frame PA, by mount type
# ALT-AZ: PA_feed = PA_sky - 90°
# Equatorial: PA_feed = PA_sky (feed rotates mechanically, PA_feed is constant)
_MOUNT_PA_OFFSET_DEG: dict[str, float] = {
    "ALT-AZ":     -90.0,
    "alt-az":     -90.0,
    "ALTAZ":      -90.0,
    "X-Y":         0.0,   # rare mount type
    "EQUATORIAL":  0.0,
    "equatorial":  0.0,
    "SPACE":       0.0,
}


def _require_astropy() -> Any:
    try:
        import astropy  # noqa: F401
        return astropy
    except ImportError:
        raise RuntimeError(
            "astropy is required for elevation and parallactic angle computation. "
            "Install with: pip install astropy>=6.0"
        )


def _read_field_coords(ms_str: str) -> tuple[list[str], list[tuple[float, float]], list[str]]:
    """
    Read field names and J2000 phase centres from msmetadata.
    Returns (names, [(ra_rad, dec_rad), ...], casa_calls).
    """
    from ms_inspect.util.casa_context import open_msmd
    import math as _math

    names: list[str] = []
    coords: list[tuple[float, float]] = []
    casa_calls = ["msmd.fieldnames()", "msmd.phasecenter(field_id)"]

    with open_msmd(ms_str) as msmd:
        field_names = list(msmd.fieldnames())
        for fid in range(len(field_names)):
            try:
                pc = msmd.phasecenter(fid)
                ra  = float(pc["m0"]["value"]) % (2 * _math.pi)
                dec = float(pc["m1"]["value"])
                coords.append((ra, dec))
            except Exception:
                coords.append((float("nan"), float("nan")))
        names = field_names

    return names, coords, casa_calls


def _read_scan_times_and_fields(ms_str: str) -> tuple[list[dict], list[str]]:
    """
    Read per-scan time midpoints and field IDs from msmetadata.
    Returns (scan_records, casa_calls).
    Each record: {scan_num, field_id, t_mid_mjd_s, duration_s}
    """
    casa_calls = ["msmd.scannumbers()", "msmd.timesforscans()", "msmd.fieldsforscan()"]
    records: list[dict] = []

    with open_msmd(ms_str) as msmd:
        scan_nums = sorted(msmd.scannumbers())
        for snum in scan_nums:
            try:
                times = msmd.timesforscans([snum])
                t_start = float(min(times))
                t_end   = float(max(times))
                fids = list(msmd.fieldsforscan(snum))
                fid = fids[0] if fids else 0
                records.append({
                    "scan_num": snum,
                    "field_id": fid,
                    "t_start_mjd_s": t_start,
                    "t_end_mjd_s":   t_end,
                    "t_mid_mjd_s":   (t_start + t_end) / 2.0,
                })
            except Exception:
                continue

    return records, casa_calls


def _read_array_centre(ms_str: str) -> tuple[float, float, float, list[str]]:
    """
    Compute array geodetic centre from mean ECEF positions.
    Returns (lat_deg, lon_deg, height_m, casa_calls).
    """
    casa_calls = [f"tb.open('{ms_str}/ANTENNA')", "tb.getcol(POSITION)"]
    with open_table(ms_str + "/ANTENNA") as tb:
        positions = tb.getcol("POSITION")  # [3, n_ant]

    mean_x = float(positions[0].mean())
    mean_y = float(positions[1].mean())
    mean_z = float(positions[2].mean())

    lat, lon, height = ecef_to_geodetic(mean_x, mean_y, mean_z)
    return lat, lon, height, casa_calls


def _read_mount_types(ms_str: str) -> list[str]:
    """Read MOUNT column from ANTENNA subtable."""
    try:
        with open_table(ms_str + "/ANTENNA") as tb:
            return list(tb.getcol("MOUNT"))
    except Exception:
        return []


def _compute_el_pa(
    ra_rad: float,
    dec_rad: float,
    t_unix: float,
    lat_deg: float,
    lon_deg: float,
    height_m: float,
) -> tuple[float, float]:
    """
    Compute elevation (deg) and sky-frame parallactic angle (deg) for a source
    at (ra_rad, dec_rad) as seen from (lat_deg, lon_deg, height_m) at Unix time t_unix.

    Returns (elevation_deg, pa_sky_deg).

    Uses astropy AltAz frame.
    """
    from astropy.coordinates import AltAz, EarthLocation, SkyCoord
    from astropy.time import Time
    import astropy.units as u

    location = EarthLocation(lat=lat_deg * u.deg, lon=lon_deg * u.deg, height=height_m * u.m)
    t = Time(t_unix, format="unix", scale="utc")
    frame = AltAz(obstime=t, location=location)

    coord = SkyCoord(ra=ra_rad * u.rad, dec=dec_rad * u.rad, frame="icrs")
    altaz = coord.transform_to(frame)

    el_deg = float(altaz.alt.deg)

    # Parallactic angle (sky-frame: North through East)
    # astropy's position_angle gives PA of the direction to NCP
    # from the source position, measured North through East.
    from astropy.coordinates import ICRS
    import astropy.units as u

    # Compute PA using the standard formula via hour angle
    lat_rad = math.radians(lat_deg)
    dec_rad_local = dec_rad
    ha_rad = float(t.sidereal_time("apparent", lon_deg * u.deg).rad) - ra_rad

    pa_sky = math.degrees(math.atan2(
        math.cos(lat_rad) * math.sin(ha_rad),
        math.sin(lat_rad) * math.cos(dec_rad_local)
        - math.cos(lat_rad) * math.sin(dec_rad_local) * math.cos(ha_rad)
    ))

    return el_deg, pa_sky


def run_elevation_vs_time(ms_path: str, threshold_deg: float = DEFAULT_EL_THRESHOLD_DEG) -> dict:
    """
    Return per-scan elevation statistics for each field.

    Uses astropy for elevation computation (not CASA measures daemon).
    Warns on scans where elevation drops below threshold_deg.
    """
    _require_astropy()
    p = validate_ms_path(ms_path)
    ms_str = str(p)
    casa_calls: list[str] = []
    warnings:   list[str] = []

    field_names, field_coords, fc_calls = _read_field_coords(ms_str)
    scan_records, sc_calls              = _read_scan_times_and_fields(ms_str)
    lat, lon, height, arr_calls         = _read_array_centre(ms_str)
    casa_calls.extend(fc_calls + sc_calls + arr_calls)

    # Group scans by field
    from collections import defaultdict
    field_scans: dict[int, list[dict]] = defaultdict(list)
    for rec in scan_records:
        field_scans[rec["field_id"]].append(rec)

    fields_out: list[dict] = []

    for fid, fname in enumerate(field_names):
        ra_rad, dec_rad = field_coords[fid] if fid < len(field_coords) else (float("nan"), float("nan"))

        # Skip suspect coordinates
        if math.isnan(ra_rad) or math.isnan(dec_rad):
            fields_out.append({
                "field_id":   fid,
                "field_name": fname,
                "scans":      field(None, flag="UNAVAILABLE",
                                    note="Cannot compute elevation — field coordinates are invalid"),
            })
            continue

        scans_out: list[dict] = []
        for rec in field_scans.get(fid, []):
            t_mid  = mjd_seconds_to_unix(rec["t_mid_mjd_s"])
            t_start = mjd_seconds_to_unix(rec["t_start_mjd_s"])
            t_end  = mjd_seconds_to_unix(rec["t_end_mjd_s"])

            try:
                el_mid, _   = _compute_el_pa(ra_rad, dec_rad, t_mid,   lat, lon, height)
                el_start, _ = _compute_el_pa(ra_rad, dec_rad, t_start, lat, lon, height)
                el_end, _   = _compute_el_pa(ra_rad, dec_rad, t_end,   lat, lon, height)
            except Exception as e:
                warnings.append(f"Elevation computation failed for scan {rec['scan_num']}: {e}")
                continue

            el_min = min(el_start, el_mid, el_end)
            below  = el_min < threshold_deg

            if below:
                warnings.append(
                    f"Field '{fname}', scan {rec['scan_num']}: "
                    f"minimum elevation {el_min:.1f}° is below threshold {threshold_deg:.0f}°."
                )

            scans_out.append({
                "scan_number":     rec["scan_num"],
                "el_start_deg":    field(round(el_start, 2)),
                "el_mid_deg":      field(round(el_mid, 2)),
                "el_end_deg":      field(round(el_end, 2)),
                "el_min_deg":      field(round(el_min, 2)),
                "below_threshold": below,
            })

        fields_out.append({
            "field_id":      fid,
            "field_name":    fname,
            "threshold_deg": threshold_deg,
            "scans":         field(scans_out, flag="COMPLETE"),
        })

    data = {
        "array_lat_deg": round(lat, 6),
        "array_lon_deg": round(lon, 6),
        "computation":   "astropy AltAz frame",
        "threshold_deg": threshold_deg,
        "fields":        fields_out,
    }

    return response_envelope(
        tool_name=TOOL_EL,
        ms_path=ms_path,
        data=data,
        warnings=warnings,
        casa_calls=casa_calls,
    )


def run_parallactic_angle_vs_time(ms_path: str) -> dict:
    """
    Return per-field parallactic angle range — sky-frame and feed-frame.

    Both pa_sky and pa_feed are returned (see DESIGN.md §6.4 for convention).
    pa_feed = pa_sky - 90° for ALT-AZ mounts.

    validation_status: PENDING — cross-check against CASA measures required
    before using for D-term solutions.
    """
    _require_astropy()
    p = validate_ms_path(ms_path)
    ms_str = str(p)
    casa_calls: list[str] = []
    warnings:   list[str] = []

    field_names, field_coords, fc_calls = _read_field_coords(ms_str)
    scan_records, sc_calls              = _read_scan_times_and_fields(ms_str)
    lat, lon, height, arr_calls         = _read_array_centre(ms_str)
    mount_types                         = _read_mount_types(ms_str)
    casa_calls.extend(fc_calls + sc_calls + arr_calls)
    casa_calls.append("tb.getcol(MOUNT) from ANTENNA subtable")

    # Determine dominant mount type
    primary_mount = mount_types[0].upper().strip() if mount_types else "ALT-AZ"
    pa_offset_deg = _MOUNT_PA_OFFSET_DEG.get(primary_mount, -90.0)

    equatorial_mount = pa_offset_deg == 0.0
    if equatorial_mount:
        warnings.append(
            f"Equatorial mount detected ({primary_mount}). "
            "Parallactic angle is constant during observation — "
            "feed rotation is handled mechanically. "
            "The D-term parallactic angle coverage criterion does not apply."
        )

    # Group scans by field
    from collections import defaultdict
    field_scans: dict[int, list[dict]] = defaultdict(list)
    for rec in scan_records:
        field_scans[rec["field_id"]].append(rec)

    fields_out: list[dict] = []

    for fid, fname in enumerate(field_names):
        ra_rad, dec_rad = field_coords[fid] if fid < len(field_coords) else (float("nan"), float("nan"))

        if math.isnan(ra_rad) or math.isnan(dec_rad):
            fields_out.append({
                "field_id":   fid,
                "field_name": fname,
                "pa_sky":     field(None, flag="UNAVAILABLE",
                                    note="Field coordinates invalid — cannot compute PA"),
            })
            continue

        pa_sky_values: list[float] = []
        for rec in field_scans.get(fid, []):
            for t_unix in [
                mjd_seconds_to_unix(rec["t_start_mjd_s"]),
                mjd_seconds_to_unix(rec["t_mid_mjd_s"]),
                mjd_seconds_to_unix(rec["t_end_mjd_s"]),
            ]:
                try:
                    _, pa_sky = _compute_el_pa(ra_rad, dec_rad, t_unix, lat, lon, height)
                    pa_sky_values.append(pa_sky)
                except Exception:
                    continue

        if not pa_sky_values:
            fields_out.append({
                "field_id":   fid,
                "field_name": fname,
                "pa_sky":     field(None, flag="UNAVAILABLE",
                                    note="PA computation failed for all scans"),
            })
            continue

        pa_sky_start = pa_sky_values[0]
        pa_sky_end   = pa_sky_values[-1]
        pa_sky_range = max(pa_sky_values) - min(pa_sky_values)

        pa_feed_start = pa_sky_start + pa_offset_deg
        pa_feed_end   = pa_sky_end   + pa_offset_deg
        pa_feed_range = pa_sky_range  # range is identical, only zero-point shifts

        fields_out.append({
            "field_id":            fid,
            "field_name":          fname,
            "mount_type":          primary_mount,
            "pa_sky_start_deg":    field(round(pa_sky_start, 2), flag="COMPLETE",
                                          note="astropy sky-frame PA, North through East"),
            "pa_sky_end_deg":      field(round(pa_sky_end, 2),   flag="COMPLETE"),
            "pa_sky_range_deg":    field(round(pa_sky_range, 2), flag="COMPLETE"),
            "pa_feed_start_deg":   field(round(pa_feed_start, 2), flag="COMPLETE",
                                          note=f"Feed-frame PA = PA_sky + ({pa_offset_deg}°), "
                                               f"CASA convention for {primary_mount} mount"),
            "pa_feed_end_deg":     field(round(pa_feed_end, 2),   flag="COMPLETE"),
            "pa_feed_range_deg":   field(round(pa_feed_range, 2), flag="COMPLETE"),
            "convention_offset_deg": pa_offset_deg,
            "convention_note": (
                f"PA_feed = PA_sky + {pa_offset_deg}° "
                f"({primary_mount} mount, CASA convention). "
                "Pending cross-validation against casatools.measures."
            ),
            "validation_status":   "PENDING",
        })

    data = {
        "array_lat_deg":     round(lat, 6),
        "computation":       "astropy LST + atan2 formula",
        "pa_convention_note": (
            "PA_sky: angle to NCP, North through East (astropy convention). "
            "PA_feed: feed-frame PA = PA_sky + offset for mount type. "
            "VALIDATION STATUS: PENDING — cross-check against casatools.measures "
            "required before use in D-term calibration."
        ),
        "fields": fields_out,
    }

    return response_envelope(
        tool_name=TOOL_PA,
        ms_path=ms_path,
        data=data,
        warnings=warnings,
        casa_calls=casa_calls,
    )
