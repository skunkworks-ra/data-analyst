"""
Unit tests for driver/verifier.py.

The load-bearing property is that the verifier never reports a pass it did not
earn. A missing measurement must read as NOT_CHECKED, and the rendered block
must state how many checks actually ran — a check that cannot fail is not
evidence.
"""

from __future__ import annotations

import pytest

from analyst_driver import verifier


def test_pass_when_the_number_is_inside_the_limit(rules):
    v = verifier.check(rules, "ms_apply_preflag", {"flag_fraction": 0.08})
    assert v.worst == verifier.PASS
    assert v.n_checked == 1


def test_fail_when_the_number_is_outside_the_limit(rules):
    v = verifier.check(rules, "ms_apply_preflag", {"flag_fraction": 0.71})
    assert v.worst == verifier.FAIL


def test_a_missing_measurement_is_not_a_pass(rules):
    """The whole point. An absent key must never look like a clean result."""
    v = verifier.check(rules, "ms_apply_preflag", {"something_else": 1})
    assert v.worst == verifier.NOT_CHECKED
    assert v.n_checked == 0
    assert all(c.verdict == verifier.NOT_CHECKED for c in v.checks)


def test_a_tool_with_no_rules_reports_no_checks(rules):
    v = verifier.check(rules, "ms_generate_priorcals", {"flag_fraction": 0.9})
    assert v.checks == []
    assert v.worst == verifier.NOT_CHECKED


def test_rules_only_apply_to_their_declared_tools(rules):
    """A flag-fraction rule must not fire on an imaging step."""
    v = verifier.check(rules, "ms_tclean", {"flag_fraction": 0.99})
    assert all(c.key != "flag_fraction" for c in v.checks)


def test_the_severe_rule_fires_as_well_as_the_ordinary_one(rules):
    v = verifier.check(rules, "ms_apply_initial_rflag", {"flag_fraction": 0.95})
    fired = [c for c in v.checks if c.verdict == verifier.FAIL]
    assert len(fired) == 2
    assert any(c.severity == "severe" for c in fired)


def test_a_gte_rule_fails_below_its_floor(rules):
    v = verifier.check(rules, "ms_tclean", {"dynamic_range": 12.0})
    dr = next(c for c in v.checks if c.key == "dynamic_range")
    assert dr.verdict == verifier.FAIL


def test_worst_prefers_fail_over_pass(rules):
    v = verifier.check(rules, "ms_tclean", {"dynamic_range": 5000.0, "peak_over_rms": 2.0})
    assert v.worst == verifier.FAIL


# -- nested lookup -------------------------------------------------------


def test_find_unwraps_the_envelope():
    assert (
        verifier._find({"flag_fraction": {"value": 0.3, "flag": "COMPLETE"}}, "flag_fraction")
        == 0.3
    )


def test_find_reaches_into_a_nested_list():
    payload = {"per_field": [{"name": "3C286"}, {"name": "J1400", "flag_fraction": 0.42}]}
    assert verifier._find(payload, "flag_fraction") == 0.42


def test_find_returns_none_for_an_absent_key():
    assert verifier._find({"a": 1}, "flag_fraction") is None


def test_find_ignores_a_boolean():
    """True is an int in Python. It is not a measurement."""
    assert verifier._find({"flags_applied": True}, "flags_applied") is None


def test_find_ignores_a_string_value():
    assert verifier._find({"flag_fraction": "high"}, "flag_fraction") is None


# -- rendering -----------------------------------------------------------


def test_render_states_how_much_work_it_did(rules):
    text = verifier.render(verifier.check(rules, "ms_apply_preflag", {"flag_fraction": 0.08}))
    assert "1 of 1 checks ran" in text


def test_render_marks_an_unchecked_rule_explicitly(rules):
    text = verifier.render(verifier.check(rules, "ms_apply_preflag", {}))
    assert "NOT CHECKED" in text
    assert "0 of 1 checks ran" in text


def test_render_includes_the_guidance_only_on_a_failure(rules):
    failed = verifier.render(verifier.check(rules, "ms_apply_preflag", {"flag_fraction": 0.8}))
    passed = verifier.render(verifier.check(rules, "ms_apply_preflag", {"flag_fraction": 0.1}))
    assert "calibrator elevation" in failed
    assert "calibrator elevation" not in passed


def test_render_handles_a_tool_with_no_rules(rules):
    assert "0 checks" in verifier.render(verifier.check(rules, "ms_generate_priorcals", {}))


# -- the rules file itself -----------------------------------------------


def test_every_rule_is_well_formed(rules):
    for r in rules:
        assert r["op"] in verifier._OPS, f"{r['key']}: unknown op {r.get('op')}"
        assert isinstance(r["value"], int | float)
        assert r["applies_to"], f"{r['key']}: applies_to is empty, so the rule can never fire"
        assert r.get("message"), f"{r['key']}: a failure with no message tells the model nothing"


def test_every_rule_targets_a_whitelisted_tool(rules, whitelist):
    """A rule naming a tool that cannot be called is dead weight."""
    for r in rules:
        for tool in r["applies_to"]:
            assert tool in whitelist["tools"], f"{r['key']} applies to unknown tool {tool}"


@pytest.mark.parametrize(
    "op,a,b,expected",
    [
        ("lte", 1.0, 1.0, True),
        ("lte", 1.1, 1.0, False),
        ("gte", 1.0, 1.0, True),
        ("lt", 1.0, 1.0, False),
        ("gt", 1.1, 1.0, True),
        ("eq", 1.0, 1.0, True),
    ],
)
def test_operators(op, a, b, expected):
    assert verifier._OPS[op](a, b) is expected
