"""
Unit tests for driver/commit.py.

commit_turn exists so that the decision record, the ledger, the replay script
and the git commit cannot drift apart. If any one of them can be written
without the others, the replay script eventually stops matching the ledger and
nobody notices for a week.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from analyst_driver import commit as commit_mod


def decision_file(run_dir, payload=None):
    p = run_dir / "decisions" / "001.json"
    p.write_text(
        json.dumps(payload or {"action": "run", "tool": "ms_setjy", "rationale": "flux model"})
    )
    return p


COMPLETED = {
    "tool": "ms_apply_preflag",
    "params": {"cal_fields": "0,1"},
    "outputs": {"headline": "flagged 8.1%"},
    "rationale": "A-priori flags first.",
}


# -- provenance ----------------------------------------------------------


def test_provenance_is_added_alongside_the_decision_not_inside_it(run_dir):
    p = decision_file(run_dir)
    commit_mod.stamp_provenance(p, {"backend": "claude", "step": 1})
    obj = json.loads(p.read_text())
    assert obj["decision"]["tool"] == "ms_setjy"
    assert obj["provenance"]["backend"] == "claude"
    assert "backend" not in obj["decision"]


def test_the_model_cannot_forge_a_provenance_field(run_dir):
    """A decision claiming its own provenance must not overwrite the driver's."""
    p = decision_file(
        run_dir,
        {"action": "done", "rationale": "x", "provenance": {"backend": "definitely-not-me"}},
    )
    commit_mod.stamp_provenance(p, {"backend": "claude"})
    obj = json.loads(p.read_text())
    assert obj["provenance"]["backend"] == "claude"
    assert obj["decision"]["provenance"]["backend"] == "definitely-not-me"


def test_commit_turn_stamps_the_utc_and_step(run_dir):
    p = decision_file(run_dir)
    commit_mod.commit_turn(
        run_dir=run_dir,
        step=7,
        decision_file=p,
        provenance={"backend": "claude"},
        completed=None,
        use_git=False,
    )
    prov = json.loads(p.read_text())["provenance"]
    assert prov["step"] == 7
    assert prov["utc"].endswith("Z")


# -- the ledger ----------------------------------------------------------


def test_a_successful_step_is_appended_to_the_ledger(run_dir):
    commit_mod.commit_turn(
        run_dir=run_dir,
        step=2,
        decision_file=decision_file(run_dir),
        provenance={},
        completed=COMPLETED,
        use_git=False,
    )
    lines = (run_dir / "reduction_log.jsonl").read_text().strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["tool"] == "ms_apply_preflag"
    assert rec["status"] == "ok"
    assert rec["params"] == {"cal_fields": "0,1"}


def test_nothing_is_appended_when_no_step_completed(run_dir):
    """The decision being stamped has not run yet. It earns its ledger line
    only after its job succeeds, one turn later."""
    commit_mod.commit_turn(
        run_dir=run_dir,
        step=1,
        decision_file=decision_file(run_dir),
        provenance={},
        completed=None,
        use_git=False,
    )
    assert not (run_dir / "reduction_log.jsonl").exists()


def test_the_ledger_accumulates_in_order(run_dir):
    for i, tool in enumerate(["ms_apply_preflag", "ms_setjy", "ms_gaincal"], start=1):
        commit_mod.commit_turn(
            run_dir=run_dir,
            step=i,
            decision_file=decision_file(run_dir),
            provenance={},
            completed={**COMPLETED, "tool": tool},
            use_git=False,
        )
    recs = [json.loads(x) for x in (run_dir / "reduction_log.jsonl").read_text().splitlines()]
    assert [r["tool"] for r in recs] == ["ms_apply_preflag", "ms_setjy", "ms_gaincal"]
    assert [r["step"] for r in recs] == [1, 2, 3]


# -- the replay script ---------------------------------------------------


def test_the_replay_script_is_rendered_from_the_ledger(run_dir):
    commit_mod.commit_turn(
        run_dir=run_dir,
        step=2,
        decision_file=decision_file(run_dir),
        provenance={},
        completed=COMPLETED,
        use_git=False,
    )
    script = (run_dir / "reduction_replay.py").read_text()
    assert "ms_modify.preflag" in script
    assert "cal_fields='0,1'" in script


