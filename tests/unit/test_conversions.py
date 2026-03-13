"""
Unit tests for util/conversions.py — pure conversion and formatting utilities.

No CASA dependency.
"""

from __future__ import annotations

import math
from datetime import UTC

from ms_inspect.util.conversions import (
    angular_resolution_arcsec,
    baseline_length_klambda,
    baseline_length_m,
    corr_codes_to_labels,
    deg_to_rad,
    ecef_to_geodetic,
    freq_to_band_name,
    hz_to_human,
    is_full_stokes,
    largest_angular_scale_arcsec,
    mjd_seconds_to_unix,
    mjd_seconds_to_utc,
    polarization_basis,
    rad_to_deg,
    rad_to_dms,
    rad_to_hms,
    seconds_to_human,
)

# ---------------------------------------------------------------------------
# Correlation type codes
# ---------------------------------------------------------------------------


class TestCorrCodesToLabels:
    def test_circular(self):
        assert corr_codes_to_labels([5, 6, 7, 8]) == ["RR", "RL", "LR", "LL"]

    def test_linear(self):
        assert corr_codes_to_labels([9, 10, 11, 12]) == ["XX", "XY", "YX", "YY"]

    def test_stokes(self):
        assert corr_codes_to_labels([1, 2, 3, 4]) == ["I", "Q", "U", "V"]

    def test_unknown_code(self):
        result = corr_codes_to_labels([999])
        assert result == ["UNKNOWN(999)"]

    def test_empty(self):
        assert corr_codes_to_labels([]) == []


class TestPolarizationBasis:
    def test_circular(self):
        assert polarization_basis(["RR", "RL", "LR", "LL"]) == "circular"

    def test_linear(self):
        assert polarization_basis(["XX", "XY", "YX", "YY"]) == "linear"

    def test_stokes(self):
        assert polarization_basis(["I", "Q", "U", "V"]) == "stokes"

    def test_mixed(self):
        assert polarization_basis(["RR", "XX"]) == "mixed"

    def test_partial_circular(self):
        assert polarization_basis(["RR", "LL"]) == "circular"

    def test_partial_linear(self):
        assert polarization_basis(["XX", "YY"]) == "linear"


class TestIsFullStokes:
    def test_full_circular(self):
        assert is_full_stokes(["RR", "RL", "LR", "LL"]) is True

    def test_full_linear(self):
        assert is_full_stokes(["XX", "XY", "YX", "YY"]) is True

    def test_full_stokes_iquv(self):
        assert is_full_stokes(["I", "Q", "U", "V"]) is True

    def test_partial(self):
        assert is_full_stokes(["RR", "LL"]) is False

    def test_empty(self):
        assert is_full_stokes([]) is False


# ---------------------------------------------------------------------------
# Time conversions
# ---------------------------------------------------------------------------


class TestMJDConversions:
    def test_known_epoch(self):
        # MJD 58000.0 = 2017-09-04 00:00:00 UTC
        mjd_sec = 58000.0 * 86400.0
        result = mjd_seconds_to_utc(mjd_sec)
        assert result.startswith("2017-09-04 00:00:00")
        assert result.endswith("UTC")

    def test_unix_conversion(self):
        # MJD 0 = 1858-11-17 → unix should be negative
        assert mjd_seconds_to_unix(0.0) < 0

    def test_round_trip_consistency(self):
        mjd_sec = 58000.0 * 86400.0
        unix_ts = mjd_seconds_to_unix(mjd_sec)
        # Should be consistent with the UTC string
        from datetime import datetime

        dt = datetime.fromtimestamp(unix_ts, tz=UTC)
        assert dt.year == 2017
        assert dt.month == 9
        assert dt.day == 4


# ---------------------------------------------------------------------------
# Frequency
# ---------------------------------------------------------------------------


class TestHzToHuman:
    def test_ghz(self):
        assert hz_to_human(1.4e9) == "1.4000 GHz"

    def test_mhz(self):
        assert hz_to_human(327e6) == "327.0000 MHz"

    def test_khz(self):
        assert hz_to_human(15e3) == "15.0000 kHz"

    def test_hz(self):
        assert hz_to_human(50.0) == "50.00 Hz"


class TestFreqToBandName:
    # VLA
    def test_vla_l_band(self):
        result = freq_to_band_name(1.5e9, "VLA")
        assert "L-band" in result

    def test_vla_c_band(self):
        result = freq_to_band_name(6.0e9, "EVLA")
        assert "C-band" in result

    def test_vla_x_band(self):
        result = freq_to_band_name(10.0e9, "JVLA")
        assert "X-band" in result

    def test_vla_p_band(self):
        result = freq_to_band_name(350e6, "VLA")
        assert "P-band" in result

    def test_vla_s_band(self):
        result = freq_to_band_name(3.0e9, "VLA")
        assert "S-band" in result

    def test_vla_ku_band(self):
        result = freq_to_band_name(15.0e9, "VLA")
        assert "Ku-band" in result

    def test_vla_k_band(self):
        result = freq_to_band_name(22.0e9, "VLA")
        assert "K-band" in result

    def test_vla_ka_band(self):
        result = freq_to_band_name(33.0e9, "VLA")
        assert "Ka-band" in result

    def test_vla_q_band(self):
        result = freq_to_band_name(44.0e9, "VLA")
        assert "Q-band" in result

    def test_vla_4_band(self):
        result = freq_to_band_name(70e6, "VLA")
        assert "4-band" in result

    # MeerKAT
    def test_meerkat_l_band(self):
        result = freq_to_band_name(1.28e9, "MeerKAT")
        assert "L-band" in result

    def test_meerkat_uhf(self):
        result = freq_to_band_name(800e6, "MeerKAT")
        assert "UHF" in result

    def test_meerkat_s_band(self):
        result = freq_to_band_name(2.5e9, "MeerKAT")
        assert "S-band" in result

    def test_meerkat_unknown(self):
        result = freq_to_band_name(5.0e9, "MeerKAT")
        assert "Unknown" in result

    # uGMRT
    def test_gmrt_band2(self):
        result = freq_to_band_name(400e6, "uGMRT")
        assert "Band 2" in result

    def test_gmrt_band5(self):
        result = freq_to_band_name(1.2e9, "GMRT")
        assert "Band 5" in result

    # Unknown telescope
    def test_unknown_telescope(self):
        assert freq_to_band_name(1.4e9, "ALMA") is None


