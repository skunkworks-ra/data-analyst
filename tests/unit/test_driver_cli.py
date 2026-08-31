"""Unit tests for analyst_driver.cli.

The verbs: ``init`` scaffolds config.toml only; ``run`` registers a run for an
MS and drives it; ``step`` does one turn. Both driving verbs pass through the
ownership gate first. The stub backend and a /bin/sh script stand in for the
model and CASA, so this is the PLAN.md "stub-backend dry run".
"""

import json
import os
import stat
import subprocess
from datetime import UTC, datetime

import pytest

from analyst_driver.cli import DEFAULT_CONFIG, main
from analyst_driver.db import DriverDB, make_run_key
from analyst_driver.loop import Loop
from analyst_driver.owner import owner_path, read_owner, write_owner

DONE = json.dumps({"done": True, "notes": "reduction finished"})


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "work").mkdir()
    monkeypatch.setattr(
        Loop,
        "sense",
        lambda self, run: {"data": {"next_recommended_step": "apply_preflag"}},
    )
    return tmp_path


def write_config(project, responses=()):
    body = ",\n  ".join(json.dumps(r) for r in responses)
    (project / "config.toml").write_text(
        f'''
[driver]
run_root = "{project / "runs"}"
max_turns = 4
poll_interval = 0.01

[backend]
kind = "stub"
responses = [
  {body}
]

[executor]
kind = "local"
runner = "/bin/sh"
'''
    )
    return project / "config.toml"


def _cli(project, *args):
    return main(["--config", str(project / "config.toml"), *args])


def _stage_script(project):
    script = project / "work" / "stage.sh"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return json.dumps({"script": str(script), "tool": "ms_apply_preflag", "stage": "apply_preflag"})


def _new_run(project, name="a.ms", status="active", now=None):
    db = DriverDB(project / "runs")
    # make_run_key stamps whole seconds, so two runs made in the same second
    # collide on the UNIQUE run_key. Tests that need two pass distinct times.
    key = make_run_key(project / name, now=now)
    db.create_run(
        key,
        ms_path=str((project / name).absolute()),
        workdir=str(project / "work"),
        executor="local",
        status=status,
    )
    db.close()
    return key


# ----------------------------------------------------------------------- init


def test_init_writes_the_template(project, capsys):
    assert _cli(project, "init") == 0
    cfg = (project / "config.toml").read_text()
    assert cfg == DEFAULT_CONFIG
    assert 'run_root = "runs"' in cfg
    assert "[backend]" in cfg and "[executor]" in cfg
    assert "wrote" in capsys.readouterr().out


def test_init_does_not_register_a_run(project):
    _cli(project, "init")
    assert not (project / "runs").exists()


def test_init_never_overwrites(project, capsys):
    _cli(project, "init")
    (project / "config.toml").write_text("# edited by hand\n")
    assert _cli(project, "init") == 0
    assert (project / "config.toml").read_text() == "# edited by hand\n"
    assert "already exists" in capsys.readouterr().err


def test_missing_config_is_refused_not_defaulted(project, capsys):
    """A mistyped --config used to run silently on code defaults."""
    assert _cli(project, "status") == 1
    assert "Run 'analyst-driver init'" in capsys.readouterr().err


# ------------------------------------------------------- run: register + drive


def test_run_registers_then_drives_to_completion(project, capsys):
    write_config(project, [_stage_script(project), DONE])
    assert (
        _cli(
            project,
            "run",
            "--ms",
            str(project / "a.ms"),
            "--workdir",
            str(project / "work"),
            "--telescope",
            "VLA",
        )
        == 0
    )
    out = capsys.readouterr().out.splitlines()
    run_key = out[0].strip()
    assert "-a-" in run_key
    results = json.loads(out[-1])
    assert results[run_key]["action"] == "run_completed"

    db = DriverDB(project / "runs")
    status = db.conn.execute("SELECT status FROM runs WHERE run_key = ?", (run_key,)).fetchone()[0]
    db.close()
    assert status == "completed"


