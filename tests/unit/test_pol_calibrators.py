"""
Unit tests for util/pol_calibrators.py.

No CASA dependency — runs in any Python environment.
"""

from __future__ import annotations

import pytest

from ms_inspect.util.pol_calibrators import (
    PolCalEntry,
    PolFreqEntry,
    is_angle_calibrator,
    is_leakage_calibrator,
    lookup_pol,
    pol_properties_at_freq,
)


# ---------------------------------------------------------------------------
# lookup_pol
# ---------------------------------------------------------------------------

class TestLookupPol:
    def test_lookup_by_b1950_name(self):
        entry = lookup_pol("3C286")
        assert entry is not None
        assert entry.b1950_name == "3C286"

    def test_lookup_by_j2000_name(self):
        entry = lookup_pol("J1331+3030")
        assert entry is not None
        assert entry.b1950_name == "3C286"

    def test_lookup_case_insensitive(self):
        assert lookup_pol("3c286") is not None
        assert lookup_pol("3C286") is not None
        assert lookup_pol("j1331+3030") is not None

    def test_lookup_alias(self):
        entry = lookup_pol("1331+305")
        assert entry is not None
        assert entry.b1950_name == "3C286"

    def test_lookup_unknown_returns_none(self):
        assert lookup_pol("unknown_source") is None
        assert lookup_pol("") is None

    def test_lookup_3c138(self):
        entry = lookup_pol("3C138")
        assert entry is not None
        assert entry.category == "A"
        assert "angle" in entry.role

    def test_lookup_3c147(self):
        entry = lookup_pol("3C147")
        assert entry is not None
        assert entry.category == "C"

    def test_lookup_3c84_alias(self):
        entry = lookup_pol("NGC1275")
        assert entry is not None
        assert entry.b1950_name == "3C84"


# ---------------------------------------------------------------------------
# is_angle_calibrator / is_leakage_calibrator
# ---------------------------------------------------------------------------

class TestCalibratoryRoleChecks:
    def test_3c286_is_angle_calibrator(self):
        assert is_angle_calibrator("3C286") is True

    def test_3c286_is_leakage_calibrator(self):
        assert is_leakage_calibrator("3C286") is True

    def test_3c147_is_not_angle_calibrator(self):
        assert is_angle_calibrator("3C147") is False

    def test_3c147_is_leakage_calibrator(self):
        assert is_leakage_calibrator("3C147") is True

    def test_3c138_is_angle_not_leakage(self):
        assert is_angle_calibrator("3C138") is True
        assert is_leakage_calibrator("3C138") is False

    def test_unknown_is_neither(self):
        assert is_angle_calibrator("unknown") is False
        assert is_leakage_calibrator("unknown") is False


# ---------------------------------------------------------------------------
# 3C286 stable PA
# ---------------------------------------------------------------------------

class Test3C286Properties:
    def test_stable_pa(self):
        entry = lookup_pol("3C286")
        assert entry.stable_pa is True

    def test_no_variability_note(self):
        entry = lookup_pol("3C286")
        assert entry.variability_note is None

    def test_category_a(self):
        entry = lookup_pol("3C286")
        assert entry.category == "A"


# ---------------------------------------------------------------------------
# pol_properties_at_freq — interpolation
# ---------------------------------------------------------------------------

class TestPolPropertiesAtFreq:
    def _get_3c286(self) -> PolCalEntry:
        entry = lookup_pol("3C286")
        assert entry is not None
        return entry

    def test_exact_node_lband(self):
        entry = self._get_3c286()
        props = pol_properties_at_freq(entry, 1.45)
        assert props is not None
        assert abs(props.frac_pol_pct - 9.8) < 0.01
        assert props.pol_angle_deg == pytest.approx(33.0, abs=0.1)
        assert props.frac_pol_upper_limit is False

    def test_interpolation_between_nodes(self):
        """Interpolating between 1.45 and 3.0 GHz should give intermediate values."""
        entry = self._get_3c286()
        # Midpoint at 2.225 GHz — expect frac_pol between 9.8 and 11.0
        props = pol_properties_at_freq(entry, 2.225)
        assert props is not None
        assert 9.8 < props.frac_pol_pct < 11.0

    def test_out_of_range_low(self):
        entry = self._get_3c286()
        props = pol_properties_at_freq(entry, 0.1)
        assert props is None

    def test_out_of_range_high(self):
        entry = self._get_3c286()
        props = pol_properties_at_freq(entry, 100.0)
        assert props is None

    def test_unknown_epoch_returns_none(self):
        entry = self._get_3c286()
        props = pol_properties_at_freq(entry, 1.45, epoch="1990")
        assert props is None

    def test_pa_stable_across_lband_to_qband(self):
        """3C286 PA should be ~33° at every band."""
        entry = self._get_3c286()
        for freq_ghz in [1.45, 6.0, 15.0, 33.0]:
            props = pol_properties_at_freq(entry, freq_ghz)
            assert props is not None, f"No props at {freq_ghz} GHz"
            assert props.pol_angle_deg is not None
            assert abs(props.pol_angle_deg - 33.0) < 1.0, (
                f"PA at {freq_ghz} GHz = {props.pol_angle_deg:.1f}°, expected ~33°"
            )


# ---------------------------------------------------------------------------
# 3C48 — low pol + undefined PA below 4 GHz
# ---------------------------------------------------------------------------

class Test3C48LowPolBelowCBand:
    def _get_3c48(self) -> PolCalEntry:
        entry = lookup_pol("3C48")
        assert entry is not None
        return entry

    def test_lband_upper_limit(self):
        entry = self._get_3c48()
        props = pol_properties_at_freq(entry, 1.45)
        assert props is not None
        assert props.frac_pol_upper_limit is True

    def test_lband_pa_undefined(self):
        entry = self._get_3c48()
        props = pol_properties_at_freq(entry, 1.45)
        assert props is not None
        assert props.pol_angle_deg is None

    def test_cband_usable(self):
        entry = self._get_3c48()
        props = pol_properties_at_freq(entry, 6.0)
        assert props is not None
        assert props.frac_pol_pct is not None
        assert props.frac_pol_pct > 3.0
        assert props.pol_angle_deg is not None

    def test_not_stable_pa(self):
        entry = self._get_3c48()
        assert entry.stable_pa is False


# ---------------------------------------------------------------------------
# 3C138 — variability flag
# ---------------------------------------------------------------------------

class Test3C138Variability:
    def test_has_variability_note(self):
        entry = lookup_pol("3C138")
        assert entry is not None
        assert entry.variability_note is not None
        assert len(entry.variability_note) > 0

    def test_not_stable_pa(self):
        entry = lookup_pol("3C138")
        assert entry.stable_pa is False


# ---------------------------------------------------------------------------
# 3C147 — leakage-only, upper limits below 10 GHz
# ---------------------------------------------------------------------------

class Test3C147LeakageOnly:
    def test_lband_upper_limit(self):
        entry = lookup_pol("3C147")
        props = pol_properties_at_freq(entry, 1.45)
        assert props is not None
        assert props.frac_pol_upper_limit is True
        assert props.frac_pol_pct <= 0.05

    def test_cband_upper_limit(self):
        entry = lookup_pol("3C147")
        props = pol_properties_at_freq(entry, 6.0)
        assert props is not None
        assert props.frac_pol_upper_limit is True