# ---------------------------------------------------------------------------
# Angle conversions
# ---------------------------------------------------------------------------


class TestAngleConversions:
    def test_rad_to_deg_and_back(self):
        assert abs(rad_to_deg(math.pi) - 180.0) < 1e-10
        assert abs(deg_to_rad(180.0) - math.pi) < 1e-10

    def test_rad_to_hms_zero(self):
        result = rad_to_hms(0.0)
        assert result == "00h00m00.00s"

    def test_rad_to_hms_known(self):
        # 6h = 90 degrees = pi/2 radians
        result = rad_to_hms(math.pi / 2)
        assert result.startswith("06h00m")

    def test_rad_to_dms_positive(self):
        # +45 degrees
        result = rad_to_dms(math.radians(45.0))
        assert result.startswith("+45d00m")

    def test_rad_to_dms_negative(self):
        # -30 degrees
        result = rad_to_dms(math.radians(-30.0))
        assert result.startswith("-30d00m")

    def test_rad_to_dms_zero(self):
        result = rad_to_dms(0.0)
        assert result.startswith("+00d00m")


# ---------------------------------------------------------------------------
# Coordinate conversions
# ---------------------------------------------------------------------------


class TestECEFToGeodetic:
    def test_vla_site(self):
        # VLA centre approximate ECEF: (-1601185, -5041977, 3554876)
        lat, lon, h = ecef_to_geodetic(-1601185.0, -5041977.0, 3554876.0)
        # VLA is at ~34.08° N, ~-107.62° W, ~2100 m
        assert abs(lat - 34.08) < 0.1
        assert abs(lon - (-107.62)) < 0.1
        assert abs(h - 2100) < 200  # rough altitude check

    def test_equator_prime_meridian(self):
        # Point on equator at prime meridian, at sea level
        # ECEF: (a, 0, 0) where a = 6378137
        lat, lon, _ = ecef_to_geodetic(6378137.0, 0.0, 0.0)
        assert abs(lat) < 0.01
        assert abs(lon) < 0.01

    def test_north_pole(self):
        # ECEF: (0, 0, b) where b ~ 6356752
        lat, lon, _ = ecef_to_geodetic(0.0, 0.0, 6356752.3)
        assert abs(lat - 90.0) < 0.01


class TestBaselineLengthM:
    def test_same_point(self):
        assert baseline_length_m((0, 0, 0), (0, 0, 0)) == 0.0

    def test_known_distance(self):
        # 3-4-5 triangle
        d = baseline_length_m((0, 0, 0), (3, 4, 0))
        assert abs(d - 5.0) < 1e-10

    def test_3d(self):
        d = baseline_length_m((1, 2, 3), (4, 6, 3))
        assert abs(d - 5.0) < 1e-10


# ---------------------------------------------------------------------------
# Angular scale
# ---------------------------------------------------------------------------


class TestAngularScale:
    def test_resolution_basic(self):
        # 1 km baseline at 1.4 GHz → λ ≈ 0.214 m → θ ≈ 44 arcsec
        res = angular_resolution_arcsec(1000.0, 1.4e9)
        assert 40 < res < 50

    def test_resolution_zero_baseline(self):
        assert math.isnan(angular_resolution_arcsec(0.0, 1.4e9))

    def test_resolution_zero_freq(self):
        assert math.isnan(angular_resolution_arcsec(1000.0, 0.0))

    def test_las_basic(self):
        las = largest_angular_scale_arcsec(100.0, 1.4e9)
        assert las > 0

    def test_las_zero_inputs(self):
        assert math.isnan(largest_angular_scale_arcsec(0.0, 1.4e9))
        assert math.isnan(largest_angular_scale_arcsec(100.0, 0.0))

    def test_baseline_klambda(self):
        # 1 km at 1.4 GHz: λ ≈ 0.214 m → 1000/0.214/1000 ≈ 4.67 kλ
        kl = baseline_length_klambda(1000.0, 1.4e9)
        assert 4.5 < kl < 5.0

    def test_baseline_klambda_zero_freq(self):
        assert math.isnan(baseline_length_klambda(1000.0, 0.0))


# ---------------------------------------------------------------------------
# Duration formatting
# ---------------------------------------------------------------------------


class TestSecondsToHuman:
    def test_hours(self):
        assert seconds_to_human(3661.5) == "1h 1m 1.5s"

    def test_minutes(self):
        assert seconds_to_human(125.3) == "2m 5.3s"

    def test_seconds_only(self):
        assert seconds_to_human(3.14) == "3.14s"

    def test_zero(self):
        assert seconds_to_human(0.0) == "0.00s"