def test_completed_run_is_not_re_driven(project, capsys):
    """The point of the completed status: bare `run` must let it alone."""
    write_config(project, [_stage_script(project), DONE])
    _cli(project, "run", "--ms", str(project / "a.ms"), "--workdir", str(project / "work"))
    capsys.readouterr()
    assert _cli(project, "run") == 1
    assert "no active runs" in capsys.readouterr().err


def test_run_on_the_same_ms_resumes_rather_than_duplicating(project, capsys):
    key = _new_run(project)
    write_config(project, [DONE])
    assert (
        _cli(project, "run", "--ms", str(project / "a.ms"), "--workdir", str(project / "work")) == 0
    )
    out = capsys.readouterr().out
    assert key in out
    db = DriverDB(project / "runs")
    assert db.conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
    db.close()


def test_two_active_runs_on_one_ms_are_refused(project, capsys):
    k1 = _new_run(project, now=datetime(2026, 8, 31, 10, 0, 0, tzinfo=UTC))
    k2 = _new_run(project, now=datetime(2026, 8, 31, 11, 0, 0, tzinfo=UTC))
    assert k1 != k2
    write_config(project, [DONE])
    assert (
        _cli(project, "run", "--ms", str(project / "a.ms"), "--workdir", str(project / "work")) == 1
    )
    err = capsys.readouterr().err
    assert k1 in err and k2 in err


def test_ms_without_workdir_is_refused(project, capsys):
    write_config(project)
    assert _cli(project, "run", "--ms", str(project / "a.ms")) == 1
    assert "must be given together" in capsys.readouterr().err


# --------------------------------------------------------------- the gate


def test_live_driver_refuses_and_resume_does_not_override(project, capsys):
    key = _new_run(project)
    write_config(project, [DONE])
    proc = subprocess.Popen(["sleep", "30"])
    try:
        write_owner(project / "runs" / key, executor="local", pid=proc.pid)
        assert _cli(project, "run", "--run", key) == 2
        assert "already running" in capsys.readouterr().err
        assert _cli(project, "run", "--run", key, "--resume") == 2
    finally:
        proc.kill()
        proc.wait()


def test_interrupted_run_needs_resume(project, capsys):
    key = _new_run(project)
    write_config(project, [DONE])
    proc = subprocess.Popen(["true"])
    proc.wait()
    write_owner(project / "runs" / key, executor="local", pid=proc.pid)

    assert _cli(project, "run", "--run", key) == 3
    assert "Pass --resume" in capsys.readouterr().err

    assert _cli(project, "run", "--run", key, "--resume") == 0


def test_interrupted_slurm_run_names_its_job(project, capsys):
    key = _new_run(project)
    write_config(project, [DONE])
    proc = subprocess.Popen(["true"])
    proc.wait()
    write_owner(project / "runs" / key, executor="slurm", job_id="1884213", pid=proc.pid)
    # executor in config is local, so no sacct call is attempted; the job
    # reads as unknown and the message still names it as interrupted.
    assert _cli(project, "run", "--run", key) == 3
    assert "Pass --resume" in capsys.readouterr().err


def test_owner_file_is_released_after_a_run(project, capsys):
    write_config(project, [DONE])
    _cli(project, "run", "--ms", str(project / "a.ms"), "--workdir", str(project / "work"))
    run_key = capsys.readouterr().out.splitlines()[0].strip()
    assert not owner_path(project / "runs" / run_key).exists()


def test_owner_file_is_released_when_a_turn_raises(project, monkeypatch, capsys):
    key = _new_run(project)
    write_config(project, [DONE])

    def boom(self, run_key, block=True):
        raise RuntimeError("backend exploded")

    monkeypatch.setattr(Loop, "step", boom)
    with pytest.raises(RuntimeError):
        _cli(project, "step", "--run", key)
    assert read_owner(project / "runs" / key) is None


