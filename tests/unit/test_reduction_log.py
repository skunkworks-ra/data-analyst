"""
Unit tests for ms_create/reduction_log.py — JSONL working-calls ledger.

No CASA required.
"""

from __future__ import annotations

import json

import pytest

from ms_create.reduction_log import _RUN_REGISTRY, run


class TestRunRegistry:
    """The replay registry maps recorded tool names to modules. Every mapped
    module must be importable and expose run(), or render() silently emits a
    broken replay line instead of a runnable call."""

    def test_every_registry_module_imports_and_has_run(self):
        import importlib

        for tool, mod_name in _RUN_REGISTRY.items():
            mod = importlib.import_module(mod_name)
            assert hasattr(mod, "run"), f"{mod_name} (for {tool}) has no run()"

    def test_replayable_pipeline_tools_are_registered(self):
        # Core steps exercised in a real reduction must be replayable, not
        # emitted as MANUAL markers.
        for tool in (
            "ms_initial_bandpass",
            "ms_apply_initial_rflag",
            "ms_apply_rflag",
            "ms_flag_caltable",
        ):
            assert tool in _RUN_REGISTRY


class TestReductionLog:
    def test_append_then_list(self, tmp_path):
        wd = str(tmp_path)
        r1 = run(
            "append", wd, tool="ms_gaincal", params={"field": "3C286"}, rationale="initial phase"
        )
        assert r1["data"]["step_recorded"]["value"] == 1
        r2 = run("append", wd, tool="ms_bandpass", params={"spw": "0"}, rationale="bandpass")
        assert r2["data"]["n_records"]["value"] == 2
        lst = run("list", wd)
        steps = lst["data"]["steps"]
        assert [s["tool"] for s in steps] == ["ms_gaincal", "ms_bandpass"]
        assert steps[0]["rationale"] == "initial phase"

    def test_jsonl_on_disk(self, tmp_path):
        wd = str(tmp_path)
        run("append", wd, tool="ms_setjy", params={"standard": "Perley-Butler 2017"})
        log = tmp_path / "reduction_log.jsonl"
        assert log.exists()
        rec = json.loads(log.read_text().splitlines()[0])
        assert rec["tool"] == "ms_setjy"
        assert rec["params"]["standard"] == "Perley-Butler 2017"
        assert rec["step"] == 1

    def test_render_emits_replay(self, tmp_path):
        wd = str(tmp_path)
        run(
            "append",
            wd,
            tool="ms_gaincal",
            params={"field": "3C286", "solint": "int"},
            rationale="G0",
        )
        out = run("render", wd)
        assert out["data"]["n_records"]["value"] == 1
        assert len(out["data"]["recipe"]["value"]) == 1
        replay = tmp_path / "reduction_replay.py"
        assert replay.exists()
        text = replay.read_text()
        # Executable form: importlib.import_module('ms_modify.gaincal').run(...)
        assert "importlib.import_module('ms_modify.gaincal').run(" in text
        assert "field='3C286'" in text
        assert "G0" in text  # rationale comment

    def test_render_marks_unmapped_as_manual(self, tmp_path):
        wd = str(tmp_path)
        run("append", wd, tool="setjy(manual,pol)", params={"polangle_deg": 33})
        run("render", wd)
        text = (tmp_path / "reduction_replay.py").read_text()
        assert "MANUAL STEP" in text
        assert "importlib.import_module" not in text

    def test_list_empty(self, tmp_path):
        out = run("list", str(tmp_path))
        assert out["data"]["n_records"]["value"] == 0
        assert out["data"]["steps"] == []

    def test_unknown_action_raises(self, tmp_path):
        from ms_inspect.exceptions import ComputationError

        with pytest.raises(ComputationError):
            run("frobnicate", str(tmp_path))

    def test_missing_workdir_raises(self, tmp_path):
        from ms_inspect.exceptions import ComputationError

        with pytest.raises(ComputationError):
            run("list", str(tmp_path / "nope"))


