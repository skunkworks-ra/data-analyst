"""
Unit tests for driver/executors.py and driver/backends.py.

The executor contract is that job state lives in FILES, not in process memory.
The driver exits between ticks, so a pid held in RAM would be lost — a restart
must pick a running job back up with no special case.
"""

from __future__ import annotations

import json
import time

import backends
import executors
import pytest


def step_dir(run_dir, name="001-x"):
    d = run_dir / "steps" / name
    d.mkdir(parents=True)
    return d


def script(d, body="import sys; sys.exit(0)"):
    p = d / "job.py"
    p.write_text(body)
    return p


# -- construction --------------------------------------------------------


@pytest.mark.parametrize(
    "kind,cls",
    [
        ("local", executors.LocalExecutor),
        ("slurm", executors.SlurmExecutor),
        ("dry", executors.DryExecutor),
    ],
)
def test_build_returns_the_named_executor(kind, cls):
    assert isinstance(executors.build({"kind": kind, "slurm": {}}), cls)


def test_build_refuses_an_unknown_kind():
    with pytest.raises(ValueError, match="unknown executor kind"):
        executors.build({"kind": "carrier-pigeon"})


def test_every_executor_exposes_the_same_interface():
    for ex in (executors.LocalExecutor(), executors.SlurmExecutor({}), executors.DryExecutor()):
        for method in ("submit", "poll", "exit_code"):
            assert callable(getattr(ex, method)), f"{ex.kind} is missing {method}"
        assert isinstance(ex.kind, str)


# -- the dry executor ----------------------------------------------------


def test_dry_reports_success_at_once(run_dir):
    d = step_dir(run_dir)
    ex = executors.DryExecutor()
    job = ex.submit(script(d), d, "x")
    assert ex.poll(job, d) == executors.DONE
    assert ex.exit_code(d) == 0


def test_dry_does_not_run_the_script(run_dir):
    d = step_dir(run_dir)
    marker = run_dir / "should_not_exist"
    ex = executors.DryExecutor()
    ex.submit(script(d, f"open({str(marker)!r}, 'w').write('ran')"), d, "x")
    assert not marker.exists()


def test_dry_still_writes_the_files_the_driver_harvests(run_dir):
    d = step_dir(run_dir)
    executors.DryExecutor().submit(script(d), d, "x")
    for name in (executors.RC_NAME, executors.STDOUT_NAME, executors.STDERR_NAME):
        assert (d / name).exists()


# -- the local executor --------------------------------------------------


