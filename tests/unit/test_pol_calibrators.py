"""
Unit tests for util/pol_calibrators.py.

No CASA dependency — runs in any Python environment.
"""

from __future__ import annotations

import pytest

from ms_inspect.util.pol_calibrators import (
    PolCalEntry,
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


# ---------------------------------------------------------------------------
# 3C48 — perley_butler_2013 epoch
# ---------------------------------------------------------------------------


class Test3C48PerleyButler2013:
    def _get_3c48(self) -> PolCalEntry:
        entry = lookup_pol("3C48")
        assert entry is not None
        return entry

    def test_epoch_present(self):
        entry = self._get_3c48()
        assert "perley_butler_2013" in entry.epochs

    def test_17_nodes(self):
        entry = self._get_3c48()
        rows = entry.epochs["perley_butler_2013"]
        assert len(rows) == 17

    def test_flux_jy_populated(self):
        entry = self._get_3c48()
        rows = entry.epochs["perley_butler_2013"]
        for row in rows:
            assert row.flux_jy is not None
            assert row.flux_jy > 0.0

    def test_flux_jy_decreasing_with_frequency(self):
        """Flux density should fall monotonically for this steep-spectrum source."""
        entry = self._get_3c48()
        rows = sorted(entry.epochs["perley_butler_2013"], key=lambda r: r.freq_ghz)
        fluxes = [r.flux_jy for r in rows]
        for i in range(len(fluxes) - 1):
            assert fluxes[i] > fluxes[i + 1], (
                f"Flux not decreasing: {rows[i].freq_ghz} GHz → {rows[i + 1].freq_ghz} GHz"
            )

    def test_lband_pa_undefined(self):
        """PA undefined for 1.022, 1.465, 1.865 GHz nodes due to RM wrapping."""
        entry = self._get_3c48()
        rows = entry.epochs["perley_butler_2013"]
        ambiguous = [r for r in rows if r.freq_ghz <= 1.865]
        assert len(ambiguous) == 3
        for row in ambiguous:
            assert row.pol_angle_deg is None, f"Expected None pol_angle at {row.freq_ghz} GHz"

    def test_sband_pa_defined(self):
        """PA defined and negative for S-band nodes (2.565 GHz and above)."""
        entry = self._get_3c48()
        rows = entry.epochs["perley_butler_2013"]
        sband_and_above = [r for r in rows if r.freq_ghz >= 2.565]
        assert len(sband_and_above) == 14
        for row in sband_and_above:
            assert row.pol_angle_deg is not None
            assert row.pol_angle_deg < 0.0, (
                f"Expected negative pol_angle at {row.freq_ghz} GHz, got {row.pol_angle_deg:.2f}°"
            )

    def test_sband_polfrac_rising(self):
        """Pol fraction should rise from S-band into C-band."""
        entry = self._get_3c48()
        props_s = pol_properties_at_freq(entry, 3.0, epoch="perley_butler_2013")
        props_c = pol_properties_at_freq(entry, 6.5, epoch="perley_butler_2013")
        assert props_s is not None and props_c is not None
        assert props_c.frac_pol_pct > props_s.frac_pol_pct

    def test_interpolation_sband(self):
        """Interpolation at 3.0 GHz should fall between the 2.565 and 3.565 GHz nodes."""
        entry = self._get_3c48()
        props = pol_properties_at_freq(entry, 3.0, epoch="perley_butler_2013")
        assert props is not None
        assert 1.548 < props.frac_pol_pct < 2.911
        assert props.pol_angle_deg is not None
        assert -112.89 < props.pol_angle_deg < -83.94

    def test_lband_upper_limit_preserved(self):
        entry = self._get_3c48()
        rows = entry.epochs["perley_butler_2013"]
        lband = [r for r in rows if r.freq_ghz <= 1.465]
        for row in lband:
            assert row.frac_pol_upper_limit is True

    def test_2019_epoch_unaffected(self):
        """The existing 2019 epoch must still be present and unchanged."""
        entry = self._get_3c48()
        props = pol_properties_at_freq(entry, 6.0, epoch="2019")
        assert props is not None
        assert abs(props.frac_pol_pct - 5.0) < 0.1
        assert props.pol_angle_deg == pytest.approx(-66.0, abs=1.0)

    def test_flux_jy_defaults_none_in_2019_epoch(self):
        """flux_jy was not tabulated for the 2019 band-averaged data."""
        entry = self._get_3c48()
        props = pol_properties_at_freq(entry, 6.0, epoch="2019")
        assert props is not None
        assert props.flux_jy is None


# ---------------------------------------------------------------------------
# flux_jy field — backward compatibility
# ---------------------------------------------------------------------------


class TestFluxJyField:
    def test_flux_jy_defaults_none_for_2019_entries(self):
        """All 2019 PolFreqEntry instances should have flux_jy=None by default."""
        entry = lookup_pol("3C286")
        assert entry is not None
        for row in entry.epochs.get("2019", []):
            assert row.flux_jy is None

    def test_flux_jy_populated_for_pb2013(self):
        entry = lookup_pol("3C48")
        assert entry is not None
        for row in entry.epochs.get("perley_butler_2013", []):
            assert row.flux_jy is not None
