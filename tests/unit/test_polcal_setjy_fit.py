"""
Unit tests for util/polcal_setjy_fit.py.

No CASA dependency — runs in any Python environment (numpy only).

Key correctness invariants tested:
  - fit_polindex/fit_polangle return ASCENDING order [c0, c1, ...] (CASA convention)
  - c0 recovers the value at the reference frequency
  - None entries in polangle_deg are excluded from the angle fit
  - Insufficient nodes raises ValueError
  - fit_from_catalogue("3C48") produces physically reasonable S-band coefficients
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from ms_inspect.util.polcal_setjy_fit import (
    SetjyPolParams,
    fit_from_catalogue,
    fit_polangle,
    fit_polindex,
    fit_setjy_params,
    fit_stokes_i,
)

# ---------------------------------------------------------------------------
# fit_polindex — coefficient ordering (ascending = CASA convention)
# ---------------------------------------------------------------------------


class TestFitPolindex:
    def test_constant_input_c0_equals_value(self):
        """Constant polfrac → c0 should recover that value at any reffreq."""
        freq = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        polfrac = np.full_like(freq, 0.05)
        coeffs = fit_polindex(freq, polfrac, reffreq_ghz=3.0, deg=3)
        assert coeffs[0] == pytest.approx(0.05, abs=1e-6)

    def test_constant_input_higher_coeffs_near_zero(self):
        """Higher-order coefficients should be negligible for constant input."""
        freq = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        polfrac = np.full_like(freq, 0.10)
        coeffs = fit_polindex(freq, polfrac, reffreq_ghz=3.0, deg=3)
        for c in coeffs[1:]:
            assert abs(c) < 1e-6

    def test_ascending_order_not_numpy_polyfit_order(self):
        """
        Verify that numpy.polynomial.polynomial.polyfit ascending order is used,
        not numpy.polyfit descending order.

        For a linearly increasing polfrac from 0.01 at 1 GHz to 0.05 at 5 GHz
        (reffreq=3 GHz), the value at reffreq should be the midpoint ≈ 0.03.
        coeffs[0] = value at reffreq. coeffs[1] should be positive (rising trend).
        numpy.polyfit would return [c1, c0], so if ordering were wrong coeffs[0]
        would be the slope, not the intercept.
        """
        freq = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        polfrac = np.linspace(0.01, 0.05, 5)
        coeffs = fit_polindex(freq, polfrac, reffreq_ghz=3.0, deg=1)
        # c0 = value at reffreq=3 GHz → midpoint of 0.01..0.05 = 0.03
        assert coeffs[0] == pytest.approx(0.03, abs=1e-6)
        # c1 should be positive (slope)
        assert coeffs[1] > 0

    def test_returns_list_of_floats(self):
        freq = np.array([2.0, 3.0, 4.0, 5.0, 6.0])
        polfrac = np.array([0.02, 0.025, 0.03, 0.035, 0.04])
        coeffs = fit_polindex(freq, polfrac, reffreq_ghz=4.0, deg=2)
        assert isinstance(coeffs, list)
        assert all(isinstance(c, float) for c in coeffs)
        assert len(coeffs) == 3

    def test_degree_determines_length(self):
        freq = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
        polfrac = np.ones(6) * 0.04
        for deg in [1, 2, 3]:
            coeffs = fit_polindex(freq, polfrac, reffreq_ghz=3.0, deg=deg)
            assert len(coeffs) == deg + 1


# ---------------------------------------------------------------------------
# fit_polangle — same ordering invariant, input is radians
# ---------------------------------------------------------------------------


class TestFitPolangle:
    def test_constant_angle_c0_equals_value(self):
        """Constant angle in radians → c0 should recover that value."""
        angle_rad = math.radians(-96.0)
        freq = np.array([2.0, 3.0, 4.0, 5.0, 6.0, 7.0])
        angles = np.full_like(freq, angle_rad)
        coeffs = fit_polangle(freq, angles, reffreq_ghz=4.0, deg=2)
        assert coeffs[0] == pytest.approx(angle_rad, abs=1e-6)

    def test_returns_radians_not_degrees(self):
        """fit_polangle takes radians and returns radians (no internal conversion)."""
        angle_rad = math.radians(-90.0)
        freq = np.array([2.0, 3.0, 4.0, 5.0])
        angles = np.full_like(freq, angle_rad)
        coeffs = fit_polangle(freq, angles, reffreq_ghz=3.0, deg=1)
        # If it accidentally converted to degrees, c0 would be ≈ -90, not ≈ -1.57
        assert abs(coeffs[0]) < math.pi * 2  # within one full circle in radians
        assert coeffs[0] == pytest.approx(angle_rad, abs=1e-6)


# ---------------------------------------------------------------------------
# fit_stokes_i
# ---------------------------------------------------------------------------


class TestFitStokesI:
    def test_exact_power_law_recovers_alpha(self):
        """For a pure power law S ∝ (f/f_ref)^alpha, spix[0] ≈ alpha, spix[1] ≈ 0."""
        alpha = -0.7
        reffreq = 3.0
        freq = np.array([1.0, 2.0, 3.0, 5.0, 8.0, 12.0])
        flux = 10.0 * (freq / reffreq) ** alpha
        flux_at_ref, spix = fit_stokes_i(freq, flux, reffreq_ghz=reffreq)
        assert flux_at_ref == pytest.approx(10.0, rel=1e-4)
        assert spix[0] == pytest.approx(alpha, abs=1e-4)
        assert spix[1] == pytest.approx(0.0, abs=1e-4)

    def test_returns_correct_types(self):
        freq = np.array([1.0, 3.0, 5.0, 10.0])
        flux = np.array([15.0, 9.0, 6.0, 3.0])
        flux_at_ref, spix = fit_stokes_i(freq, flux, reffreq_ghz=3.0)
        assert isinstance(flux_at_ref, float)
        assert isinstance(spix, list)
        assert len(spix) == 2


# ---------------------------------------------------------------------------
# fit_setjy_params — None exclusion, band restriction, error cases
# ---------------------------------------------------------------------------


class TestFitSetjyParams:
    _freq = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
    _flux = [20.0, 14.0, 10.0, 7.5, 6.0, 5.0, 4.2, 3.7]
    _polfrac = [None, None, 0.015, 0.022, 0.030, 0.040, 0.050, 0.055]
    _polangle_deg = [None, None, -100.0, -95.0, -90.0, -88.0, -87.0, -86.0]

    def test_none_polangle_nodes_excluded(self):
        """None entries in polangle_deg must not raise and must not count as data."""
        params = fit_setjy_params(
            self._freq,
            self._flux,
            self._polfrac,
            self._polangle_deg,
            reffreq_ghz=4.0,
        )
        # polangle[0] should be near radians of -90° (the value closest to reffreq=4 GHz)
        assert isinstance(params, SetjyPolParams)
        assert params.polangle[0] == pytest.approx(math.radians(-93.5), abs=0.15)

    def test_none_polfrac_nodes_excluded(self):
        """None entries in polfrac must not raise."""
        params = fit_setjy_params(
            self._freq,
            self._flux,
            self._polfrac,
            self._polangle_deg,
            reffreq_ghz=4.0,
        )
        assert params.polindex[0] > 0

    def test_pol_freq_range_restricts_fit(self):
        """pol_freq_range_ghz should restrict the polindex/polangle nodes used."""
        params_wide = fit_setjy_params(
            self._freq,
            self._flux,
            self._polfrac,
            self._polangle_deg,
            reffreq_ghz=4.0,
        )
        params_narrow = fit_setjy_params(
            self._freq,
            self._flux,
            self._polfrac,
            self._polangle_deg,
            reffreq_ghz=4.0,
            pol_freq_range_ghz=(3.0, 7.0),
        )
        # Different range → different fit; narrow band uses fewer nodes
        # They should not be identical in general
        assert params_wide.polindex != params_narrow.polindex

    def test_insufficient_stokes_i_nodes_raises(self):
        with pytest.raises(ValueError, match="Stokes I"):
            fit_setjy_params(
                [3.0, 4.0],
                [10.0, 7.5],
                [0.02, 0.03],
                [-95.0, -90.0],
                reffreq_ghz=3.5,
            )

    def test_insufficient_polindex_nodes_raises(self):
        """Fewer nodes than polindex_deg+1 should raise ValueError."""
        with pytest.raises(ValueError, match="polindex"):
            fit_setjy_params(
                self._freq,
                self._flux,
                polfrac=[None, None, None, None, None, None, None, 0.05],
                polangle_deg=self._polangle_deg,
                reffreq_ghz=4.0,
                polindex_deg=3,
            )

    def test_insufficient_polangle_nodes_raises(self):
        """Fewer nodes than polangle_poly_deg+1 should raise ValueError."""
        with pytest.raises(ValueError, match="polangle"):
            fit_setjy_params(
                self._freq,
                self._flux,
                self._polfrac,
                polangle_deg=[None, None, None, None, None, None, None, -86.0],
                reffreq_ghz=4.0,
                polangle_poly_deg=4,
            )

    def test_returns_setjy_pol_params(self):
        params = fit_setjy_params(
            self._freq,
            self._flux,
            self._polfrac,
            self._polangle_deg,
            reffreq_ghz=4.0,
        )
        assert isinstance(params, SetjyPolParams)
        assert len(params.spix) == 2
        assert len(params.polindex) == 4  # default deg=3
        assert len(params.polangle) == 5  # default deg=4


# ---------------------------------------------------------------------------
# fit_from_catalogue — 3C48 S-band physically reasonable coefficients
# ---------------------------------------------------------------------------


class TestFitFromCatalogue:
    def test_3c48_sband_polindex_c0_at_3ghz(self):
        """
        3C48 polindex[0] at 3 GHz (pol_freq_range_ghz=(2.0, 9.0)) should be
        between 1.5% and 4% — consistent with the tabulated 1.548% at 2.565 GHz
        and 2.911% at 3.565 GHz (docstring example: ≈ 0.022).
        """
        params = fit_from_catalogue("3C48", reffreq_ghz=3.0, pol_freq_range_ghz=(2.0, 9.0))
        assert 0.015 <= params.polindex[0] <= 0.040

    def test_3c48_sband_polangle_c0_negative_radians(self):
        """
        3C48 polangle[0] at 3 GHz should be negative (all S-band nodes have
        PA between -112.89° and -63.38°). In radians: between -2.0 and -1.1.
        """
        params = fit_from_catalogue("3C48", reffreq_ghz=3.0, pol_freq_range_ghz=(2.0, 9.0))
        assert -2.0 <= params.polangle[0] <= -1.1

    def test_3c48_flux_jy_at_3ghz_reasonable(self):
        """
        3C48 Stokes I at 3 GHz should be between 7 and 11 Jy based on the
        tabulated 9.82 Jy at 2.565 GHz and 7.31 Jy at 3.565 GHz.
        """
        params = fit_from_catalogue("3C48", reffreq_ghz=3.0)
        assert 7.0 <= params.flux_jy <= 11.0

    def test_3c48_coefficient_array_lengths(self):
        """Default degrees: polindex deg=3 → 4 coeffs; polangle deg=4 → 5 coeffs."""
        params = fit_from_catalogue("3C48", reffreq_ghz=3.0, pol_freq_range_ghz=(2.0, 9.0))
        assert len(params.spix) == 2
        assert len(params.polindex) == 4
        assert len(params.polangle) == 5

    def test_3c48_reffreq_stored(self):
        params = fit_from_catalogue("3C48", reffreq_ghz=5.0, pol_freq_range_ghz=(2.0, 9.0))
        assert params.reffreq_ghz == pytest.approx(5.0)

    def test_3c48_narrow_band_too_few_nodes_raises(self):
        """A very narrow range that leaves <4 pol nodes should raise ValueError."""
        with pytest.raises(ValueError):
            fit_from_catalogue(
                "3C48",
                reffreq_ghz=3.0,
                pol_freq_range_ghz=(3.4, 3.6),  # only 1 node (3.565 GHz)
            )

    def test_unknown_calibrator_raises_key_error(self):
        with pytest.raises(KeyError, match="not found in pol catalogue"):
            fit_from_catalogue("J9999+0000", reffreq_ghz=3.0)

    def test_unknown_epoch_raises_key_error(self):
        with pytest.raises(KeyError, match="Epoch"):
            fit_from_catalogue("3C48", reffreq_ghz=3.0, epoch="nonexistent_epoch")

    def test_lband_nodes_excluded_when_pol_freq_range_set(self):
        """
        3C48 L-band nodes (1.022, 1.465, 1.865 GHz) have pol_angle_deg=None.
        With pol_freq_range_ghz=(2.0, 9.0) they must be excluded, leaving
        enough nodes for the default polangle deg=4 fit (need ≥5 nodes with PA).
        """
        # Should NOT raise — 14 S-band-and-above nodes with defined PA are available
        params = fit_from_catalogue("3C48", reffreq_ghz=3.0, pol_freq_range_ghz=(2.0, 9.0))
        assert params is not None