class TestSupersededMSCheck:
    """
    render must refuse to emit a replay script when a calibration or imaging
    step uses an MS that a later step replaced.

    Why this matters: the ALMA prior-cal split REPLACES the working MS. The old
    MS still exists and still opens, but its data has no priors applied. A
    replay that reaches back to it produces a wrong image and no error. The VLA
    calibrators.ms split is NOT a replacement — later steps correctly return to
    the full MS — so supersession is declared per step, never inferred.
    """

    def test_no_supersession_declared_leaves_check_ineffective_and_says_so(self, tmp_path):
        wd = str(tmp_path)
        run("append", wd, tool="ms_gaincal", params={"ms_path": "/d/cal.ms"})
        out = run("render", wd)

        assert out["data"]["order_violations"] == []
        assert out["data"]["n_supersessions_declared"]["value"] == 0
        # A check that cannot fail must not read as evidence.
        eff = out["data"]["check_effective"]
        assert eff["value"] is False
        assert "could not fail" in eff["note"]

    def test_vla_style_return_to_full_ms_is_not_a_violation(self, tmp_path):
        """calibrators.ms is a side branch; going back to the full MS is correct."""
        wd = str(tmp_path)
        run("append", wd, tool="ms_apply_preflag", params={"ms_path": "/d/raw.ms"})
        run("append", wd, tool="ms_bandpass", params={"ms_path": "/d/calibrators.ms"})
        run("append", wd, tool="ms_applycal", params={"ms_path": "/d/raw.ms"})
        run("append", wd, tool="ms_tclean", params={"ms_path": "/d/raw.ms"})
        out = run("render", wd)

        assert out["data"]["order_violations"] == []
        assert (tmp_path / "reduction_replay.py").exists()

    def test_using_a_superseded_ms_refuses_to_render(self, tmp_path):
        from ms_inspect.exceptions import ComputationError

        wd = str(tmp_path)
        run("append", wd, tool="ms_apply_preflag", params={"ms_path": "/d/raw.ms"})
        run(
            "append",
            wd,
            tool="ms_apply_priorcals_split",
            params={"ms_path": "/d/science.ms"},
            supersedes="/d/raw.ms",
        )
        # Wrong: images the pre-split MS, which has no priors applied.
        run("append", wd, tool="ms_tclean", params={"ms_path": "/d/raw.ms"})

        with pytest.raises(ComputationError) as exc:
            run("render", wd)

        msg = str(exc.value)
        assert "/d/raw.ms" in msg
        assert "ms_tclean" in msg
        # No script may be left behind for someone to run by mistake.
        assert not (tmp_path / "reduction_replay.py").exists()

    def test_flagging_a_superseded_ms_is_allowed(self, tmp_path):
        """Flagging the pre-split MS stays valid; only cal/imaging must move on."""
        wd = str(tmp_path)
        run(
            "append",
            wd,
            tool="ms_apply_priorcals_split",
            params={"ms_path": "/d/science.ms"},
            supersedes="/d/raw.ms",
        )
        run("append", wd, tool="ms_apply_rflag", params={"ms_path": "/d/raw.ms"})
        out = run("render", wd)

        assert out["data"]["order_violations"] == []
        assert out["data"]["check_effective"]["value"] is True

    def test_supersession_is_recorded_and_reported(self, tmp_path):
        wd = str(tmp_path)
        run(
            "append",
            wd,
            tool="ms_apply_priorcals_split",
            params={"ms_path": "/d/science.ms"},
            supersedes="/d/raw.ms",
        )
        out = run("render", wd)
        chain = out["data"]["ms_chain"]

        assert len(chain) == 1
        assert chain[0]["replaced"] == "/d/raw.ms"
        assert chain[0]["with"] == "/d/science.ms"
        assert (
            json.loads((tmp_path / "reduction_log.jsonl").read_text().splitlines()[0])["supersedes"]
            == "/d/raw.ms"
        )


class TestReplayScriptMSVariables:
    """Each MS is declared once and referenced by variable, so a replay cannot
    end up half on one MS and half on another without it being visible."""

    def test_ms_paths_become_declared_variables(self, tmp_path):
        wd = str(tmp_path)
        run("append", wd, tool="ms_bandpass", params={"ms_path": "/d/calibrators.ms"})
        run("append", wd, tool="ms_tclean", params={"ms_path": "/d/science.ms"})
        run("render", wd)
        text = (tmp_path / "reduction_replay.py").read_text()

        assert "calibrators_ms = '/d/calibrators.ms'" in text
        assert "science_ms = '/d/science.ms'" in text
        # Referenced by variable, not repeated as a literal in the call.
        assert "ms_path=calibrators_ms," in text
        assert "ms_path=science_ms," in text
        assert "ms_path='/d/calibrators.ms'" not in text

    def test_supersession_is_commented_in_the_script(self, tmp_path):
        wd = str(tmp_path)
        run(
            "append",
            wd,
            tool="ms_apply_priorcals_split",
            params={"ms_path": "/d/science.ms"},
            supersedes="/d/raw.ms",
        )
        run("render", wd)
        text = (tmp_path / "reduction_replay.py").read_text()

        assert "REPLACED" in text
        assert "/d/raw.ms" in text

    def test_distinct_ms_with_same_basename_get_distinct_variables(self, tmp_path):
        wd = str(tmp_path)
        run("append", wd, tool="ms_bandpass", params={"ms_path": "/a/cal.ms"})
        run("append", wd, tool="ms_gaincal", params={"ms_path": "/b/cal.ms"})
        run("render", wd)
        text = (tmp_path / "reduction_replay.py").read_text()

        assert "cal_ms = '/a/cal.ms'" in text
        assert "cal_ms_2 = '/b/cal.ms'" in text
