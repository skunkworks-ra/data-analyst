"""Unit tests for spw_amp_severity pure-logic helpers (no CASA required)."""

from __future__ import annotations

import numpy as np

from ms_inspect.tools.spw_amp_severity import (
    _bound_chan_records,
    _ChanReservoir,
    _corr_first_axis,
)


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


def _mk_records(n, ptf_fn):
    return [{"chan": i, "peak_to_floor": ptf_fn(i)} for i in range(n)]


def test_bound_chan_records_under_cap_is_unchanged():
    records = _mk_records(10, lambda i: float(i))
    bounded, n_omitted = _bound_chan_records(records, max_records=256)
    assert bounded == records
    assert n_omitted == 0


def test_bound_chan_records_exactly_at_cap_is_unchanged():
    records = _mk_records(256, lambda i: float(i))
    bounded, n_omitted = _bound_chan_records(records, max_records=256)
    assert bounded == records
    assert n_omitted == 0


def test_bound_chan_records_over_cap_keeps_worst_by_peak_to_floor():
    # 1000 channels; peak_to_floor increases with channel index, so the
    # "worst" (highest) 256 are the last 256 channel indices.
    n = 1000
    cap = 256
    records = _mk_records(n, lambda i: float(i))
    bounded, n_omitted = _bound_chan_records(records, max_records=cap)

    assert n_omitted == n - cap
    assert len(bounded) == cap
    # kept the worst (highest peak_to_floor) channels...
    expected_chans = set(range(n - cap, n))
    assert {r["chan"] for r in bounded} == expected_chans
    # ...and re-sorted back into channel order for readability.
    assert [r["chan"] for r in bounded] == sorted(r["chan"] for r in bounded)


def test_bound_chan_records_missing_peak_to_floor_omitted_first():
    # Channels with no unflagged data (peak_to_floor=None) should be the
    # first candidates dropped, ahead of any channel with a real value.
    n = 300
    cap = 256
    records = _mk_records(n, lambda i: None if i < (n - cap + 10) else float(i))
    bounded, n_omitted = _bound_chan_records(records, max_records=cap)

    assert n_omitted == n - cap
    kept_ptf = [r["peak_to_floor"] for r in bounded]
    # every None-ptf record beyond what fits should have been dropped before
    # any real-valued record was dropped
    assert None not in kept_ptf or sum(1 for v in kept_ptf if v is None) <= 10
