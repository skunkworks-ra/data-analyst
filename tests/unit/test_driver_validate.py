"""
Unit tests for driver/validate.py — the refusal paths.

Every check here exists to stop a bad decision before it costs hours of
compute, so each one gets a test that proves it actually fires. A validator
that silently accepts everything looks exactly like a validator that works.

These tests use the REAL whitelist and the REAL tool signatures. That is
deliberate: if a tool's run() signature changes and the whitelist stops
matching it, these fail.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from analyst_driver import validate


def write(run_dir: Path, decision: dict) -> Path:
    p = run_dir / "decisions" / "001.json"
    p.write_text(json.dumps(decision))
    return p


def measurements(run_dir: Path, payload: dict) -> str:
    d = run_dir / "steps" / "001-ms_apply_preflag"
    d.mkdir(parents=True, exist_ok=True)
    (d / "measurements.json").write_text(json.dumps(payload))
    return "steps/001-ms_apply_preflag/measurements.json"


GOOD_PREFLAG = {
    "action": "run",
    "tool": "ms_apply_preflag",
    "params": {"cal_fields": "0,1"},
    "rationale": "Both fields are calibrators.",
}


# -- shape ---------------------------------------------------------------


def test_accepts_a_valid_decision(run_dir, whitelist, fake_ms):
    d = validate.validate(write(run_dir, GOOD_PREFLAG), whitelist, run_dir, fake_ms, [])
    assert d["action"] == "run"


def test_rejects_malformed_json(run_dir, whitelist, fake_ms):
    p = run_dir / "decisions" / "001.json"
    p.write_text("{not json")
    with pytest.raises(validate.Refusal, match="not valid JSON"):
        validate.validate(p, whitelist, run_dir, fake_ms, [])


def test_rejects_a_json_list(run_dir, whitelist, fake_ms):
    p = run_dir / "decisions" / "001.json"
    p.write_text("[1, 2]")
    with pytest.raises(validate.Refusal, match="must contain a JSON object"):
        validate.validate(p, whitelist, run_dir, fake_ms, [])


@pytest.mark.parametrize("action", ["proceed", "stop", "skip", "", None])
def test_rejects_an_action_outside_the_four(run_dir, whitelist, fake_ms, action):
    with pytest.raises(validate.Refusal, match="is not one of"):
        validate.validate(
            write(run_dir, {**GOOD_PREFLAG, "action": action}), whitelist, run_dir, fake_ms, []
        )


@pytest.mark.parametrize("action", sorted(validate.ACTIONS))
def test_accepts_every_documented_action(run_dir, whitelist, fake_ms, action):
    """PROMPT.md advertises four actions. All four must be accepted."""
    decision = (
        {**GOOD_PREFLAG, "action": action}
        if action in validate.NEEDS_TOOL
        else {"action": action, "rationale": "done or asking"}
    )
    assert validate.validate(write(run_dir, decision), whitelist, run_dir, fake_ms, [])


def test_rejects_an_empty_rationale(run_dir, whitelist, fake_ms):
    with pytest.raises(validate.Refusal, match="rationale is empty"):
        validate.validate(
            write(run_dir, {**GOOD_PREFLAG, "rationale": "   "}), whitelist, run_dir, fake_ms, []
        )


def test_rejects_run_without_a_tool(run_dir, whitelist, fake_ms):
    with pytest.raises(validate.Refusal, match="needs a tool"):
        validate.validate(
            write(run_dir, {"action": "run", "rationale": "x"}), whitelist, run_dir, fake_ms, []
        )


def test_rejects_non_object_params(run_dir, whitelist, fake_ms):
    with pytest.raises(validate.Refusal, match="params must be an object"):
        validate.validate(
            write(run_dir, {**GOOD_PREFLAG, "params": ["cal_fields"]}),
            whitelist,
            run_dir,
            fake_ms,
            [],
        )


# -- tool and parameters -------------------------------------------------


def test_rejects_a_tool_off_the_whitelist(run_dir, whitelist, fake_ms):
    with pytest.raises(validate.Refusal, match="not on the whitelist"):
        validate.validate(
            write(run_dir, {**GOOD_PREFLAG, "tool": "ms_do_magic"}), whitelist, run_dir, fake_ms, []
        )


def test_rejects_an_unknown_parameter(run_dir, whitelist, fake_ms):
    decision = {**GOOD_PREFLAG, "params": {"cal_fields": "0", "tfcrop": True}}
    with pytest.raises(validate.Refusal, match="has no parameter 'tfcrop'"):
        validate.validate(write(run_dir, decision), whitelist, run_dir, fake_ms, [])


def test_rejects_a_missing_required_parameter(run_dir, whitelist, fake_ms):
    with pytest.raises(validate.Refusal, match="requires 'cal_fields'"):
        validate.validate(
            write(run_dir, {**GOOD_PREFLAG, "params": {}}), whitelist, run_dir, fake_ms, []
        )


@pytest.mark.parametrize("owned", ["ms_path", "workdir", "execute"])
def test_rejects_driver_owned_parameters(run_dir, whitelist, fake_ms, owned):
    decision = {**GOOD_PREFLAG, "params": {"cal_fields": "0", owned: "anything"}}
    with pytest.raises(validate.Refusal, match=f"params.{owned} is set by the driver"):
        validate.validate(write(run_dir, decision), whitelist, run_dir, fake_ms, [])


def test_the_error_message_does_not_advertise_driver_owned_parameters(run_dir, whitelist, fake_ms):
    """Otherwise the model copies ms_path and workdir straight back out of it."""
    decision = {**GOOD_PREFLAG, "params": {"cal_fields": "0", "nonsense": 1}}
    with pytest.raises(validate.Refusal) as exc:
        validate.validate(write(run_dir, decision), whitelist, run_dir, fake_ms, [])
    offered = str(exc.value).split("It accepts:")[1]
    assert "ms_path" not in offered
    assert "workdir" not in offered
    assert "execute" not in offered
    assert "cal_fields" in offered


def test_every_whitelisted_tool_is_importable_and_has_run(whitelist):
    """The whitelist declares modules. A typo there is only found at runtime."""
    import importlib

    for name, entry in whitelist["tools"].items():
        mod = importlib.import_module(entry["module"])
        assert hasattr(mod, "run"), f"{name}: {entry['module']} has no run()"


def test_every_whitelisted_tool_takes_workdir_and_execute(whitelist):
    """The driver writes every script into a step directory and never executes it.

    ms_path is deliberately NOT required here: ms_flag_caltable acts on a
    caltable and takes caltable_path instead. driver.generate_script supplies
    the intersection of the owned names with each signature.
    """
    import importlib
    import inspect

    for name, entry in whitelist["tools"].items():
        sig = inspect.signature(importlib.import_module(entry["module"]).run)
        for owned in ("workdir", "execute"):
            assert owned in sig.parameters, f"{name} does not accept {owned}"


def test_a_tool_without_ms_path_is_still_callable(whitelist):
    """Regression: ms_flag_caltable has caltable_path, not ms_path.

    An earlier driver passed ms_path unconditionally, so this tool could never
    be called at all.
    """
    import importlib
    import inspect

    sig = inspect.signature(importlib.import_module("ms_modify.flag_caltable").run)
    assert "ms_path" not in sig.parameters
    assert "caltable_path" in sig.parameters
    assert "ms_flag_caltable" in whitelist["tools"]


# -- preconditions -------------------------------------------------------


def test_rejects_when_a_file_glob_precondition_is_unmet(run_dir, whitelist, fake_ms):
    decision = {
        "action": "run",
        "tool": "ms_fluxscale",
        "params": {"caltable": "a.G", "fluxtable": "b.F", "reference": "0", "transfer": "1"},
        "rationale": "x",
    }
    with pytest.raises(validate.Refusal, match=r"needs a file matching steps/\*/\*\.G"):
        validate.validate(write(run_dir, decision), whitelist, run_dir, fake_ms, [])


def test_accepts_when_the_file_glob_precondition_is_met(run_dir, whitelist, fake_ms):
    (run_dir / "steps" / "003-ms_gaincal").mkdir(parents=True)
    (run_dir / "steps" / "003-ms_gaincal" / "phase.G").write_text("")
    decision = {
        "action": "run",
        "tool": "ms_fluxscale",
        "params": {"caltable": "a.G", "fluxtable": "b.F", "reference": "0", "transfer": "1"},
        "rationale": "x",
    }
    assert validate.validate(write(run_dir, decision), whitelist, run_dir, fake_ms, [])


def test_step_done_precondition_tracks_completed_tools(run_dir, whitelist, fake_ms):
    decision = {
        "action": "run",
        "tool": "ms_apply_initial_rflag",
        "params": {"field": "0"},
        "rationale": "x",
    }
    with pytest.raises(validate.Refusal, match="ms_initial_bandpass completed OK"):
        validate.validate(write(run_dir, decision), whitelist, run_dir, fake_ms, [])
    # ... and passes once that tool has run.
    validate.validate(
        write(run_dir, decision), whitelist, run_dir, fake_ms, ["ms_initial_bandpass"]
    )


def test_ms_exists_precondition_fails_on_a_missing_ms(run_dir, whitelist, tmp_path):
    with pytest.raises(validate.Refusal, match="needs MS exists"):
        validate.validate(
            write(run_dir, GOOD_PREFLAG), whitelist, run_dir, tmp_path / "gone.ms", []
        )


def test_unknown_precondition_is_treated_as_met_not_as_a_crash(run_dir, fake_ms):
    met, label = validate.precondition_status({"nonsense": 1}, run_dir, fake_ms, [])
    assert met is True
    assert "unknown precondition" in label


# -- evidence ------------------------------------------------------------


def test_rejects_a_fabricated_number(run_dir, whitelist, fake_ms):
    src = measurements(run_dir, {"total_flag_fraction": {"value": 0.09}})
    decision = {
        "action": "done",
        "rationale": "x",
        "evidence": [{"name": "total_flag_fraction", "value": 0.62, "source": src}],
    }
    with pytest.raises(validate.Refusal, match="you cited total_flag_fraction=0.62"):
        validate.validate(write(run_dir, decision), whitelist, run_dir, fake_ms, [])


def test_accepts_a_rounded_number(run_dir, whitelist, fake_ms):
    """A model may round 0.6234 to 0.62. It may not invent a number."""
    src = measurements(run_dir, {"flag_fraction": 0.6234})
    decision = {
        "action": "done",
        "rationale": "x",
        "evidence": [{"name": "flag_fraction", "value": 0.62, "source": src}],
    }
    assert validate.validate(write(run_dir, decision), whitelist, run_dir, fake_ms, [])


def test_rejects_a_number_just_outside_the_tolerance(run_dir, whitelist, fake_ms):
    src = measurements(run_dir, {"flag_fraction": 0.50})
    decision = {
        "action": "done",
        "rationale": "x",
        "evidence": [{"name": "flag_fraction", "value": 0.52, "source": src}],
    }
    with pytest.raises(validate.Refusal, match="you cited"):
        validate.validate(write(run_dir, decision), whitelist, run_dir, fake_ms, [])


def test_finds_a_number_nested_inside_the_envelope(run_dir, whitelist, fake_ms):
    src = measurements(run_dir, {"per_field": [{"field_name": "3C286", "flag_fraction": 0.31}]})
    decision = {
        "action": "done",
        "rationale": "x",
        "evidence": [{"name": "flag_fraction", "value": 0.31, "source": src}],
    }
    assert validate.validate(write(run_dir, decision), whitelist, run_dir, fake_ms, [])


def test_rejects_a_missing_evidence_source(run_dir, whitelist, fake_ms):
    decision = {
        "action": "done",
        "rationale": "x",
        "evidence": [{"name": "flag_fraction", "value": 0.1, "source": "steps/nope/m.json"}],
    }
    with pytest.raises(validate.Refusal, match="evidence source does not exist"):
        validate.validate(write(run_dir, decision), whitelist, run_dir, fake_ms, [])


def test_rejects_a_key_absent_from_the_cited_file(run_dir, whitelist, fake_ms):
    src = measurements(run_dir, {"total_flagged": 12})
    decision = {
        "action": "done",
        "rationale": "x",
        "evidence": [{"name": "dynamic_range", "value": 400.0, "source": src}],
    }
    with pytest.raises(validate.Refusal, match="does not appear in"):
        validate.validate(write(run_dir, decision), whitelist, run_dir, fake_ms, [])


def test_rejects_a_malformed_evidence_item(run_dir, whitelist, fake_ms):
    decision = {"action": "done", "rationale": "x", "evidence": [{"value": 1.0}]}
    with pytest.raises(validate.Refusal, match="evidence item is malformed"):
        validate.validate(write(run_dir, decision), whitelist, run_dir, fake_ms, [])


def test_evidence_without_a_value_is_a_citation_not_a_claim(run_dir, whitelist, fake_ms):
    """A pointer to a file, with no number, has nothing to verify."""
    src = measurements(run_dir, {"total_flagged": 12})
    decision = {
        "action": "done",
        "rationale": "x",
        "evidence": [{"name": "total_flagged", "source": src}],
    }
    assert validate.validate(write(run_dir, decision), whitelist, run_dir, fake_ms, [])


def test_reports_every_problem_at_once(run_dir, whitelist, fake_ms):
    """One round trip per attempt is expensive; do not dribble errors out."""
    decision = {
        **GOOD_PREFLAG,
        "params": {"cal_fields": "0", "bogus1": 1, "bogus2": 2, "execute": True},
    }
    with pytest.raises(validate.Refusal) as exc:
        validate.validate(write(run_dir, decision), whitelist, run_dir, fake_ms, [])
    assert len(str(exc.value).splitlines()) >= 3
