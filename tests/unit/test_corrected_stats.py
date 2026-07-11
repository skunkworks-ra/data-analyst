"""
Unit tests for ms_inspect/tools/corrected_stats.py pure numeric core.

No CASA required — exercises parallel-hand selection and the per-field
amplitude/phase statistics on synthetic visibility arrays.
"""

from __future__ import annotations

import numpy as np

from ms_inspect.tools.corrected_stats import _field_stats, _parallel_indices


class TestParallelIndices:
    def test_circular_basis(self):
        # RR RL LR LL  → codes 5 6 7 8 ; parallel = RR(0), LL(3)
        assert _parallel_indices([5, 6, 7, 8]) == [0, 3]

    def test_linear_basis(self):
        # XX XY YX YY → codes 9 10 11 12 ; parallel = XX(0), YY(3)
        assert _parallel_indices([9, 10, 11, 12]) == [0, 3]

    def test_parallel_only(self):
        assert _parallel_indices([5, 8]) == [0, 1]

    def test_unknown_falls_back_to_all(self):
        assert _parallel_indices([99, 98]) == [0, 1]


class TestFieldStats:
    def _block(self, amp, phase_deg, n_chan=10, n_rows=20, n_corr=4):
        """Build a [n_corr, n_chan, n_rows] complex block at fixed amp/phase."""
        val = amp * np.exp(1j * np.deg2rad(phase_deg))
        return np.full((n_corr, n_chan, n_rows), val, dtype=complex)

    def test_point_source_clean(self):
        data = self._block(15.0, 0.0)
        flag = np.zeros_like(data, dtype=bool)
        s = _field_stats(data, flag, [0, 3], None, None)
        assert abs(s["amp_median"] - 15.0) < 1e-6
        assert s["amp_robust_std"] == 0.0
        assert abs(s["phase_rms_deg"]) < 1e-6
        # channel-averaged: one sample per (parallel corr, row) = 2 * 20
        assert s["n_samples"] == 2 * 20

    def test_channel_range_excludes_edges(self):
        data = self._block(1.0, 0.0)
        # Corrupt edge channels (0 and 9) with huge amplitude.
        data[:, 0, :] = 1000.0
        data[:, 9, :] = 1000.0
        flag = np.zeros_like(data, dtype=bool)
        # Without edge exclusion the median is pulled up.
        s_all = _field_stats(data, flag, [0, 3], None, None)
        assert s_all["amp_p95"] > 100
        # In-band channels 1..8 only → clean.
        s_in = _field_stats(data, flag, [0, 3], 1, 9)
        assert abs(s_in["amp_median"] - 1.0) < 1e-6
        assert s_in["amp_p95"] < 2.0

    def test_all_flagged(self):
        data = self._block(5.0, 0.0)
        flag = np.ones_like(data, dtype=bool)
        s = _field_stats(data, flag, [0, 3], None, None)
        assert s["amp_median"] is None
        assert s["phase_rms_deg"] is None
        assert s["n_samples"] == 0

    def test_phase_rms(self):
        # Half the rows at +30 deg, half at -30 deg → RMS = 30 deg.
        data = self._block(2.0, 0.0, n_rows=20)
        data[:, :, :10] = 2.0 * np.exp(1j * np.deg2rad(30.0))
        data[:, :, 10:] = 2.0 * np.exp(1j * np.deg2rad(-30.0))
        flag = np.zeros_like(data, dtype=bool)
        s = _field_stats(data, flag, [0, 3], None, None)
        assert abs(s["phase_rms_deg"] - 30.0) < 0.5
