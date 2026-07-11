"""
Unit tests for describe_numeric_fields (ms_inspect.util.casa_context).

The numeric-resolution path is exercised by monkeypatching open_msmd so the
tests need no CASA. The early-return paths (empty / name-only selections) need
no patching at all — they short-circuit before any MS is opened.
"""

from __future__ import annotations

import contextlib

import pytest

from ms_inspect.util import casa_context
from ms_inspect.util.casa_context import describe_numeric_fields


class _FakeMsmd:
    def __init__(self, names):
        self._names = names

    def fieldnames(self):
        return self._names


@pytest.fixture
def patch_msmd(monkeypatch):
    """Patch open_msmd to yield a fake msmd with the given field names."""

    def _apply(names):
        @contextlib.contextmanager
        def _fake_open_msmd(ms_path):
            yield _FakeMsmd(names)

        monkeypatch.setattr(casa_context, "open_msmd", _fake_open_msmd)

    return _apply


class TestEarlyReturns:
    def test_empty_selection_returns_empty(self):
        assert describe_numeric_fields("/x.ms", "") == []

    def test_whitespace_selection_returns_empty(self):
        assert describe_numeric_fields("/x.ms", "   ") == []

    def test_name_only_selection_returns_empty(self):
        # No CASA call should be needed — names short-circuit.
        assert describe_numeric_fields("/x.ms", "3C147,J1331+3030") == []

    def test_range_token_not_treated_as_numeric(self):
        # '1~3' is not a bare integer; nothing to warn about without other ints.
        assert describe_numeric_fields("/x.ms", "1~3") == []


class TestNumericResolution:
    def test_single_numeric_resolved(self, patch_msmd):
        patch_msmd(["3C286", "J1331+3030", "J1407+2827"])
        out = describe_numeric_fields("/x.ms", "1")
        assert len(out) == 1
        assert "'1'→'J1331+3030'" in out[0]
        assert "prefer field NAMES" in out[0]

    def test_multiple_numeric_resolved(self, patch_msmd):
        patch_msmd(["a", "b", "c", "d", "e", "3C84"])
        out = describe_numeric_fields("/x.ms", "1,4,5")
        assert "'1'→'b'" in out[0]
        assert "'4'→'e'" in out[0]
        assert "'5'→'3C84'" in out[0]

    def test_out_of_range_flagged(self, patch_msmd):
        patch_msmd(["a", "b"])
        out = describe_numeric_fields("/x.ms", "9")
        assert "'9'→<out of range>" in out[0]

    def test_mixed_name_and_numeric_resolves_numeric_only(self, patch_msmd):
        patch_msmd(["a", "b", "c"])
        out = describe_numeric_fields("/x.ms", "J1331+3030,2")
        assert "'2'→'c'" in out[0]

    def test_casa_failure_is_non_fatal(self, monkeypatch):
        @contextlib.contextmanager
        def _boom(ms_path):
            raise RuntimeError("no CASA")
            yield  # pragma: no cover

        monkeypatch.setattr(casa_context, "open_msmd", _boom)
        # Must swallow the error and return [] rather than raise.
        assert describe_numeric_fields("/x.ms", "1") == []
