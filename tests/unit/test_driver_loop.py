"""Unit tests for analyst_driver.loop, executors and backends.

CASA-free: the loop is exercised with a stub backend, a shell-script "CASA
script", and the synchronous local executor. ``sense`` is monkeypatched with
a synthetic ms_workflow_status payload.
"""

import json
import stat
from pathlib import Path

import pytest

from analyst_driver.backends import (
    ClaudeBackend,
    CodexBackend,
    OpencodeBackend,
    StubBackend,
)
from analyst_driver.db import DriverDB
from analyst_driver.executors import (
    HTCondorExecutor,
    LocalExecutor,
    make_executor,
)
from analyst_driver.loop import (
    Loop,
    check_citations,
    harvest_from_tool_calls,
    harvest_metrics,
    parse_decision,
    render_brief,
)
from analyst_driver.owner import read_owner, write_owner

# ------------------------------------------------------------ parse_decision


def test_parse_decision_takes_last_object():
    text = 'noise {"a": 1} more noise\n{"script": "s.py"}\n'
    assert parse_decision(text) == {"script": "s.py"}


def test_parse_decision_with_surrounding_prose():
    text = "I looked at the data.\nDecision:\n" + json.dumps(
        {"script": "cal.py", "tool": "ms_gaincal"}
    )
    assert parse_decision(text)["tool"] == "ms_gaincal"


def test_parse_decision_failure_returns_none():
    assert parse_decision("no json here { broken") is None


def test_parse_decision_nested_braces():
    text = 'x {"script": "s.py", "cited": [{"name": "a", "value": 1}]}'
    dec = parse_decision(text)
    assert dec["cited"][0]["value"] == 1


# ---------------------------------------------------------- harvest_metrics


def test_harvest_walks_envelope_without_naming_keys():
    payload = {
        "data": {
            "flag_fraction": {"value": 0.12, "flag": "OK"},
            "nested": {"dynamic_range": 250.0},
            "n_antennas": 27,
            "name": "not-a-number",
        }
    }
    rows = harvest_metrics(payload, "ms_image_stats")
    by_name = {r["name"]: r for r in rows}
    assert by_name["ms_image_stats.data.flag_fraction"]["value"] == 0.12
    assert by_name["ms_image_stats.data.flag_fraction"]["flag"] == "OK"
    assert by_name["ms_image_stats.data.nested.dynamic_range"]["value"] == 250.0
    assert by_name["ms_image_stats.data.n_antennas"]["value"] == 27.0
    assert "ms_image_stats.data.name" not in by_name


def test_harvest_keeps_unavailable_flag():
    rows = harvest_metrics({"x": {"value": None, "flag": "UNAVAILABLE"}}, "t")
    assert rows == [{"name": "t.x", "value": None, "unit": None, "flag": "UNAVAILABLE"}]


def test_harvest_ignores_booleans():
    assert harvest_metrics({"ok": True}, "t") == []


def test_harvest_from_tool_calls_parses_text_blocks():
    calls = [
        {"tool": "ms_image_stats", "result": [{"type": "text", "text": json.dumps({"rms": 1.5})}]},
        {"tool": "broken", "result": "not json"},
    ]
    rows = harvest_from_tool_calls(calls)
    assert rows == [{"name": "ms_image_stats.rms", "value": 1.5, "unit": None, "flag": None}]


# --------------------------------------------------------- check_citations


def test_citation_mismatch_recorded_not_refused(tmp_path):
    (tmp_path / "measurements.json").write_text(json.dumps({"flag_fraction": 0.92}))
    out = check_citations(
        [{"name": "flag_fraction", "value": 0.12, "source": "measurements.json"}],
        tmp_path,
    )
    assert out[0]["cited_value"] == 0.12
    assert out[0]["found_value"] == 0.92
    assert out[0]["n_matches"] == 1


def test_citation_missing_source_recorded(tmp_path):
    out = check_citations([{"name": "x", "value": 1, "source": "absent.json"}], tmp_path)
    assert out[0]["found_value"] is None
    assert out[0]["error"] is not None


# ------------------------------------------------------------- render_brief


def test_render_brief_from_synthetic_status():
    run = {"ms_path": "/d/a.ms", "workdir": "/w", "telescope": "VLA"}
    payload = {"data": {"next_recommended_step": "apply_preflag"}}
    brief = render_brief(run, payload, None)
    assert "/d/a.ms" in brief
    assert "apply_preflag" in brief
    assert "first turn" in brief
    prev = {
        "stage": "import_asdm",
        "outcome": "accepted",
        "jobs": [{"exit_code": 0, "log_paths": ["/w/j.log"]}],
    }
    brief2 = render_brief(run, payload, prev)
    assert "import_asdm" in brief2


