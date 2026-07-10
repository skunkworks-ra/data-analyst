"""Unit tests for ms_verify_model classification + metrics (no CASA required)."""

from __future__ import annotations

import numpy as np

from ms_inspect.tools.verify_model import _indices, _model_metrics, classify

# Default thresholds mirroring the tool defaults.
_KW = dict(
    default_amp_tol=0.05,
    default_phase_rms_deg=1.0,
    plausible_min_jy=0.1,
    plausible_max_jy=100.0,
    crosshand_ratio_thresh=0.001,
)


class TestClassify:
    def test_pinned_default_flagged(self):
        # amp ~1.0 with flat phase = untouched MODEL=1 Jy default
        status, ratio, reasons = classify(1.0, 0.0, 0.0, False, **_KW)
        assert status == "SUSPECT"
        assert any("default" in r for r in reasons)

    def test_real_one_jy_source_not_flagged(self):
        # A genuine ~1 Jy source has phase structure -> not pinned
        status, _, reasons = classify(1.0, 12.0, 0.0, False, **_KW)
        assert status == "COMPLETE"
        assert reasons == []

    def test_out_of_band_amp_flagged(self):
        status, _, reasons = classify(1e-4, 20.0, 0.0, False, **_KW)
        assert status == "SUSPECT"
        assert any("plausible band" in r for r in reasons)

    def test_polcal_with_polarization_ok(self):
        # cross/parallel ratio 0.06 > 0.001 -> polarization present
        status, ratio, reasons = classify(7.0, 15.0, 0.42, True, **_KW)
        assert status == "COMPLETE"
        assert ratio == round(0.42 / 7.0, 6)

    def test_polcal_missing_polarization_flagged(self):
        # Stokes-I model / clobbered: zero cross-hands on a pol-cal field
        status, ratio, reasons = classify(7.0, 15.0, 0.0, True, **_KW)
        assert status == "SUSPECT"
        assert any("no polarization" in r for r in reasons)
        assert ratio == 0.0

    def test_nonpolcal_zero_crosshand_not_flagged(self):
        # A Stokes-I flux/phase cal legitimately has zero cross-hands
        status, _, reasons = classify(7.0, 15.0, 0.0, False, **_KW)
        assert status == "COMPLETE"

    def test_polcal_no_crosshand_correlations_flagged(self):
        # MS without cross-hand correlations (cross_amp None) on a pol cal
        status, ratio, reasons = classify(7.0, 15.0, None, True, **_KW)
        assert status == "SUSPECT"
        assert ratio is None
        assert any("no cross-hand" in r for r in reasons)


class TestIndices:
    def test_parallel_and_cross_split(self):
        # RR,RL,LR,LL = 5,6,7,8
        codes = [5, 6, 7, 8]
        assert _indices(codes, {5, 8, 9, 12}) == [0, 3]
        assert _indices(codes, {6, 7, 10, 11}) == [1, 2]


class TestModelMetrics:
    def test_polarized_model_metrics(self):
        # [corr, chan, row]; corr order RR,RL,LR,LL
        n_chan, n_row = 2, 4
        data = np.zeros((4, n_chan, n_row), dtype=complex)
        data[0] = 5.0  # RR
        data[3] = 5.0  # LL
        data[1] = 0.5  # RL (Q/U)
        data[2] = 0.5  # LR
        flag = np.zeros((4, n_chan, n_row), dtype=bool)
        m = _model_metrics(data, flag, par_idx=[0, 3], cross_idx=[1, 2])
        assert m["par_amp"] == 5.0
        assert m["cross_amp"] == 0.5
        assert m["par_phase_rms"] == 0.0

    def test_stokes_i_only_zero_crosshands(self):
        data = np.zeros((4, 1, 3), dtype=complex)
        data[0] = 2.0
        data[3] = 2.0
        flag = np.zeros((4, 1, 3), dtype=bool)
        m = _model_metrics(data, flag, par_idx=[0, 3], cross_idx=[1, 2])
        assert m["par_amp"] == 2.0
        assert m["cross_amp"] == 0.0

    def test_all_flagged_returns_none(self):
        data = np.ones((4, 1, 2), dtype=complex)
        flag = np.ones((4, 1, 2), dtype=bool)
        m = _model_metrics(data, flag, par_idx=[0, 3], cross_idx=[1, 2])
        assert m["n_par"] == 0
        assert m["par_amp"] is None
