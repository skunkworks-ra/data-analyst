"""
Unit tests for ms_create/reduction_log.py — JSONL working-calls ledger.

No CASA required.
"""

from __future__ import annotations

import json

import pytest

from ms_create.reduction_log import run


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
        assert "ms_gaincal" in text
        assert "G0" in text

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