def wait_for_rc(ex, job, d, timeout=20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = ex.poll(job, d)
        if status != executors.RUNNING:
            return status
        time.sleep(0.05)
    pytest.fail("job never finished")


def test_local_runs_a_script_and_reports_done(run_dir):
    d = step_dir(run_dir)
    ex = executors.LocalExecutor()
    job = ex.submit(script(d, "print('hello')"), d, "x")
    assert wait_for_rc(ex, job, d) == executors.DONE
    assert ex.exit_code(d) == 0
    assert "hello" in (d / executors.STDOUT_NAME).read_text()


def test_local_reports_a_failure_with_its_exit_code(run_dir):
    d = step_dir(run_dir)
    ex = executors.LocalExecutor()
    job = ex.submit(script(d, "import sys; sys.exit(3)"), d, "x")
    assert wait_for_rc(ex, job, d) == executors.FAILED
    assert ex.exit_code(d) == 3


def test_local_captures_stderr(run_dir):
    d = step_dir(run_dir)
    ex = executors.LocalExecutor()
    job = ex.submit(script(d, "import sys; print('bad', file=sys.stderr); sys.exit(1)"), d, "x")
    wait_for_rc(ex, job, d)
    assert "bad" in (d / executors.STDERR_NAME).read_text()


def test_local_reports_running_before_the_job_ends(run_dir):
    d = step_dir(run_dir)
    ex = executors.LocalExecutor()
    job = ex.submit(script(d, "import time; time.sleep(5)"), d, "x")
    assert ex.poll(job, d) == executors.RUNNING


def test_poll_survives_a_driver_restart(run_dir):
    """A fresh executor object, as a new driver process would build, must still
    see the result. This is why rc lives on disk rather than in a pid."""
    d = step_dir(run_dir)
    job = executors.LocalExecutor().submit(script(d, "print('x')"), d, "x")
    assert wait_for_rc(executors.LocalExecutor(), job, d) == executors.DONE


def test_a_stale_rc_from_an_earlier_attempt_is_cleared(run_dir):
    """A redo into the same step directory must not read the old result."""
    d = step_dir(run_dir)
    (d / executors.RC_NAME).write_text("0\n")
    ex = executors.LocalExecutor()
    job = ex.submit(script(d, "import sys; sys.exit(9)"), d, "x")
    assert wait_for_rc(ex, job, d) == executors.FAILED
    assert ex.exit_code(d) == 9


def test_exit_code_is_none_while_the_job_runs(run_dir):
    d = step_dir(run_dir)
    executors.LocalExecutor().submit(script(d, "import time; time.sleep(5)"), d, "x")
    assert executors.LocalExecutor().exit_code(d) is None


# -- the slurm executor (no scheduler needed) ----------------------------


def test_slurm_writes_a_batch_file_with_the_rc_line(run_dir, monkeypatch):
    """The rc file is what poll() trusts, so the job itself must write it —
    otherwise a scheduler-killed job would look like it never finished."""
    d = step_dir(run_dir)
    calls = {}

    class FakeCompleted:
        stdout = "12345;cluster"

    def fake_run(cmd, **kw):
        calls["cmd"] = cmd
        return FakeCompleted()

    monkeypatch.setattr(executors.subprocess, "run", fake_run)
    ex = executors.SlurmExecutor({"partition": "batch", "mem": "8G", "modules": ["casa"]})
    job = ex.submit(script(d), d, "run-001")

    assert job == "slurm:12345"
    body = (d / "job.sbatch").read_text()
    assert "#SBATCH --job-name=run-001" in body
    assert "#SBATCH --partition=batch" in body
    assert "#SBATCH --mem=8G" in body
    assert "module load casa" in body
    assert f"echo $? > {executors.RC_NAME}" in body
    assert calls["cmd"][0] == "sbatch"


def test_slurm_treats_completed_without_an_rc_as_a_failure(run_dir, monkeypatch):
    """sacct says COMPLETED but the job never wrote rc — it died before its
    last line. A silent success there would poison the ledger."""
    d = step_dir(run_dir)

    class Out:
        stdout = "COMPLETED\n"

    monkeypatch.setattr(executors.subprocess, "run", lambda *a, **k: Out())
    assert executors.SlurmExecutor({}).poll("slurm:1", d) == executors.FAILED


def test_slurm_reads_the_rc_file_in_preference_to_sacct(run_dir, monkeypatch):
    d = step_dir(run_dir)
    (d / executors.RC_NAME).write_text("0\n")

    def explode(*a, **k):
        raise AssertionError("sacct must not be called once rc exists")

    monkeypatch.setattr(executors.subprocess, "run", explode)
    assert executors.SlurmExecutor({}).poll("slurm:1", d) == executors.DONE


@pytest.mark.parametrize(
    "state,expected",
    [
        ("PENDING", executors.RUNNING),
        ("RUNNING", executors.RUNNING),
        ("CONFIGURING", executors.RUNNING),
        ("", executors.RUNNING),
        ("FAILED", executors.FAILED),
        ("TIMEOUT", executors.FAILED),
        ("CANCELLED", executors.FAILED),
    ],
)
def test_slurm_maps_sacct_states(run_dir, monkeypatch, state, expected):
    d = step_dir(run_dir)

    class Out:
        stdout = state + "\n" if state else ""

    monkeypatch.setattr(executors.subprocess, "run", lambda *a, **k: Out())
    assert executors.SlurmExecutor({}).poll("slurm:1", d) == expected


# -- backends ------------------------------------------------------------


def test_the_prompt_puts_the_stable_contract_first(run_dir):
    (run_dir / "PROMPT.md").write_text("CONTRACT")
    (run_dir / "BRIEF.md").write_text("BRIEF BODY")
    text = backends.build_prompt(run_dir, run_dir / "PROMPT.md", run_dir / "BRIEF.md").read_text()
    assert text.index("CONTRACT") < text.index("BRIEF BODY")


def stub_cfg(run_dir, body: str) -> dict:
    """A backend that is a real subprocess writing a real file."""
    stub = run_dir / "stub.py"
    stub.write_text(body)
    return {
        "kind": "stub",
        "stub": {"cmd": f"python {stub} {{decision}}", "stdin": "", "timeout_s": 60},
    }


def test_backend_reads_the_decision_from_a_file_not_from_stdout(run_dir):
    """The whole reason a backend swap needs no code change."""
    decision = run_dir / "decisions" / "001.json"
    prompt = run_dir / "turn.md"
    prompt.write_text("go")
    cfg = stub_cfg(
        run_dir,
        'import sys\nopen(sys.argv[1], \'w\').write(\'{"action": "done", "rationale": "ok"}\')\n',
    )
    backends.run_model(cfg, run_dir, prompt, decision)
    assert json.loads(decision.read_text())["action"] == "done"


def test_backend_reports_a_bad_command_template_clearly(run_dir):
    """A literal brace in the operator's cmd must not surface as an IndexError."""
    cfg = {"kind": "stub", "stub": {"cmd": "python -c 'print({})'", "stdin": ""}}
    with pytest.raises(backends.BackendError, match="not a valid template"):
        backends.run_model(cfg, run_dir, run_dir / "p.md", run_dir / "d.json")


def test_backend_errors_when_no_decision_is_written(run_dir):
    decision = run_dir / "decisions" / "001.json"
    prompt = run_dir / "turn.md"
    prompt.write_text("go")
    cfg = {"kind": "stub", "stub": {"cmd": "python -c pass", "stdin": "", "timeout_s": 60}}
    with pytest.raises(backends.BackendError, match="without writing"):
        backends.run_model(cfg, run_dir, prompt, decision)


def test_backend_errors_when_the_decision_is_left_unchanged(run_dir):
    """A model that exits without touching a decision file from a previous
    attempt must not have the stale file accepted as its answer."""
    decision = run_dir / "decisions" / "001.json"
    decision.write_text('{"action": "done", "rationale": "from the last attempt"}')
    prompt = run_dir / "turn.md"
    prompt.write_text("go")
    cfg = {"kind": "stub", "stub": {"cmd": "python -c pass", "stdin": "", "timeout_s": 60}}
    with pytest.raises(backends.BackendError, match="unchanged"):
        backends.run_model(cfg, run_dir, prompt, decision)


def test_backend_errors_on_a_missing_command(run_dir):
    prompt = run_dir / "turn.md"
    prompt.write_text("go")
    cfg = {"kind": "nope", "nope": {"cmd": "definitely-not-a-real-binary", "stdin": ""}}
    with pytest.raises(backends.BackendError, match="not found"):
        backends.run_model(cfg, run_dir, prompt, run_dir / "decisions" / "001.json")


def test_backend_errors_on_an_unconfigured_kind(run_dir):
    with pytest.raises(backends.BackendError, match="no \\[backend"):
        backends.run_model({"kind": "ghost"}, run_dir, run_dir / "p.md", run_dir / "d.json")


def test_backend_keeps_the_transcript(run_dir):
    decision = run_dir / "decisions" / "001.json"
    prompt = run_dir / "turn.md"
    prompt.write_text("go")
    cfg = stub_cfg(
        run_dir,
        "import sys\nprint('thinking')\nopen(sys.argv[1], 'w').write('{}')\n",
    )
    backends.run_model(cfg, run_dir, prompt, decision)
    assert "thinking" in (run_dir / "last_backend_stdout.txt").read_text()