def test_owner_recorded_while_a_turn_runs(project, monkeypatch):
    key = _new_run(project)
    write_config(project, [DONE])
    seen = {}

    real = Loop.step

    def spy(self, run_key, block=True):
        seen["owner"] = read_owner(self.db._run_dir(run_key))
        return real(self, run_key, block=block)

    monkeypatch.setattr(Loop, "step", spy)
    _cli(project, "step", "--run", key)
    assert seen["owner"]["pid"] == os.getpid()


# ------------------------------------------------------------ status, rebuild


def test_status_reports_the_owner(project, capsys):
    key = _new_run(project)
    write_config(project)
    proc = subprocess.Popen(["sleep", "30"])
    try:
        write_owner(project / "runs" / key, executor="local", pid=proc.pid)
        assert _cli(project, "status") == 0
        out = capsys.readouterr().out
        assert "owner: alive" in out and "status=active" in out
    finally:
        proc.kill()
        proc.wait()


def test_stub_dry_run_then_rebuild(project, capsys):
    """PLAN.md 'stub-backend dry run', then rebuild on its output."""
    write_config(project, [_stage_script(project), DONE])
    _cli(project, "run", "--ms", str(project / "a.ms"), "--workdir", str(project / "work"))
    capsys.readouterr()

    db = DriverDB(project / "runs")
    before = db.dump()
    assert _cli(project, "rebuild") == 0
    counts = json.loads(capsys.readouterr().out)
    assert counts == {"runs": 1, "turns": 2}
    db2 = DriverDB(project / "runs")
    assert db2.dump() == before
    db.close()
    db2.close()


def test_run_with_no_active_runs(project, capsys):
    write_config(project)
    assert _cli(project, "run") == 1
    assert "no active runs" in capsys.readouterr().err


# ------------------------------------------------------------ ASDM as input


def test_run_registers_an_asdm_with_no_ms_yet(project, capsys):
    asdm = project / "uid___A002_X1"
    asdm.mkdir()
    (asdm / "ASDM.xml").write_text("<x/>")
    write_config(project, [DONE])
    assert _cli(project, "run", "--input", str(asdm), "--workdir", str(project / "work")) == 0
    run_key = capsys.readouterr().out.splitlines()[0].strip()

    db = DriverDB(project / "runs")
    row = db.conn.execute(
        "SELECT input_path, ms_path FROM runs WHERE run_key = ?", (run_key,)
    ).fetchone()
    db.close()
    assert row[0] == str(asdm.absolute())
    assert row[1] == ""


def test_ms_flag_still_works_as_an_alias(project, capsys):
    ms = project / "a.ms"
    ms.mkdir()
    (ms / "table.info").write_text("x")
    write_config(project, [DONE])
    assert _cli(project, "run", "--ms", str(ms), "--workdir", str(project / "work")) == 0
    run_key = capsys.readouterr().out.splitlines()[0].strip()
    db = DriverDB(project / "runs")
    row = db.conn.execute(
        "SELECT input_path, ms_path FROM runs WHERE run_key = ?", (run_key,)
    ).fetchone()
    db.close()
    assert row[0] == row[1] == str(ms.absolute())


def test_second_run_on_the_same_asdm_resumes(project, capsys):
    asdm = project / "uid___A002_X1"
    asdm.mkdir()
    write_config(project, [DONE, DONE])
    _cli(project, "run", "--input", str(asdm), "--workdir", str(project / "work"))
    first = capsys.readouterr().out.splitlines()[0].strip()

    db = DriverDB(project / "runs")
    db.set_run_status(first, "active")
    db.close()

    assert _cli(project, "run", "--input", str(asdm), "--workdir", str(project / "work")) == 0
    assert first in capsys.readouterr().out
    db = DriverDB(project / "runs")
    assert db.conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
    db.close()


def test_input_without_workdir_is_refused(project, capsys):
    write_config(project)
    assert _cli(project, "run", "--input", str(project / "x")) == 1
    assert "must be given together" in capsys.readouterr().err