# ------------------------------------------------------------- executors


def _script(tmp_path, body="exit 0"):
    s = tmp_path / "job.sh"
    s.write_text(f"#!/bin/sh\n{body}\n")
    s.chmod(s.stat().st_mode | stat.S_IEXEC)
    return s


def test_local_executor_synchronous_success(tmp_path):
    ex = LocalExecutor(runner="/bin/sh")
    handle = ex.submit(_script(tmp_path, "echo hi; exit 0"), tmp_path / "job")
    assert ex.poll(handle) == "done"
    assert ex.exit_code(handle) == 0
    assert "hi" in Path(handle["log_paths"][0]).read_text()


def test_local_executor_failure(tmp_path):
    ex = LocalExecutor(runner="/bin/sh")
    handle = ex.submit(_script(tmp_path, "exit 3"), tmp_path / "job")
    assert ex.poll(handle) == "failed"
    assert ex.exit_code(handle) == 3


def test_local_crashed_turn_polls_failed():
    # A journal handle with no exit code: the driver died mid-job.
    ex = LocalExecutor()
    assert ex.poll({"executor": "local", "handle": "local:sync"}) == "failed"


def test_htcondor_is_a_stub(tmp_path):
    with pytest.raises(NotImplementedError):
        HTCondorExecutor().submit("s.py", tmp_path)


def test_make_executor_rejects_unknown():
    with pytest.raises(ValueError):
        make_executor("kubernetes")


def test_slurm_executor_state_mapping(monkeypatch, tmp_path):
    from analyst_driver import executors as ex_mod

    class FakeCompleted:
        def __init__(self, stdout, returncode=0):
            self.stdout = stdout
            self.returncode = returncode

    def fake_run(args, **kwargs):
        assert args[0] == "sacct"
        return FakeCompleted(fake_run.line + "\n")

    monkeypatch.setattr(ex_mod.subprocess, "run", fake_run)
    ex = ex_mod.SlurmExecutor(config=object())
    handle = {"job_id": "42"}
    for line, state, code in [
        ("PENDING|", "pending", None),
        ("RUNNING|", "running", None),
        ("COMPLETED|0:0", "done", 0),
        ("FAILED|1:0", "failed", 1),
        ("CANCELLED+|0:15", "failed", 0),
        ("TIMEOUT|0:1", "failed", 0),
    ]:
        fake_run.line = line
        assert ex.poll(handle) == state
        assert ex.exit_code(handle) == code


# -------------------------------------------------------------- backends


def test_claude_parse_stream_json():
    raw = "\n".join(
        [
            json.dumps({"type": "system", "subtype": "init", "model": "claude-sonnet-5"}),
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "tool_use", "id": "t1", "name": "ms_image_stats", "input": {}}
                        ]
                    },
                }
            ),
            json.dumps(
                {
                    "type": "user",
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "t1",
                                "content": [{"type": "text", "text": json.dumps({"rms": 2.0})}],
                            }
                        ]
                    },
                }
            ),
            json.dumps(
                {
                    "type": "result",
                    "result": '{"script": "s.py"}',
                    "usage": {"input_tokens": 100, "output_tokens": 20},
                }
            ),
        ]
    )
    res = ClaudeBackend.parse(raw)
    assert res.text == '{"script": "s.py"}'
    assert res.model == "claude-sonnet-5"
    assert res.tokens_in == 100 and res.tokens_out == 20
    assert res.tool_calls[0]["tool"] == "ms_image_stats"


def test_claude_parse_degrades_to_raw():
    res = ClaudeBackend.parse("plain text, not JSONL")
    assert res.text == "plain text, not JSONL"
    assert res.tool_calls == []


def test_opencode_parse_events():
    raw = "\n".join(
        [
            json.dumps({"part": {"type": "text", "text": "thinking..."}}),
            json.dumps(
                {
                    "part": {
                        "type": "tool",
                        "tool": "ms_scan_list",
                        "state": {"output": json.dumps({"n_scans": 12})},
                    }
                }
            ),
            json.dumps({"part": {"type": "text", "text": '{"script": "s.py"}'}}),
        ]
    )
    res = OpencodeBackend.parse(raw)
    assert '{"script": "s.py"}' in res.text
    assert res.tool_calls[0]["tool"] == "ms_scan_list"


