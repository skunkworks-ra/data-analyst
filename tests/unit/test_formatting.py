"""
Unit tests for util/formatting.py — response envelopes, completion flags,
numeric formatting helpers.

No CASA dependency.
"""

from __future__ import annotations

from ms_inspect.util.formatting import (
    _collect_flags,
    error_envelope,
    field,
    response_envelope,
    round_dict,
    truncate_list,
    worst_flag,
)

# ---------------------------------------------------------------------------
# field()
# ---------------------------------------------------------------------------


class TestField:
    def test_default_complete(self):
        result = field(42)
        assert result == {"value": 42, "flag": "COMPLETE"}

    def test_with_flag(self):
        result = field(None, "UNAVAILABLE")
        assert result["flag"] == "UNAVAILABLE"

    def test_with_note(self):
        result = field(3.14, "INFERRED", note="derived from heuristic")
        assert result["note"] == "derived from heuristic"
        assert result["value"] == 3.14

    def test_no_note_by_default(self):
        result = field(1)
        assert "note" not in result


# ---------------------------------------------------------------------------
# worst_flag()
# ---------------------------------------------------------------------------


class TestWorstFlag:
    def test_empty(self):
        assert worst_flag([]) == "COMPLETE"

    def test_single(self):
        assert worst_flag(["PARTIAL"]) == "PARTIAL"

    def test_ordering(self):
        assert worst_flag(["COMPLETE", "INFERRED"]) == "INFERRED"
        assert worst_flag(["COMPLETE", "PARTIAL"]) == "PARTIAL"
        assert worst_flag(["INFERRED", "SUSPECT"]) == "SUSPECT"
        assert worst_flag(["PARTIAL", "UNAVAILABLE"]) == "UNAVAILABLE"

    def test_all_complete(self):
        assert worst_flag(["COMPLETE", "COMPLETE", "COMPLETE"]) == "COMPLETE"

    def test_worst_wins(self):
        assert (
            worst_flag(["COMPLETE", "INFERRED", "PARTIAL", "SUSPECT", "UNAVAILABLE"])
            == "UNAVAILABLE"
        )


# ---------------------------------------------------------------------------
# _collect_flags()
# ---------------------------------------------------------------------------


class TestCollectFlags:
    def test_flat_dict(self):
        # Outer dict has no "flag" key; inner dict has one
        data = {"a": {"value": 1, "flag": "COMPLETE"}}
        assert _collect_flags(data) == ["COMPLETE"]

    def test_simple_flagged_field(self):
        data = {"value": 42, "flag": "PARTIAL"}
        flags = _collect_flags(data)
        assert "PARTIAL" in flags

    def test_nested(self):
        data = {
            "field_a": {"value": 1, "flag": "COMPLETE"},
            "field_b": {"value": None, "flag": "UNAVAILABLE"},
        }
        flags = _collect_flags(data)
        assert "COMPLETE" in flags
        assert "UNAVAILABLE" in flags

    def test_list_of_dicts(self):
        data = {
            "items": [
                {"value": 1, "flag": "COMPLETE"},
                {"value": 2, "flag": "SUSPECT"},
            ]
        }
        flags = _collect_flags(data)
        assert "COMPLETE" in flags
        assert "SUSPECT" in flags

    def test_no_flags(self):
        assert _collect_flags({"x": 1, "y": "hello"}) == []

    def test_deeply_nested(self):
        data = {
            "level1": {
                "level2": {
                    "value": 99,
                    "flag": "INFERRED",
                }
            }
        }
        flags = _collect_flags(data)
        assert "INFERRED" in flags


# ---------------------------------------------------------------------------
# response_envelope()
# ---------------------------------------------------------------------------


class TestResponseEnvelope:
    def test_basic_structure(self):
        data = {"count": field(10)}
        env = response_envelope("ms_test_tool", "/path/to/test.ms", data)
        assert env["tool"] == "ms_test_tool"
        assert env["ms_path"] == "/path/to/test.ms"
        assert env["status"] == "ok"
        assert env["data"] is data
        assert env["warnings"] == []
        assert "provenance" in env

    def test_completeness_summary_complete(self):
        data = {"a": field(1, "COMPLETE")}
        env = response_envelope("t", "/ms", data)
        assert env["completeness_summary"] == "COMPLETE"

    def test_completeness_summary_worst(self):
        data = {
            "a": field(1, "COMPLETE"),
            "b": field(None, "UNAVAILABLE"),
        }
        env = response_envelope("t", "/ms", data)
        assert env["completeness_summary"] == "UNAVAILABLE"

    def test_extra_flags(self):
        data = {"a": field(1, "COMPLETE")}
        env = response_envelope("t", "/ms", data, extra_flags=["SUSPECT"])
        assert env["completeness_summary"] == "SUSPECT"

    def test_warnings_passed_through(self):
        env = response_envelope("t", "/ms", {}, warnings=["watch out"])
        assert env["warnings"] == ["watch out"]

    def test_casa_calls_in_provenance(self):
        env = response_envelope("t", "/ms", {}, casa_calls=["tb.open()"])
        assert env["provenance"]["casa_calls"] == ["tb.open()"]

    def test_empty_data(self):
        env = response_envelope("t", "/ms", {})
        assert env["completeness_summary"] == "COMPLETE"


# ---------------------------------------------------------------------------
# error_envelope()
# ---------------------------------------------------------------------------


class TestErrorEnvelope:
    def test_structure(self):
        env = error_envelope("ms_test", "/ms", "MS_NOT_FOUND", "File not found")
        assert env["tool"] == "ms_test"
        assert env["ms_path"] == "/ms"
        assert env["status"] == "error"
        assert env["error_type"] == "MS_NOT_FOUND"
        assert env["message"] == "File not found"
        assert env["data"] is None

    def test_none_path(self):
        env = error_envelope("t", None, "CASA_NOT_AVAILABLE", "no casa")
        assert env["ms_path"] is None


# ---------------------------------------------------------------------------
# round_dict()
# ---------------------------------------------------------------------------


class TestRoundDict:
    def test_flat(self):
        result = round_dict({"x": 3.14159265}, decimals=2)
        assert result["x"] == 3.14

    def test_nested(self):
        result = round_dict({"a": {"b": 1.23456}}, decimals=3)
        assert result["a"]["b"] == 1.235

    def test_list_values(self):
        result = round_dict({"vals": [1.111, 2.222, 3.333]}, decimals=1)
        assert result["vals"] == [1.1, 2.2, 3.3]

    def test_non_float_untouched(self):
        result = round_dict({"s": "hello", "i": 42, "f": 1.5}, decimals=0)
        assert result["s"] == "hello"
        assert result["i"] == 42
        assert result["f"] == 2.0

    def test_empty(self):
        assert round_dict({}) == {}


# ---------------------------------------------------------------------------
# truncate_list()
# ---------------------------------------------------------------------------


class TestTruncateList:
    def test_no_truncation(self):
        items, truncated = truncate_list([1, 2, 3], max_items=5)
        assert items == [1, 2, 3]
        assert truncated is False

    def test_exact_limit(self):
        items, truncated = truncate_list([1, 2, 3], max_items=3)
        assert items == [1, 2, 3]
        assert truncated is False

    def test_truncated(self):
        items, truncated = truncate_list(list(range(100)), max_items=10)
        assert len(items) == 10
        assert items == list(range(10))
        assert truncated is True

    def test_empty(self):
        items, truncated = truncate_list([], max_items=5)
        assert items == []
        assert truncated is False
