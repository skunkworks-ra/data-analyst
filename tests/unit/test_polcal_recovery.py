"""
Unit tests for tools/polcal_recovery.py — no CASA required.

The Stokes conversion and the EVPA arithmetic are where the real risk is: a
swapped Q/U rotates every angle by 45 degrees, and a naive EVPA subtraction
reports 178 degrees of disagreement when the truth is 2 degrees. Both failures
are silent and would make the posterior check confidently wrong, so they are
tested against constructed cases with known answers.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from ms_inspect.tools.polcal_recovery import (
    _vector_average,
    evpa_difference_deg,
    frac_pol_and_evpa,
    stokes_from_corr,
)


class TestStokesFromCorr:
    def test_linear_basis_unpolarized(self):
        vis = {"XX": 2.0 + 0j, "XY": 0j, "YX": 0j, "YY": 2.0 + 0j}
        stokes, basis, note = stokes_from_corr(vis)
        assert basis == "linear"
        assert note is None
        assert stokes["I"] == pytest.approx(2.0)
        assert stokes["Q"] == pytest.approx(0.0)
        assert stokes["U"] == pytest.approx(0.0)
        assert stokes["V"] == pytest.approx(0.0)

    def test_linear_basis_q_lives_in_parallel_hands(self):
        """XX-YY carries Q in the linear basis. 10% Q, nothing else."""
        vis = {"XX": 1.1 + 0j, "XY": 0j, "YX": 0j, "YY": 0.9 + 0j}
        stokes, _, _ = stokes_from_corr(vis)
        assert stokes["I"] == pytest.approx(1.0)
        assert stokes["Q"] == pytest.approx(0.1)
        assert stokes["U"] == pytest.approx(0.0)

    def test_linear_basis_u_lives_in_cross_hands(self):
        vis = {"XX": 1.0 + 0j, "XY": 0.1 + 0j, "YX": 0.1 + 0j, "YY": 1.0 + 0j}
        stokes, _, _ = stokes_from_corr(vis)
        assert stokes["U"] == pytest.approx(0.1)
        assert stokes["Q"] == pytest.approx(0.0)

    def test_circular_basis_swaps_the_roles(self):
        """In the circular basis V is in the parallel hands and Q,U in the cross."""
        vis = {"RR": 1.1 + 0j, "RL": 0.1 + 0j, "LR": 0.1 + 0j, "LL": 0.9 + 0j}
        stokes, basis, _ = stokes_from_corr(vis)
        assert basis == "circular"
        assert stokes["I"] == pytest.approx(1.0)
        assert stokes["V"] == pytest.approx(0.1)
        assert stokes["Q"] == pytest.approx(0.1)

    def test_parallel_hands_only_reports_unavailable_u_and_v(self):
        vis = {"XX": 1.0 + 0j, "YY": 1.0 + 0j}
        stokes, basis, note = stokes_from_corr(vis)
        assert basis == "linear"
        assert stokes["I"] is not None
        assert stokes["U"] is None and stokes["V"] is None
        assert note is not None and "Cross-hand" in note

    def test_unknown_basis_is_reported_not_guessed(self):
        stokes, basis, note = stokes_from_corr({"XX": 1.0 + 0j})
        assert basis == "unknown"
        assert all(v is None for v in stokes.values())
        assert note is not None


class TestFracPolAndEvpa:
    def test_pure_q_gives_zero_evpa(self):
        frac, evpa = frac_pol_and_evpa({"I": 1.0, "Q": 0.1, "U": 0.0, "V": 0.0})
        assert frac == pytest.approx(0.1)
        assert evpa == pytest.approx(0.0)

    def test_pure_u_gives_45_degrees(self):
        frac, evpa = frac_pol_and_evpa({"I": 1.0, "Q": 0.0, "U": 0.1, "V": 0.0})
        assert frac == pytest.approx(0.1)
        assert evpa == pytest.approx(45.0)

    def test_negative_q_gives_90_wrapped_to_minus_90(self):
        _, evpa = frac_pol_and_evpa({"I": 1.0, "Q": -0.1, "U": 0.0, "V": 0.0})
        assert evpa == pytest.approx(-90.0)

    def test_frac_pol_combines_q_and_u_in_quadrature(self):
        frac, _ = frac_pol_and_evpa({"I": 2.0, "Q": 0.3, "U": 0.4, "V": 0.0})
        assert frac == pytest.approx(0.25)  # hypot(0.3,0.4)=0.5, /2.0

    def test_3c286_like_case(self):
        """3C286 at L-band: about 9.5% pol at EVPA 33 degrees."""
        frac_true, evpa_true = 0.095, 33.0
        q = frac_true * math.cos(2 * math.radians(evpa_true))
        u = frac_true * math.sin(2 * math.radians(evpa_true))
        frac, evpa = frac_pol_and_evpa({"I": 1.0, "Q": q, "U": u, "V": 0.0})
        assert frac == pytest.approx(frac_true)
        assert evpa == pytest.approx(evpa_true)

    def test_missing_stokes_returns_none(self):
        assert frac_pol_and_evpa({"I": 1.0, "Q": None, "U": 0.1, "V": None}) == (None, None)

    def test_zero_i_does_not_divide_by_zero(self):
        assert frac_pol_and_evpa({"I": 0.0, "Q": 0.1, "U": 0.1, "V": 0.0}) == (None, None)


class TestEvpaDifference:
    def test_small_difference(self):
        assert evpa_difference_deg(35.0, 33.0) == pytest.approx(2.0)

    def test_wrap_across_the_90_boundary(self):
        """89 vs -89 is 2 degrees apart, not 178. This is the trap."""
        d = evpa_difference_deg(89.0, -89.0)
        assert abs(d) == pytest.approx(2.0)

    def test_identical_angles_give_zero(self):
        assert evpa_difference_deg(-12.5, -12.5) == pytest.approx(0.0)

    def test_none_propagates(self):
        assert evpa_difference_deg(None, 10.0) is None
        assert evpa_difference_deg(10.0, None) is None


class TestVectorAverage:
    def _block(self, values: list[complex], n_chan: int = 4, n_row: int = 2):
        arr = np.zeros((len(values), n_chan, n_row), dtype=complex)
        for i, v in enumerate(values):
            arr[i, :, :] = v
        return arr

    def test_averages_over_channels_and_rows(self):
        data = self._block([1.0 + 0j, 3.0 + 0j])
        flag = np.zeros_like(data, dtype=bool)
        avg = _vector_average(data, flag, None, None)
        assert avg[0] == pytest.approx(1.0)
        assert avg[1] == pytest.approx(3.0)

    def test_is_vector_not_amplitude_average(self):
        """Two opposite phases must cancel. An amplitude average would not."""
        data = np.zeros((1, 2, 1), dtype=complex)
        data[0, 0, 0] = 1.0 + 0j
        data[0, 1, 0] = -1.0 + 0j
        flag = np.zeros_like(data, dtype=bool)
        avg = _vector_average(data, flag, None, None)
        assert avg[0] == pytest.approx(0.0)

    def test_flagged_samples_excluded(self):
        data = np.zeros((1, 2, 1), dtype=complex)
        data[0, 0, 0] = 1.0 + 0j
        data[0, 1, 0] = 99.0 + 0j
        flag = np.zeros_like(data, dtype=bool)
        flag[0, 1, 0] = True
        avg = _vector_average(data, flag, None, None)
        assert avg[0] == pytest.approx(1.0)

    def test_channel_range_respected(self):
        data = np.zeros((1, 4, 1), dtype=complex)
        data[0, :, 0] = [1.0, 2.0, 3.0, 100.0]
        flag = np.zeros_like(data, dtype=bool)
        avg = _vector_average(data, flag, 0, 3)
        assert avg[0] == pytest.approx(2.0)

    def test_all_flagged_returns_none(self):
        data = self._block([1.0 + 0j])
        flag = np.ones_like(data, dtype=bool)
        assert _vector_average(data, flag, None, None) is None