def test_codex_parse_last_message():
    raw = "\n".join(
        [
            json.dumps({"item": {"type": "agent_message", "text": "working"}}),
            json.dumps({"item": {"type": "agent_message", "text": '{"script": "s.py"}'}}),
        ]
    )
    res = CodexBackend.parse(raw)
    assert res.text == '{"script": "s.py"}'


# ------------------------------------------------------- one turn, end to end


@pytest.fixture
def env(tmp_path, monkeypatch):
    db = DriverDB(tmp_path / "runs")
    workdir = tmp_path / "work"
    workdir.mkdir()
    key = "20260831T120000Z-sim-abcd"
    db.create_run(
        key,
        ms_path=str(tmp_path / "sim.ms"),
        workdir=str(workdir),
        telescope="VLA",
        backend="stub",
        executor="local",
    )
    payload = {"data": {"next_recommended_step": "apply_preflag"}}
    monkeypatch.setattr(Loop, "sense", lambda self, run: payload)
    yield db, key, workdir
    db.close()


def _loop(db, backend):
    return Loop(db, backend, LocalExecutor(runner="/bin/sh"), max_turns=5, poll_interval=0.01)


def test_one_turn_end_to_end(env, tmp_path):
    db, key, workdir = env
    script = _script(workdir, "echo done; exit 0")
    decision = {
        "script": str(script),
        "tool": "ms_apply_preflag",
        "stage": "apply_preflag",
        "outputs": [{"path": str(workdir / "nothing.G"), "kind": "caltable"}],
    }
    backend = StubBackend([f"reasoning...\n{json.dumps(decision)}"])
    res = _loop(db, backend).step(key)
    assert res == {"action": "completed", "ordinal": 1, "outcome": "accepted", "exit_code": 0}
    record = db._read_json(db._turn_json(key, 1))
    assert record["state"] == "complete"
    assert record["stage"] == "apply_preflag"
    # the script artifact was measured; the absent output recorded as absent
    kinds = {a["kind"]: a for a in record["artifacts"]}
    assert kinds["script"]["checksum"].startswith("sha256:")
    assert kinds["caltable"]["size"] is None
    # the brief reached the model
    assert "apply_preflag" in backend.calls[0]


def test_no_script_is_a_retryable_turn(env):
    db, key, _ = env
    backend = StubBackend(['{"tool": "ms_gaincal"}', "not json at all"])
    loop = _loop(db, backend)
    res1 = loop.step(key)
    assert res1["action"] == "turn_failed"
    assert "no script" in res1["reason"]
    res2 = loop.step(key)
    assert res2["action"] == "turn_failed"
    assert "did not parse" in res2["reason"]
    # both turns recorded, run still active
    assert db.next_ordinal(key) == 3
    assert db._read_json(db._run_json(key))["status"] == "active"


def test_citations_recorded_both_sides(env):
    db, key, workdir = env
    (workdir / "m.json").write_text(json.dumps({"flag_fraction": 0.92}))
    script = _script(workdir)
    decision = {
        "script": str(script),
        "cited": [{"name": "flag_fraction", "value": 0.12, "source": "m.json"}],
    }
    backend = StubBackend([json.dumps(decision)])
    _loop(db, backend).step(key)
    record = db._read_json(db._turn_json(key, 1))
    cite = record["citations"][0]
    assert cite["cited_value"] == 0.12
    assert cite["found_value"] == 0.92
    # recorded, and the job still ran
    assert record["outcome"] == "accepted"


def test_failed_job_outcome(env):
    db, key, workdir = env
    script = _script(workdir, "exit 7")
    backend = StubBackend([json.dumps({"script": str(script)})])
    res = _loop(db, backend).step(key)
    assert res["outcome"] == "failed"
    assert res["exit_code"] == 7


def test_max_turns_goes_needs_human(env):
    db, key, workdir = env
    script = _script(workdir)
    backend = StubBackend([json.dumps({"script": str(script)})] * 6)
    loop = _loop(db, backend)
    results = [loop.step(key) for _ in range(6)]
    assert results[-1]["action"] == "needs_human"
    assert db._read_json(db._run_json(key))["status"] == "needs_human"
    assert _loop(db, backend).step(key)["action"] == "skipped"


