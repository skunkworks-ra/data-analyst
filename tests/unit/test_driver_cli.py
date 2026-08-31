"""Unit tests for analyst_driver.cli — init, status, rebuild, and the
stub-backend dry run through `run` (the loop without CASA or a model)."""

import json
import stat

import pytest

from analyst_driver.cli import main
from analyst_driver.db import DriverDB
from analyst_driver.loop import Loop


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "config.toml").write_text(
        f'''
[driver]
run_root = "{tmp_path / "runs"}"
max_turns = 4
poll_interval = 0.01

[backend]
kind = "stub"
responses = []

[executor]
kind = "local"
runner = "/bin/sh"
'''
    )
    (tmp_path / "work").mkdir()
    monkeypatch.setattr(
        Loop, "sense",
        lambda self, run: {"data": {"next_recommended_step": "apply_preflag"}},
    )
    return tmp_path


def _cli(project, *args):
    return main(["--config", str(project / "config.toml"), *args])


def test_init_and_status(project, capsys):
    rc = _cli(project, "init", "--ms", str(project / "a.ms"),
              "--workdir", str(project / "work"), "--telescope", "VLA")
    assert rc == 0
    run_key = capsys.readouterr().out.strip()
    assert run_key.endswith(run_key[-4:]) and "-a-" in run_key
    assert _cli(project, "status") == 0
    out = capsys.readouterr().out
    assert run_key in out
    assert "status=active" in out


def test_stub_dry_run_and_rebuild(project, capsys):
    """PLAN.md 'Stub-backend dry run': canned decisions, local executor,
    a trivial script — proves the loop, then proves rebuild on its output."""
    _cli(project, "init", "--ms", str(project / "a.ms"),
         "--workdir", str(project / "work"))
    run_key = capsys.readouterr().out.strip()

    script = project / "work" / "stage.sh"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    decision = json.dumps({"script": str(script), "tool": "ms_apply_preflag",
                           "stage": "apply_preflag"})
    cfg = (project / "config.toml").read_text().replace(
        "responses = []", f"responses = [{json.dumps(decision)!s}] * 1"
    )
    # toml has no list-multiply; write the literal
    cfg = cfg.replace(f"[{json.dumps(decision)!s}] * 1", f"[{json.dumps(decision)!s}]")
    (project / "config.toml").write_text(cfg)

    rc = _cli(project, "step", "--run", run_key)
    assert rc == 0
    result = json.loads(capsys.readouterr().out)
    assert result["action"] == "completed"
    assert result["outcome"] == "accepted"

    # rebuild reproduces the same rows
    db = DriverDB(project / "runs")
    before = db.dump()
    assert _cli(project, "rebuild") == 0
    counts = json.loads(capsys.readouterr().out)
    assert counts == {"runs": 1, "turns": 1}
    db2 = DriverDB(project / "runs")
    assert db2.dump() == before
    db.close()
    db2.close()


def test_run_with_no_active_runs(project, capsys):
    assert _cli(project, "run") == 1
    assert "no active runs" in capsys.readouterr().err
