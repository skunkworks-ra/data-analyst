"""
util/conversions.py — Pure conversion and formatting utilities.

No CASA dependency. All functions are independently testable.

Covers:
- MJD seconds → ISO UTC string
- Hz → human frequency string
- Radians → degrees
- ECEF XYZ → geodetic latitude/longitude
- Correlation type integer codes → string labels
- Frequency → band name (telescope-aware)
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

# ---------------------------------------------------------------------------
# CASA correlation type codes → string labels
# CASA integer encoding: defined in casacore/ms/MeasurementSets/Stokes.h
# Confidence: 99% — these are stable integer assignments in CASA since v4.
# ---------------------------------------------------------------------------
CORR_TYPE_MAP: dict[int, str] = {
    1: "I",
    2: "Q",
    3: "U",
    4: "V",
    5: "RR",
    6: "RL",
    7: "LR",
    8: "LL",
    9: "XX",
    10: "XY",
    11: "YX",
    12: "YY",
    13: "RX",
    14: "RY",
    15: "LX",
    16: "LY",
    17: "XR",
    18: "XL",
    19: "YR",
    20: "YL",
    21: "PP",
    22: "PQ",
    23: "QP",
    24: "QQ",
}

# Polarization basis inferred from correlation product set
_CIRCULAR = {"RR", "RL", "LR", "LL"}
_LINEAR = {"XX", "XY", "YX", "YY"}


def corr_codes_to_labels(codes: list[int]) -> list[str]:
    """Convert a list of CASA correlation type integers to string labels."""
    return [CORR_TYPE_MAP.get(c, f"UNKNOWN({c})") for c in codes]


def polarization_basis(corr_labels: list[str]) -> str:
    """
    Infer polarization basis from correlation labels.

    Returns 'circular', 'linear', 'mixed', or 'stokes'.
    """
    label_set = set(corr_labels)
    if label_set & _CIRCULAR and not label_set & _LINEAR:
        return "circular"
    if label_set & _LINEAR and not label_set & _CIRCULAR:
        return "linear"
    if label_set <= {"I", "Q", "U", "V"}:
        return "stokes"
    return "mixed"


def is_full_stokes(corr_labels: list[str]) -> bool:
    """True if all four Stokes products are present."""
    s = set(corr_labels)
    return bool(
        ({"RR", "RL", "LR", "LL"} <= s)
        or ({"XX", "XY", "YX", "YY"} <= s)
        or ({"I", "Q", "U", "V"} <= s)
    )


# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------

# CASA MJD epoch: Modified Julian Date in seconds (MJD * 86400)
# MJD zero = 1858-11-17 00:00:00 UTC
_MJD_EPOCH_UNIX = -3506716800.0  # unix timestamp of MJD=0


def mjd_seconds_to_utc(mjd_seconds: float) -> str:
    """
    Convert CASA MJD seconds to an ISO 8601 UTC string.

    CASA stores times as MJD * 86400 (seconds since MJD epoch).
    MJD epoch is 1858-11-17 00:00:00 UTC.

    Returns: '2017-03-15 10:23:01.000 UTC'
    """
    unix_ts = mjd_seconds + _MJD_EPOCH_UNIX
    dt = datetime.fromtimestamp(unix_ts, tz=UTC)
    return dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + " UTC"


def mjd_seconds_to_unix(mjd_seconds: float) -> float:
    """Convert CASA MJD seconds to a Unix timestamp (float)."""
    return mjd_seconds + _MJD_EPOCH_UNIX


# ---------------------------------------------------------------------------
# Frequency
# ---------------------------------------------------------------------------


def hz_to_human(freq_hz: float) -> str:
    """Format a frequency in Hz as a human-readable string."""
    if freq_hz >= 1e9:
        return f"{freq_hz / 1e9:.4f} GHz"
    elif freq_hz >= 1e6:
        return f"{freq_hz / 1e6:.4f} MHz"
    elif freq_hz >= 1e3:
        return f"{freq_hz / 1e3:.4f} kHz"
    else:
        return f"{freq_hz:.2f} Hz"


def freq_to_band_name(freq_hz: float, telescope: str) -> str | None:
    """
    Map a centre frequency to a human band name for a given telescope.

    Returns None if the telescope is not recognised — callers should
    flag this as UNAVAILABLE rather than guessing.

    Confidence: ~90% for VLA/MeerKAT standard bands.
    uGMRT band boundaries are approximate (official ranges vary slightly
    by receiver installation status).
    """
    freq_ghz = freq_hz / 1e9
    t = telescope.upper()

    if "VLA" in t or "EVLA" in t or "JVLA" in t:
        if freq_ghz < 0.30:
            return "4-band (<300 MHz)"
        elif freq_ghz < 1.00:
            return "P-band (230–470 MHz)"
        elif freq_ghz < 2.00:
            return "L-band (1–2 GHz)"
        elif freq_ghz < 4.00:
            return "S-band (2–4 GHz)"
        elif freq_ghz < 8.00:
            return "C-band (4–8 GHz)"
        elif freq_ghz < 12.00:
            return "X-band (8–12 GHz)"
        elif freq_ghz < 18.00:
            return "Ku-band (12–18 GHz)"
        elif freq_ghz < 26.50:
            return "K-band (18–26.5 GHz)"
        elif freq_ghz < 40.00:
            return "Ka-band (26.5–40 GHz)"
        else:
            return "Q-band (40–50 GHz)"

    elif "MEERKAT" in t or "MKT" in t:
        if freq_ghz < 0.90:
            return "UHF-band (544–1088 MHz)"
        elif freq_ghz < 1.75:
            return "L-band (856–1712 MHz)"
        elif freq_ghz < 3.50:
            return "S-band (1.75–3.5 GHz)"
        else:
            return f"Unknown MeerKAT band ({freq_ghz:.3f} GHz)"

    elif "GMRT" in t:
        if freq_ghz < 0.25:
            return "Band 1 (120–250 MHz)"
        elif freq_ghz < 0.50:
            return "Band 2 (250–500 MHz)"
        elif freq_ghz < 0.75:
            return "Band 3 (550–750 MHz)"
        elif freq_ghz < 1.05:
            return "Band 4 (700–950 MHz)"
        else:
            return "Band 5 (1050–1450 MHz)"

    return None  # unknown telescope — caller flags as UNAVAILABLE


# ---------------------------------------------------------------------------
# Angles
# ---------------------------------------------------------------------------


def rad_to_deg(radians: float) -> float:
    """Radians to degrees."""
    return math.degrees(radians)


def deg_to_rad(degrees: float) -> float:
    """Degrees to radians."""
    return math.radians(degrees)


def rad_to_hms(radians: float) -> str:
    """
    Convert radians to an HH:MM:SS.ss right-ascension string.
    Input is assumed to be in [0, 2π).
    """
    total_hours = math.degrees(radians) / 15.0
    h = int(total_hours)
    m = int((total_hours - h) * 60)
    s = ((total_hours - h) * 60 - m) * 60
    return f"{h:02d}h{m:02d}m{s:05.2f}s"


def rad_to_dms(radians: float) -> str:
    """
    Convert radians to a ±DD:MM:SS.s declination string.

    Uses total-arcseconds rounding to avoid floating-point boundary issues
    (e.g. -29d59m60.0s instead of -30d00m00.0s).
    """
    deg_total = math.degrees(radians)
    sign = "+" if deg_total >= 0 else "-"
    total_arcsec = round(abs(deg_total) * 3600.0, 1)
    d = int(total_arcsec // 3600)
    m = int((total_arcsec % 3600) // 60)
    s = total_arcsec % 60
    return f"{sign}{d:02d}d{m:02d}m{s:04.1f}s"


# ---------------------------------------------------------------------------
# Coordinate conversions
# ---------------------------------------------------------------------------


def ecef_to_geodetic(x: float, y: float, z: float) -> tuple[float, float, float]:
    """
    Convert ECEF (Earth-Centred Earth-Fixed) XYZ coordinates in metres
    to geodetic latitude (deg), longitude (deg), height (m) above WGS84 ellipsoid.

    Uses the Bowring iterative method (converges in 2–3 iterations).
    Accuracy: better than 1 mm for all Earth-surface points.

    Reference: Bowring (1985), 'The geodetic line', Survey Review.
    """
    # WGS84 constants
    a = 6_378_137.0  # semi-major axis, m
    f = 1.0 / 298.257_223_563
    b = a * (1.0 - f)  # semi-minor axis
    e2 = 1.0 - (b / a) ** 2  # first eccentricity squared
    ep2 = (a / b) ** 2 - 1.0  # second eccentricity squared

    lon_rad = math.atan2(y, x)
    p = math.sqrt(x * x + y * y)

    # Initial estimate
    theta = math.atan2(z * a, p * b)
    lat_rad = math.atan2(
        z + ep2 * b * math.sin(theta) ** 3,
        p - e2 * a * math.cos(theta) ** 3,
    )

    # Two Bowring iterations
    for _ in range(3):
        sin_lat = math.sin(lat_rad)
        N = a / math.sqrt(1.0 - e2 * sin_lat * sin_lat)
        lat_rad = math.atan2(z + e2 * N * sin_lat, p)

    sin_lat = math.sin(lat_rad)
    cos_lat = math.cos(lat_rad)
    N = a / math.sqrt(1.0 - e2 * sin_lat * sin_lat)
    h = p / cos_lat - N if abs(cos_lat) > 1e-10 else abs(z) / sin_lat - N * (1 - e2)

    return math.degrees(lat_rad), math.degrees(lon_rad), h


def baseline_length_m(pos1: tuple[float, float, float], pos2: tuple[float, float, float]) -> float:
    """Euclidean distance between two ECEF positions, in metres."""
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(pos1, pos2, strict=False)))


# ---------------------------------------------------------------------------
# Angular scale
# ---------------------------------------------------------------------------


def angular_resolution_arcsec(max_baseline_m: float, freq_hz: float) -> float:
    """
    Approximate synthesised beam FWHM in arcseconds: θ ≈ λ / B_max.

    Ignores weighting, taper, and actual PSF shape.
    Confidence: ~85% — use as an order-of-magnitude estimate only.
    """
    if max_baseline_m <= 0 or freq_hz <= 0:
        return float("nan")
    c = 2.997_924_58e8  # m/s
    wavelength_m = c / freq_hz
    theta_rad = wavelength_m / max_baseline_m
    return math.degrees(theta_rad) * 3600.0


def largest_angular_scale_arcsec(min_baseline_m: float, freq_hz: float) -> float:
    """
    Maximum recoverable angular scale: θ_LAS ≈ λ / B_min.

    Confidence: ~80% — true LAS depends on UV sampling density near origin.
    """
    if min_baseline_m <= 0 or freq_hz <= 0:
        return float("nan")
    c = 2.997_924_58e8
    wavelength_m = c / freq_hz
    theta_rad = wavelength_m / min_baseline_m
    return math.degrees(theta_rad) * 3600.0


def baseline_length_klambda(baseline_m: float, freq_hz: float) -> float:
    """Convert a physical baseline length to kilo-lambda units."""
    if freq_hz <= 0:
        return float("nan")
    c = 2.997_924_58e8
    wavelength_m = c / freq_hz
    return baseline_m / wavelength_m / 1000.0


# ---------------------------------------------------------------------------
# Duration formatting
# ---------------------------------------------------------------------------


def seconds_to_human(seconds: float) -> str:
    """Format a duration in seconds as 'Xh Ym Z.Zs'."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    if h > 0:
        return f"{h}h {m}m {s:.1f}s"
    elif m > 0:
        return f"{m}m {s:.1f}s"
    else:
        return f"{s:.2f}s"
