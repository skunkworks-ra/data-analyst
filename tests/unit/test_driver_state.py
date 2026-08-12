"""
Unit tests for driver/state.py.

run.json is the driver's only memory across ticks, and the driver can be killed
at any moment — including between submitting a job and recording its id. So the
write must be atomic and the round trip must be lossless.
"""

from __future__ import annotations

import json

import pytest

from analyst_driver import state as state_mod


def make(**kw) -> state_mod.RunState:
    base = {
        "run_id": "r1",
        "goal": "image it",
        "recipe": "vla_continuum",
        "input_path": "/data/x.ms",
        "started_utc": "2026-08-11T00:00:00Z",
    }
    return state_mod.RunState(**{**base, **kw})


def test_round_trip_without_a_pending_job(run_dir):
    st = make(step=4, tools_done=["ms_apply_preflag"])
    state_mod.save(run_dir, st)
    back = state_mod.load(run_dir)
    assert back == st
    assert back.pending is None


def test_round_trip_with_a_pending_job(run_dir):
    st = make(
        pending=state_mod.Pending(
            job_id="slurm:12345",
            step=7,
            tool="ms_tclean",
            submitted_utc="2026-08-11T01:00:00Z",
            step_dir="/runs/r1/steps/007-ms_tclean",
        )
    )
    state_mod.save(run_dir, st)
    back = state_mod.load(run_dir)
    assert isinstance(back.pending, state_mod.Pending)
    assert back.pending.job_id == "slurm:12345"
    assert back.pending.tool == "ms_tclean"


def test_save_leaves_no_temporary_file_behind(run_dir):
    state_mod.save(run_dir, make())
    assert not list(run_dir.glob("*.tmp"))


def test_save_overwrites_cleanly(run_dir):
    state_mod.save(run_dir, make(step=1))
    state_mod.save(run_dir, make(step=2))
    assert state_mod.load(run_dir).step == 2


def test_unknown_fields_in_the_file_are_ignored(run_dir):
    """An old run.json from a previous driver version must still load."""
    state_mod.save(run_dir, make())
    raw = json.loads(state_mod.state_path(run_dir).read_text())
    raw["some_field_we_removed"] = 1
    state_mod.state_path(run_dir).write_text(json.dumps(raw))
    assert state_mod.load(run_dir).run_id == "r1"


def test_terminal_statuses(run_dir):
    assert state_mod.STATUS_DONE in state_mod.TERMINAL
    assert state_mod.STATUS_NEEDS_HUMAN in state_mod.TERMINAL
    assert state_mod.STATUS_STOPPED in state_mod.TERMINAL
    assert state_mod.STATUS_RUNNING not in state_mod.TERMINAL
    assert state_mod.STATUS_IDLE not in state_mod.TERMINAL


# -- the cycle detector --------------------------------------------------


def test_the_same_call_hashes_the_same():
    a = state_mod.call_digest("ms_gaincal", {"field": "0", "solint": "int"})
    b = state_mod.call_digest("ms_gaincal", {"solint": "int", "field": "0"})
    assert a == b, "key order must not change the digest"


def test_a_changed_parameter_changes_the_hash():
    a = state_mod.call_digest("ms_gaincal", {"field": "0"})
    b = state_mod.call_digest("ms_gaincal", {"field": "1"})
    assert a != b


def test_a_changed_tool_changes_the_hash():
    assert state_mod.call_digest("ms_gaincal", {}) != state_mod.call_digest("ms_bandpass", {})


def test_a_redo_with_new_parameters_is_not_a_cycle():
    """The point of redo. Same tool, different scales, must be allowed."""
    a = state_mod.call_digest("ms_apply_rflag", {"field": "0", "timedevscale": 5.0})
    b = state_mod.call_digest("ms_apply_rflag", {"field": "0", "timedevscale": 7.0})
    assert a != b


# -- paths ---------------------------------------------------------------


