"""
Unit tests for tools/shadowing.py's report parsing.

What these cover: _summaries_by_name across both flagdata(mode='list') return
arities, and _shadow_delta's before/after subtraction, including that a missing
or malformed report raises instead of reading as zero shadowing.

What they do NOT cover: the flagdata call itself, the FLAG_CMD read, or the
assembled envelope. The record shapes below are not invented — they are what
casatasks 6.7.5.18 returned against 3C391 D-config on 2026-07-31, where a
single summary agent gives a flat record and two or more give reportN wrappers.
"""

from __future__ import annotations

import pytest

from ms_inspect.tools.shadowing import (
    _SUMMARY_AFTER,
    _SUMMARY_BEFORE,
    _shadow_delta,
    _summaries_by_name,
)


def _summary(name: str, flagged: float, total: float = 216417024.0, antenna=None) -> dict:
    rec = {
        "name": name,
        "type": "summary",
        "flagged": flagged,
        "total": total,
    }
    if antenna is not None:
        rec["antenna"] = antenna
    return rec


def _pair(before_flagged: float, after_flagged: float, **kw) -> dict:
    return {
        "report0": _summary(_SUMMARY_BEFORE, before_flagged, **kw),
        "report1": _summary(_SUMMARY_AFTER, after_flagged, **kw),
    }


class TestSummariesByName:
    def test_single_summary_is_flat(self):
        """One summary agent: no reportN wrapper, 'name' at the top level."""
        result = _summary("shadow_before", 74199808.0)
        assert _summaries_by_name(result) == {"shadow_before": result}

    def test_two_summaries_are_wrapped(self):
        by_name = _summaries_by_name(_pair(74199808.0, 74199808.0))
        assert sorted(by_name) == sorted([_SUMMARY_BEFORE, _SUMMARY_AFTER])

    def test_empty_report_raises(self):
        """The shape mode='shadow' alone returns. Must not read as zero."""
        with pytest.raises(ValueError, match="no report"):
            _summaries_by_name({})

    def test_none_raises(self):
        with pytest.raises(ValueError, match="no report"):
            _summaries_by_name(None)

    def test_unrecognised_shape_raises_rather_than_defaulting(self):
        with pytest.raises(ValueError, match="neither a flat summary nor reportN"):
            _summaries_by_name({"antenna": {}, "spw": {}})

    def test_report_without_a_name_raises(self):
        with pytest.raises(ValueError, match="no 'name' field"):
            _summaries_by_name({"report0": {"flagged": 1.0, "total": 2.0}})


class TestShadowDelta:
    def test_delta_isolates_the_shadow_agent(self):
        """Pre-existing flags belong to 'before' and must not count as shadow."""
        n_total, n_shadow, per_ant = _shadow_delta(_pair(74199808.0, 74466528.0))
        assert n_total == 216417024
        assert n_shadow == 266720
        assert per_ant == []

    def test_measured_zero_is_a_real_answer(self):
        """The 3C391 case: 34% of the data already flagged, none of it shadow."""
        n_total, n_shadow, _ = _shadow_delta(_pair(74199808.0, 74199808.0))
        assert n_total == 216417024
        assert n_shadow == 0

    def test_per_antenna_counts_are_also_differences(self):
        result = _pair(
            100.0,
            160.0,
            antenna={"ea01": {"flagged": 100.0, "total": 1000.0}},
        )
        result["report1"]["antenna"] = {"ea01": {"flagged": 160.0, "total": 1000.0}}
        _, n_shadow, per_ant = _shadow_delta(result)
        assert n_shadow == 60
        assert per_ant == [
            {
                "antenna_name": "ea01",
                "shadow_flag_fraction": 0.06,
                "n_flagged": 60,
                "n_total": 1000,
            }
        ]

    def test_antenna_absent_from_before_counts_from_zero(self):
        result = _pair(0.0, 50.0)
        result["report1"]["antenna"] = {"ea09": {"flagged": 50.0, "total": 500.0}}
        _, _, per_ant = _shadow_delta(result)
        assert per_ant[0]["antenna_name"] == "ea09"
        assert per_ant[0]["n_flagged"] == 50

    def test_missing_after_summary_raises(self):
        with pytest.raises(ValueError, match="no summary named"):
            _shadow_delta({"report0": _summary(_SUMMARY_BEFORE, 1.0)})

    def test_summary_without_counts_raises(self):
        broken = {"report0": _summary(_SUMMARY_BEFORE, 1.0), "report1": {"name": _SUMMARY_AFTER}}
        with pytest.raises(ValueError, match="no 'flagged'/'total'"):
            _shadow_delta(broken)
