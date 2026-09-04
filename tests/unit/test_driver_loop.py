"""Unit tests for analyst_driver.loop, executors and backends.

CASA-free: the loop is exercised with a stub backend, a shell-script "CASA
script", and the synchronous local executor. ``sense`` is monkeypatched with
a synthetic ms_workflow_status payload.
"""

import json
import stat
import subprocess
from pathlib import Path

import pytest

from analyst_driver.backends import (
    BackendResult,
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

FIXTURES = Path(__file__).parent / "fixtures"

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


def test_harvest_unwraps_the_real_mcp_double_encoding():
    """A captured envelope (G55 run, turn 5, ms_corrected_stats).

    ``result`` decodes once to ``{"result": "<json string>"}`` — the MCP
    bridge's own wrapper — not the tool's payload. Before the fix this
    produced zero rows on every real run despite passing hand-typed tests.
    """
    call = json.loads((FIXTURES / "g55_turn5_mcp_tool_call.json").read_text())
    rows = harvest_from_tool_calls([call])
    by_name = {r["name"]: r for r in rows}
    assert by_name["mcp__ms-inspect__ms_corrected_stats.data.per_field[0].amp_median"]["value"] == (
        0.158563
    )
    assert by_name["mcp__ms-inspect__ms_corrected_stats.data.per_field[1].phase_rms_deg"][
        "value"
    ] == 7.536


# --------------------------------------------------------- check_citations


def test_citation_mismatch_recorded_not_refused():
    """'decision cited flag_fraction=0.12; the tool said 0.92' is a fact kept, not a refusal."""
    tool_calls = [{"tool": "ms_flag_summary", "result": json.dumps({"flag_fraction": 0.92})}]
    out = check_citations(
        [{"name": "flag_fraction", "value": 0.12, "source": "ms_flag_summary"}],
        tool_calls,
    )
    assert out[0]["cited_value"] == 0.12
    assert out[0]["found_value"] == 0.92
    assert out[0]["n_matches"] == 1


def test_citation_not_found_in_any_tool_call_recorded():
    out = check_citations([{"name": "x", "value": 1, "source": "ms_flag_summary"}], [])
    assert out[0]["found_value"] is None
    assert out[0]["n_matches"] == 0


def test_citation_source_is_prose_not_a_path():
    """The model's own words, not a file path — the defect check_citations existed to fix.

    A real ``source`` value looks like "ms_field_list on calibrators.ms
    (field_id 4, OBSERVE_TARGET)": prose naming a tool call, never a path
    that resolves on disk. The check must not try to open it.
    """
    tool_calls = [{"tool": "ms_field_list", "result": json.dumps({"field_id": 4})}]
    out = check_citations(
        [
            {
                "name": "field_id",
                "value": 4,
                "source": "ms_field_list on calibrators.ms (field_id 4, OBSERVE_TARGET)",
            }
        ],
        tool_calls,
    )
    assert out[0]["found_value"] == 4
    assert out[0]["n_matches"] == 1


def test_citation_resolves_against_the_real_double_encoded_call():
    """Same fixture as the harvest test: the key exists once per field (6),
    so a citation naming it without a field qualifier finds all six and
    reports the first — this is the search, not a targeted lookup."""
    call = json.loads((FIXTURES / "g55_turn5_mcp_tool_call.json").read_text())
    out = check_citations(
        [{"name": "phase_rms_deg", "value": 7.536, "source": "ms_corrected_stats, field 1"}],
        [call],
    )
    assert out[0]["n_matches"] == 6
    assert out[0]["found_value"] == 104.794


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


def test_render_brief_states_the_declared_scope():
    run = {"ms_path": "/d/a.ms", "workdir": "/w", "telescope": "VLA"}
    payload = {"data": {"next_recommended_step": "apply_preflag"}}
    brief = render_brief(run, payload, None, "calibration + imaging, prefer awproject")
    assert "calibration + imaging, prefer awproject" in brief


def test_render_brief_default_scope_says_use_your_own_judgement():
    """No scope declared must not read as an empty, silently-missing line."""
    run = {"ms_path": "/d/a.ms", "workdir": "/w", "telescope": "VLA"}
    payload = {"data": {"next_recommended_step": "apply_preflag"}}
    brief = render_brief(run, payload, None)
    assert "not declared — use your own judgement" in brief


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


def test_loop_scope_reaches_the_backend_prompt(env, tmp_path):
    """Loop(scope=...) must survive to the actual brief, not just render_brief."""
    db, key, workdir = env
    script = _script(workdir, "exit 0")
    decision = {"script": str(script), "tool": "ms_apply_preflag", "stage": "apply_preflag"}
    backend = StubBackend([json.dumps(decision)])
    loop = Loop(
        db,
        backend,
        LocalExecutor(runner="/bin/sh"),
        poll_interval=0.01,
        scope="full-Stokes calibration + imaging",
    )
    loop.step(key)
    assert "full-Stokes calibration + imaging" in backend.calls[0]


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
    script = _script(workdir)
    decision = {
        "script": str(script),
        "cited": [
            {
                "name": "flag_fraction",
                "value": 0.12,
                "source": "ms_flag_summary on calibrators.ms",
            }
        ],
    }
    tool_calls = [[{"tool": "ms_flag_summary", "result": json.dumps({"flag_fraction": 0.92})}]]
    backend = StubBackend([json.dumps(decision)], tool_calls=tool_calls)
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
    # "stopped", not "completed": the driver never verifies the model's claim,
    # only that it made one — decision.notes carries what the model meant.
    assert db._read_json(db._run_json("k1"))["status"] == "stopped"
    db.close()


def test_done_decision_labels_the_turn_done_not_the_next_recommendation(tmp_path):
    """A terminal marker must not borrow ms_workflow_status's forward-looking
    label — that mislabels the halted-run case, where 'done' is how the
    model signals it cannot continue, not that it finished cleanly."""
    db, loop = _done_loop(tmp_path, json.dumps({"done": True, "notes": "halted, not complete"}))
    loop.step("k1")
    turn = db._read_json(db._turn_json("k1", 1))
    assert turn["stage"] == "done"
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


def test_stopped_run_is_skipped_on_the_next_step(tmp_path):
    db, loop = _done_loop(tmp_path, json.dumps({"done": True}))
    loop.step("k1")
    assert loop.step("k1") == {"action": "skipped", "status": "stopped"}
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


# ------------------------------------------------------- starting from an ASDM


def test_sense_probes_the_input_when_no_ms_exists_yet(tmp_path):
    """The whole ASDM path rests on this: no MS, no raise."""
    from ms_inspect.tools import workflow_status

    asdm = tmp_path / "uid___A002_X1"
    asdm.mkdir()
    (asdm / "ASDM.xml").write_text("<x/>")
    payload = workflow_status.run(str(asdm), str(tmp_path))
    assert payload["data"]["next_recommended_step"] == "import_asdm"
    assert payload["data"]["ms_valid"]["value"] is False
    assert any("not a Measurement Set" in w for w in payload["warnings"])


def test_workflow_status_on_a_missing_path_is_the_import_stage(tmp_path):
    from ms_inspect.tools import workflow_status

    payload = workflow_status.run(str(tmp_path / "nothing"), str(tmp_path))
    assert payload["data"]["next_recommended_step"] == "import_asdm"
    assert any("does not exist" in w for w in payload["warnings"])


def test_loop_senses_an_asdm_run_through_input_path(tmp_path):
    asdm = tmp_path / "uid___A002_X1"
    asdm.mkdir()
    db = DriverDB(tmp_path / "runs")
    db.create_run("k1", input_path=str(asdm), ms_path="", workdir=str(tmp_path))
    loop = Loop(db, StubBackend([]), LocalExecutor(), poll_interval=0.01)
    payload = loop.sense(db._read_json(db._run_json("k1")))
    assert payload["data"]["next_recommended_step"] == "import_asdm"
    db.close()


def test_brief_names_the_input_and_the_create_server(tmp_path):
    run = {"input_path": "/data/uid___A002_X1", "ms_path": "", "workdir": "/w", "telescope": "ALMA"}
    brief = render_brief(run, {"data": {"next_recommended_step": "import_asdm"}}, None)
    assert "/data/uid___A002_X1" in brief
    assert "ms_import_asdm" in brief and "ms_create" in brief
    assert "not imported yet" in brief


def test_import_turn_teaches_the_run_where_the_ms_is(tmp_path):
    """After the import job, ms_path comes from the turn's "ms" output."""
    asdm = tmp_path / "uid___A002_X1"
    asdm.mkdir()
    ms = tmp_path / "out.ms"
    script = tmp_path / "import.sh"
    # the "job" is what creates the MS, exactly as importasdm would
    script.write_text(f"#!/bin/sh\nmkdir -p {ms}\ntouch {ms}/table.info\n")

    db = DriverDB(tmp_path / "runs")
    db.create_run("k1", input_path=str(asdm), ms_path="", workdir=str(tmp_path), executor="local")
    decision = json.dumps(
        {
            "script": str(script),
            "tool": "ms_import_asdm",
            "stage": "import_asdm",
            "outputs": [{"path": str(ms), "kind": "ms"}],
        }
    )
    loop = Loop(db, StubBackend([decision]), LocalExecutor(runner="/bin/sh"), poll_interval=0.01)
    loop.sense = lambda run: {"data": {"next_recommended_step": "import_asdm"}}
    assert loop.step("k1")["outcome"] == "accepted"
    assert db._read_json(db._run_json("k1"))["ms_path"] == str(ms)
    db.close()


def test_ms_is_not_adopted_when_the_script_wrote_nothing(tmp_path):
    """A claimed output that does not exist must not become the run's MS."""
    script = tmp_path / "import.sh"
    script.write_text("#!/bin/sh\nexit 0\n")
    db = DriverDB(tmp_path / "runs")
    db.create_run(
        "k1", input_path=str(tmp_path / "asdm"), ms_path="", workdir=str(tmp_path), executor="local"
    )
    decision = json.dumps(
        {
            "script": str(script),
            "tool": "ms_import_asdm",
            "stage": "import_asdm",
            "outputs": [{"path": str(tmp_path / "never_written.ms"), "kind": "ms"}],
        }
    )
    loop = Loop(db, StubBackend([decision]), LocalExecutor(runner="/bin/sh"), poll_interval=0.01)
    loop.sense = lambda run: {"data": {"next_recommended_step": "import_asdm"}}
    loop.step("k1")
    assert db._read_json(db._run_json("k1"))["ms_path"] == ""
    db.close()


def test_an_existing_ms_path_is_never_overwritten(tmp_path):
    """A later stage naming an "ms" output must not repoint the run."""
    ms = tmp_path / "real.ms"
    ms.mkdir()
    (ms / "table.info").write_text("x")
    other = tmp_path / "split.ms"
    other.mkdir()
    script = tmp_path / "s.sh"
    script.write_text("#!/bin/sh\nexit 0\n")

    db = DriverDB(tmp_path / "runs")
    db.create_run(
        "k1", input_path=str(ms), ms_path=str(ms), workdir=str(tmp_path), executor="local"
    )
    decision = json.dumps(
        {
            "script": str(script),
            "stage": "apply_preflag",
            "outputs": [{"path": str(other), "kind": "ms"}],
        }
    )
    loop = Loop(db, StubBackend([decision]), LocalExecutor(runner="/bin/sh"), poll_interval=0.01)
    loop.sense = lambda run: {"data": {"next_recommended_step": "apply_preflag"}}
    loop.step("k1")
    assert db._read_json(db._run_json("k1"))["ms_path"] == str(ms)
    db.close()


# ------------------------------------------------------ backend permissions


def test_claude_backend_passes_allowed_tools():
    args = ClaudeBackend(allowed_tools=["mcp__ms-create", "Read"])._args()
    assert "--allowedTools" in args
    assert args[args.index("--allowedTools") + 1] == "mcp__ms-create,Read"


def test_claude_backend_omits_the_flag_when_unset():
    assert "--allowedTools" not in ClaudeBackend()._args()


def test_claude_backend_empty_list_omits_the_flag():
    assert "--allowedTools" not in ClaudeBackend(allowed_tools=[])._args()


def test_claude_backend_never_puts_the_prompt_in_argv():
    """--allowedTools is variadic: a trailing positional is eaten as a tool.

    The earlier version of this test asserted the prompt was the LAST argument,
    which is exactly the arrangement that broke. The prompt goes on stdin.
    """
    args = ClaudeBackend(allowed_tools=["Read"])._args()
    assert not any("prompt" in a for a in args)
    # --disallowedTools now follows the allow list, so the allow list is no
    # longer last. The property that matters is unchanged: no argv entry is the
    # prompt, because --allowedTools is variadic and would swallow it.
    assert "Read" in args[args.index("--allowedTools") + 1]


def test_claude_backend_sends_the_prompt_on_stdin(tmp_path, monkeypatch):
    """The launch test: the prompt must actually reach the process."""
    seen = {}

    def fake_run(args, **kw):
        seen["args"] = args
        seen["input"] = kw.get("input")
        return subprocess.CompletedProcess(args, 0, stdout='{"type":"result"}', stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    ClaudeBackend(allowed_tools=["Read"]).run("THE BRIEF", tmp_path)
    assert seen["input"] == "THE BRIEF"
    assert "THE BRIEF" not in seen["args"]


def test_backend_failure_is_recorded_not_swallowed(tmp_path, monkeypatch):
    """A harness that exits non-zero must not read as an empty answer."""

    def fake_run(args, **kw):
        return subprocess.CompletedProcess(
            args, 1, stdout="", stderr="Error: Input must be provided"
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    res = ClaudeBackend().run("brief", tmp_path)
    assert res.exit_code == 1
    assert "Input must be provided" in res.error


def test_zero_exit_with_no_output_is_still_a_failure(tmp_path, monkeypatch):
    def fake_run(args, **kw):
        return subprocess.CompletedProcess(args, 0, stdout="   \n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    res = ClaudeBackend().run("brief", tmp_path)
    assert res.error == "exited 0 with no output"


def test_successful_run_records_no_error(tmp_path, monkeypatch):
    def fake_run(args, **kw):
        return subprocess.CompletedProcess(
            args, 0, stdout='{"type":"result","result":"{\\"done\\": true}"}', stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    res = ClaudeBackend().run("brief", tmp_path)
    assert res.error is None and res.exit_code == 0


def test_turn_reports_the_backend_failure_reason(tmp_path):
    """The stop_reason must name the harness, not blame the decision."""

    class BrokenBackend:
        kind = "claude"

        def run(self, prompt, workdir):
            return BackendResult(text="", error="Error: Input must be provided", exit_code=1)

    db = DriverDB(tmp_path / "runs")
    db.create_run(
        "k1", input_path=str(tmp_path / "a.ms"), ms_path="", workdir=str(tmp_path), executor="local"
    )
    loop = Loop(db, BrokenBackend(), LocalExecutor(), poll_interval=0.01)
    loop.sense = lambda run: {"data": {"next_recommended_step": "import_asdm"}}
    result = loop.step("k1")

    assert result["action"] == "turn_failed"
    assert "backend claude failed (exit 1)" in result["reason"]
    assert "Input must be provided" in result["reason"]

    turn = db._read_json(db._turn_json("k1", 1))
    assert turn["backend_exit_code"] == 1
    assert "Input must be provided" in turn["backend_error"]
    db.close()


# ---------------------------------------------------------------------------
# Free space in the brief
# ---------------------------------------------------------------------------
#
# The G55 run halted when applycal_target aborted mid-write on a full
# filesystem and left a partial main table. Nothing in the brief had said the
# disk was nearly full. This is reported as a measured number with no threshold
# and no refusal — the driver may report a number, it may never name a verdict.


def test_free_bytes_reports_a_real_number(tmp_path):
    from analyst_driver.loop import free_bytes

    n = free_bytes(tmp_path)
    assert isinstance(n, int)
    assert n > 0


def test_free_bytes_of_a_missing_directory_is_none(tmp_path):
    """It must not raise: this runs on every turn, before every submit."""
    from analyst_driver.loop import free_bytes

    assert free_bytes(tmp_path / "does_not_exist") is None


def test_the_brief_carries_free_space(tmp_path):
    from analyst_driver.loop import render_brief

    brief = render_brief(
        {"workdir": str(tmp_path), "ms_path": "/d/x.ms", "input_path": "/d/x.asdm",
         "telescope": "EVLA"},
        {"data": {}},
        None,
    )
    assert "Free space in the work directory:" in brief
    assert " GB)" in brief


def test_the_brief_states_no_threshold_and_no_verdict(tmp_path):
    """Guards the design rule, not the formatting: if someone later adds
    'WARNING: low disk' here, the driver has started naming verdicts."""
    from analyst_driver.loop import render_brief

    brief = render_brief(
        {"workdir": str(tmp_path), "ms_path": "/d/x.ms", "input_path": "/d/x.asdm",
         "telescope": "EVLA"},
        {"data": {}},
        None,
    )
    line = next(ln for ln in brief.splitlines() if ln.startswith("Free space"))
    assert not any(w in line.lower() for w in ("warning", "low", "insufficient", "too little"))


def test_unreadable_free_space_is_stated_not_omitted(tmp_path):
    """A missing line would read as 'plenty of room' rather than 'unknown'."""
    from analyst_driver.loop import render_brief

    brief = render_brief(
        {"workdir": str(tmp_path / "gone"), "ms_path": "/d/x.ms", "input_path": "/d/x.asdm",
         "telescope": "EVLA"},
        {"data": {}},
        None,
    )
    assert "unavailable" in brief
