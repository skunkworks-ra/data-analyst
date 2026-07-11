"""
Unit tests for the SpW-coverage guardrail (ms_inspect.util.spw_coverage).

The pure set-math (`evaluate_coverage`) is tested directly. The msmd wrapper
(`check_spw_coverage`) is driven by a fake msmetadata object so the intent
inference, the STOP-and-ask raise, and the graceful msmd-failure degrade are all
covered without CASA.

The modelled trap is the AB1345 / G55.7+3.4 case: 3C286 recorded under two field
IDs with disjoint SpWs (0,1 vs 2-9), where the target lives only in 2-9.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from ms_inspect.exceptions import ComputationError
from ms_inspect.util import spw_coverage
from ms_inspect.util.spw_coverage import check_spw_coverage, evaluate_coverage

# ---------------------------------------------------------------------------
# Pure set math
# ---------------------------------------------------------------------------


class TestEvaluateCoverage:
    def test_disjoint_warns_uncovered(self):
        warns = evaluate_coverage({0, 1}, {2, 3, 4, 5}, None, "0", "[3]")
        assert len(warns) == 1
        assert "coverage gap" in warns[0]
        assert "[2, 3, 4, 5]" in warns[0]  # uncovered SpWs surfaced

    def test_full_coverage_silent(self):
        assert evaluate_coverage({2, 3, 4, 5}, {2, 3, 4, 5}, None, "1", "[3]") == []

    def test_partial_coverage_warns(self):
        warns = evaluate_coverage({2, 3}, {2, 3, 4, 5}, None, "1", "[3]")
        assert len(warns) == 1
        assert "[4, 5]" in warns[0]

    def test_selection_drops_all_spws(self):
        warns = evaluate_coverage({0, 1}, {0, 1}, {5}, "0", "[3]")
        assert len(warns) == 1
        assert "no SpWs to solve" in warns[0]

    def test_selection_excludes_needed_spws(self):
        # Solve field carries the target's SpWs, but the explicit selection drops some.
        warns = evaluate_coverage({2, 3, 4, 5}, {2, 3, 4, 5}, {2, 3}, "1", "[3]")
        text = " ".join(warns)
        assert "coverage gap" in text  # effective {2,3} misses {4,5}
        assert "excludes SpWs [4, 5]" in text  # selection-sanity warning


# ---------------------------------------------------------------------------
# msmd wrapper — fake msmetadata
# ---------------------------------------------------------------------------


class FakeMsmd:
    """Minimal stand-in for casatools.msmetadata."""

    def __init__(self, fields):
        # fields: list of (name, intents, spws) indexed by field id
        self._fields = fields

    def nfields(self):
        return len(self._fields)

    def fieldnames(self):
        return [f[0] for f in self._fields]

    def fieldsforname(self, name):
        return [i for i, f in enumerate(self._fields) if f[0] == name]

    def intentsforfield(self, fid):
        return list(self._fields[fid][1])

    def spwsforfield(self, fid):
        return list(self._fields[fid][2])


def _patch_msmd(monkeypatch, fields):
    @contextmanager
    def fake_open(_ms_path):
        yield FakeMsmd(fields)

    monkeypatch.setattr(spw_coverage, "open_msmd", fake_open)


# 3C286 split across two field IDs with disjoint SpWs; phase cal + target in 2-9.
_AB1345_LIKE = [
    ("3C286", ["CALIBRATE_BANDPASS#ON_SOURCE"], [0, 1]),  # 0 — wrong solve field
    ("J1331+3030", ["CALIBRATE_BANDPASS#ON_SOURCE"], [2, 3, 4, 5]),  # 1 — correct ref
    ("J1925+2106", ["CALIBRATE_PHASE#ON_SOURCE"], [2, 3, 4, 5]),  # 2 — transfer
    ("G55.7+3.4", ["OBSERVE_TARGET#ON_SOURCE"], [2, 3, 4, 5]),  # 3 — science target
]


class TestCheckSpwCoverage:
    def test_disjoint_solve_field_warns(self, monkeypatch):
        _patch_msmd(monkeypatch, _AB1345_LIKE)
        warns = check_spw_coverage("/fake.ms", "3C286", "", "")
        assert any("coverage gap" in w for w in warns)
        assert any("[2, 3, 4, 5]" in w for w in warns)

    def test_correct_solve_field_silent(self, monkeypatch):
        _patch_msmd(monkeypatch, _AB1345_LIKE)
        # Solve on field 1 (J1331+3030, SpWs 2-9) — covers target/transfer fully.
        assert check_spw_coverage("/fake.ms", "J1331+3030", "", "") == []

    def test_uninferable_target_raises(self, monkeypatch):
        # No OBSERVE_TARGET / CALIBRATE_PHASE anywhere → STOP and ask.
        only_bp = [
            ("3C286", ["CALIBRATE_BANDPASS#ON_SOURCE"], [0, 1]),
            ("3C147", ["CALIBRATE_FLUX#ON_SOURCE"], [2, 3]),
        ]
        _patch_msmd(monkeypatch, only_bp)
        with pytest.raises(ComputationError, match="Cannot infer"):
            check_spw_coverage("/fake.ms", "3C286", "", "")

    def test_explicit_target_fields_bypasses_inference(self, monkeypatch):
        only_bp = [
            ("3C286", ["CALIBRATE_BANDPASS#ON_SOURCE"], [0, 1]),
            ("3C147", ["CALIBRATE_FLUX#ON_SOURCE"], [2, 3]),
        ]
        _patch_msmd(monkeypatch, only_bp)
        # Explicit target on disjoint SpWs → coverage gap, no raise.
        warns = check_spw_coverage("/fake.ms", "3C286", "", "3C147")
        assert any("coverage gap" in w for w in warns)

    def test_msmd_unavailable_degrades(self, monkeypatch):
        @contextmanager
        def boom(_ms_path):
            raise RuntimeError("no CASA")
            yield  # pragma: no cover

        monkeypatch.setattr(spw_coverage, "open_msmd", boom)
        assert check_spw_coverage("/fake.ms", "3C286", "", "") == []