def test_resume_adopts_submitted_turn(env):
    db, key, workdir = env
    script = _script(workdir)
    # simulate a crash: a turn recorded as submitted, never completed
    db.record_turn(
        key,
        1,
        stage="apply_preflag",
        decision={"script": str(script)},
        jobs=[
            {
                "executor": "local",
                "handle": "local:sync",
                "exit_code": 0,
                "submitted_at": "2026-08-31T12:00:00Z",
                "finished_at": "2026-08-31T12:10:00Z",
                "log_paths": [],
            }
        ],
    )
    backend = StubBackend([])  # a fresh decision would exhaust the stub
    res = _loop(db, backend).step(key)
    assert res == {"action": "completed", "ordinal": 1, "outcome": "accepted", "exit_code": 0}
    record = db._read_json(db._turn_json(key, 1))
    assert record["wall_time_s"] == 600.0


# ------------------------------------------------ the model declares it done


def _done_loop(tmp_path, response):
    db = DriverDB(tmp_path / "runs")
    db.create_run("k1", ms_path=str(tmp_path / "a.ms"), workdir=str(tmp_path), executor="local")
    loop = Loop(db, StubBackend([response]), LocalExecutor(runner="/bin/sh"), poll_interval=0.01)
    loop.sense = lambda run: {"data": {"next_recommended_step": "selfcal_or_done"}}
    return db, loop


def test_done_decision_completes_the_run(tmp_path):
    db, loop = _done_loop(tmp_path, json.dumps({"done": True, "notes": "finished"}))
    result = loop.step("k1")
    assert result["action"] == "run_completed"
    assert db._read_json(db._run_json("k1"))["status"] == "completed"
    db.close()


def test_done_decision_is_journalled_as_a_turn(tmp_path):
    """The declaration is a recorded fact, so rebuild keeps it."""
    db, loop = _done_loop(tmp_path, json.dumps({"done": True, "notes": "finished"}))
    loop.step("k1")
    turn = db._read_json(db._turn_json("k1", 1))
    assert turn["outcome"] == "accepted"
    assert turn["jobs"] == []
    assert "declared the run complete" in turn["stop_reason"]
    db.close()


def test_done_false_is_not_a_completion(tmp_path):
    """Only an explicit true ends a run; a script must still be named."""
    db, loop = _done_loop(tmp_path, json.dumps({"done": False}))
    result = loop.step("k1")
    assert result["action"] == "turn_failed"
    assert db._read_json(db._run_json("k1"))["status"] == "active"
    db.close()


def test_completed_run_is_skipped_on_the_next_step(tmp_path):
    db, loop = _done_loop(tmp_path, json.dumps({"done": True}))
    loop.step("k1")
    assert loop.step("k1") == {"action": "skipped", "status": "completed"}
    db.close()


def test_run_all_stops_once_the_run_completes(tmp_path):
    db, loop = _done_loop(tmp_path, json.dumps({"done": True}))
    results = loop.run_all(["k1"])
    assert results["k1"]["action"] == "run_completed"
    db.close()


def test_brief_tells_the_model_how_to_declare_completion(tmp_path):
    run = {"ms_path": "/d/a.ms", "workdir": "/w", "telescope": "VLA"}
    brief = render_brief(run, {"data": {"next_recommended_step": "selfcal_or_done"}}, None)
    assert '"done": true' in brief
    assert "selfcal_or_done" in brief


def test_submitted_job_id_is_written_to_the_owner_file(tmp_path):
    """A SLURM job id on the owner file is what lets a later run adopt it."""
    db = DriverDB(tmp_path / "runs")
    db.create_run("k1", ms_path=str(tmp_path / "a.ms"), workdir=str(tmp_path), executor="slurm")
    write_owner(db._run_dir("k1"), executor="slurm")

    script = tmp_path / "s.sh"
    script.write_text("#!/bin/sh\nexit 0\n")
    seen = {}

    class FakeSlurm:
        kind = "slurm"

        def submit(self, script, job_dir):
            return {
                "executor": "slurm",
                "handle": "slurm:99",
                "job_id": "99",
                "submitted_at": "2026-08-31T12:00:00Z",
                "log_paths": [],
            }

        def poll(self, handle):
            seen["owner_mid_flight"] = read_owner(db._run_dir("k1"))
            return "done"

        def exit_code(self, handle):
            return 0

    loop = Loop(
        db, StubBackend([json.dumps({"script": str(script)})]), FakeSlurm(), poll_interval=0.01
    )
    loop.sense = lambda run: {"data": {"next_recommended_step": "apply_preflag"}}
    loop.step("k1")

    assert seen["owner_mid_flight"]["job_id"] == "99"
    # cleared once the turn settles, so a later probe does not chase a dead job
    assert read_owner(db._run_dir("k1"))["job_id"] is None
    db.close()