def test_the_replay_script_holds_code_not_a_path(run_dir):
    """Regression: ms_reduction_log's render returns the script's PATH, and an
    earlier driver wrote that path into a file called replay.py."""
    commit_mod.commit_turn(
        run_dir=run_dir,
        step=2,
        decision_file=decision_file(run_dir),
        provenance={},
        completed=COMPLETED,
        use_git=False,
    )
    body = (run_dir / "reduction_replay.py").read_text()
    assert body.startswith("#!/usr/bin/env python")
    assert not (run_dir / "replay.py").exists()


def test_the_replay_script_carries_the_rationale_as_a_comment(run_dir):
    commit_mod.commit_turn(
        run_dir=run_dir,
        step=2,
        decision_file=decision_file(run_dir),
        provenance={},
        completed=COMPLETED,
        use_git=False,
    )
    assert "A-priori flags first." in (run_dir / "reduction_replay.py").read_text()


def test_render_replay_is_a_noop_without_a_ledger(run_dir):
    assert commit_mod.render_replay(run_dir) is False


# -- git -----------------------------------------------------------------


def git(run_dir, *args) -> str:
    return subprocess.run(
        ["git", "-C", str(run_dir), *args], capture_output=True, text=True
    ).stdout.strip()


def test_the_run_directory_becomes_its_own_repository(run_dir):
    decision_file(run_dir)  # git cannot commit an empty directory
    assert commit_mod.git_commit(run_dir, "step 001: ms_setjy") is True
    assert (run_dir / ".git").is_dir()
    assert "step 001: ms_setjy" in git(run_dir, "log", "--oneline")


def test_a_second_commit_with_no_change_is_not_an_error(run_dir):
    decision_file(run_dir)
    commit_mod.git_commit(run_dir, "first")
    assert commit_mod.git_commit(run_dir, "second") is False
    assert git(run_dir, "log", "--oneline").count("\n") == 0


def test_the_commit_is_scoped_to_the_run_directory(run_dir, tmp_path):
    """git -C must confine this. It must never reach the code repository."""
    outside = tmp_path.parent / "not_in_the_run"
    outside.mkdir(exist_ok=True)
    (outside / "untouched.txt").write_text("x")
    decision_file(run_dir)
    commit_mod.git_commit(run_dir, "step 001")
    tracked = git(run_dir, "ls-files")
    assert "decisions/001.json" in tracked, "the test would pass vacuously on an empty index"
    assert "untouched.txt" not in tracked


def test_commit_turn_labels_the_commit_with_the_tool(run_dir):
    commit_mod.commit_turn(
        run_dir=run_dir,
        step=3,
        decision_file=decision_file(run_dir),
        provenance={"tool": "ms_gaincal", "action": "run"},
        completed=None,
        use_git=True,
    )
    assert "step 003: ms_gaincal" in git(run_dir, "log", "--oneline")


def test_commit_turn_labels_a_toolless_action_with_the_action(run_dir):
    """Regression: `done` has no tool, and produced a bare 'step 003:'."""
    commit_mod.commit_turn(
        run_dir=run_dir,
        step=3,
        decision_file=decision_file(run_dir),
        provenance={"tool": "", "action": "done"},
        completed=None,
        use_git=True,
    )
    assert "step 003: done" in git(run_dir, "log", "--oneline")


def test_git_can_be_turned_off(run_dir):
    commit_mod.commit_turn(
        run_dir=run_dir,
        step=1,
        decision_file=decision_file(run_dir),
        provenance={},
        completed=None,
        use_git=False,
    )
    assert not (run_dir / ".git").exists()


# -- warnings, not crashes -----------------------------------------------


def test_a_ledger_failure_warns_rather_than_killing_a_live_run(run_dir, monkeypatch):
    monkeypatch.setattr(commit_mod, "append_ledger", lambda *a, **k: False)
    warnings = commit_mod.commit_turn(
        run_dir=run_dir,
        step=2,
        decision_file=decision_file(run_dir),
        provenance={},
        completed=COMPLETED,
        use_git=False,
    )
    assert any("ledger not updated" in w for w in warnings)


def test_a_clean_turn_produces_no_warnings(run_dir):
    warnings = commit_mod.commit_turn(
        run_dir=run_dir,
        step=2,
        decision_file=decision_file(run_dir),
        provenance={},
        completed=COMPLETED,
        use_git=False,
    )
    assert warnings == []


@pytest.mark.parametrize(
    "wrapped,expected",
    [
        ({"value": 3.0, "flag": "COMPLETE"}, 3.0),
        ("plain", "plain"),
        ({"no_value_key": 1}, {"no_value_key": 1}),
    ],
)
def test_unwrap(wrapped, expected):
    assert commit_mod._unwrap(wrapped) == expected