def test_step_dir_is_zero_padded(run_dir):
    assert state_mod.step_dir(run_dir, 7, "ms_tclean").name == "007-ms_tclean"


def test_step_dir_finds_an_existing_directory_by_number(run_dir):
    (run_dir / "steps" / "007-ms_tclean").mkdir(parents=True)
    assert state_mod.step_dir(run_dir, 7).name == "007-ms_tclean"


def test_decision_path_is_zero_padded(run_dir):
    assert state_mod.decision_path(run_dir, 3).name == "003.json"


@pytest.mark.parametrize("step", [1, 42, 999])
def test_step_and_decision_numbering_agree(run_dir, step):
    assert state_mod.decision_path(run_dir, step).stem == f"{step:03d}"


# -- the MS registry -----------------------------------------------------


def test_a_new_run_knows_no_ms():
    assert make().ms_for(state_mod.ROLE_RAW) == ""


def test_a_role_resolves_to_its_own_ms():
    st = make()
    st.record_ms(state_mod.ROLE_CALIBRATORS, "/w/calibrators.ms")
    assert st.ms_for(state_mod.ROLE_CALIBRATORS) == "/w/calibrators.ms"


def test_calibrators_falls_back_to_raw_before_the_split():
    """Every solve declares ms_role: calibrators, but preflag has not run yet
    and the calibrator fields are still in the raw MS."""
    st = make()
    st.record_ms(state_mod.ROLE_RAW, "/w/raw.ms")
    assert st.ms_for(state_mod.ROLE_CALIBRATORS) == "/w/raw.ms"


def test_calibrators_stops_falling_back_once_the_split_exists():
    st = make()
    st.record_ms(state_mod.ROLE_RAW, "/w/raw.ms")
    st.record_ms(state_mod.ROLE_CALIBRATORS, "/w/calibrators.ms")
    assert st.ms_for(state_mod.ROLE_CALIBRATORS) == "/w/calibrators.ms"


def test_target_falls_back_to_raw():
    """Without a target split the target fields live in the raw MS, with their
    corrected data already applied."""
    st = make()
    st.record_ms(state_mod.ROLE_RAW, "/w/raw.ms")
    assert st.ms_for(state_mod.ROLE_TARGET) == "/w/raw.ms"


def test_raw_never_falls_back_to_a_split():
    """applycal writes the target fields of the raw MS. Handing it
    calibrators.ms instead would silently calibrate the wrong data."""
    st = make()
    st.record_ms(state_mod.ROLE_CALIBRATORS, "/w/calibrators.ms")
    assert st.ms_for(state_mod.ROLE_RAW) == ""


def test_the_registry_survives_a_round_trip(run_dir):
    st = make()
    st.record_ms(state_mod.ROLE_RAW, "/w/raw.ms")
    st.record_ms(state_mod.ROLE_CALIBRATORS, "/w/calibrators.ms")
    state_mod.save(run_dir, st)
    assert state_mod.load(run_dir).ms_registry == {
        "raw": "/w/raw.ms",
        "calibrators": "/w/calibrators.ms",
    }


def test_planned_outputs_survive_a_round_trip(run_dir):
    """They are captured at submit and checked at harvest, which are different
    driver processes."""
    st = make(
        pending=state_mod.Pending(
            job_id="local:1",
            step=1,
            tool="ms_apply_preflag",
            submitted_utc="2026-08-12T00:00:00Z",
            step_dir="/runs/r1/steps/001",
            planned_outputs=[{"role": "calibrators", "path": "/w/calibrators.ms", "kind": "ms"}],
        )
    )
    state_mod.save(run_dir, st)
    back = state_mod.load(run_dir).pending
    assert back.planned_outputs[0]["path"] == "/w/calibrators.ms"


def test_processed_dir_is_one_place_for_every_product(run_dir):
    assert state_mod.processed_dir(run_dir).name == "processed"
    assert state_mod.processed_dir(run_dir).parent == run_dir
