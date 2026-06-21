"""Unit tests for spw_amp_severity pure-logic helpers (no CASA required)."""

from __future__ import annotations

import numpy as np

from ms_inspect.tools.spw_amp_severity import _ChanReservoir, _corr_first_axis


def test_corr_first_axis_folds_corr_and_rows():
    # [n_corr=2, n_chan=3, n_rows=4] → [n_chan=3, n_corr*n_rows=8]
    arr = np.arange(2 * 3 * 4).reshape(2, 3, 4)
    out = _corr_first_axis(arr)
    assert out.shape == (3, 8)
    # channel 0 must contain exactly the corr/row values at chan index 0
    expected_ch0 = np.concatenate([arr[0, 0, :], arr[1, 0, :]])
    np.testing.assert_array_equal(np.sort(out[0]), np.sort(expected_ch0))


def test_reservoir_stats_recover_distribution():
    rng = np.random.default_rng(0)
    data = rng.normal(10.0, 2.0, size=200_000)
    data = np.abs(data)  # amplitudes are positive
    res = _ChanReservoir(5000)
    # feed in several batches to exercise the merge path
    for batch in np.array_split(data, 7):
        res.add(batch, rng)
    st = res.stats()
    # sample-based median/MAD should track the population within a few percent
    assert abs(st["median"] - np.median(data)) / np.median(data) < 0.02
    pop_mad = np.median(np.abs(data - np.median(data)))
    assert abs(st["mad"] - pop_mad) / pop_mad < 0.05
    # min/max are tracked EXACTLY, not from the sample
    assert st["min"] == float(data.min())
    assert st["max"] == float(data.max())


def test_reservoir_bounded_memory():
    rng = np.random.default_rng(1)
    res = _ChanReservoir(1000)
    for _ in range(50):
        res.add(rng.random(10_000), rng)
    assert res.vals.size <= 1000
    assert res.n_unflagged == 50 * 10_000


def test_reservoir_empty_returns_none():
    assert _ChanReservoir(100).stats() is None
