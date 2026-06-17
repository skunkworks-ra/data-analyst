"""
Unit tests for ms_initial_bandpass.

No CASA required. Tests cover the execute=False script-generation path, with a
focus on the applycal_field / applymode plumbing added to decouple the Step 3
applycal field from the solve field (it previously hardwired field='', which
corrupted the FLAG state of non-BP calibrators).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ms_modify.initial_bandpass import run


def _make_ms(tmp_path) -> Path:
    ms = tmp_path / "test.ms"
    ms.mkdir()
    (ms / "table.info").write_text("Type = Measurement Set\n")
    return ms


def _make_workdir(tmp_path) -> Path:
    workdir = tmp_path / "work"
    workdir.mkdir()
    return workdir


# ---------------------------------------------------------------------------
# Validation / required arguments
# ---------------------------------------------------------------------------


class TestValidation:
    def test_missing_workdir_raises(self, tmp_path):
        from ms_inspect.exceptions import ComputationError

        ms = _make_ms(tmp_path)
        with pytest.raises(ComputationError, match="workdir does not exist"):
            run(str(ms), "3C147", "3C147", "ea05", str(tmp_path / "nodir"))

    def test_applycal_field_is_required(self):
        # No default for applycal_field — calling without it is a TypeError.
        import inspect

        sig = inspect.signature(run)
        assert sig.parameters["applycal_field"].default is inspect.Parameter.empty

    def test_applymode_defaults_to_calflagstrict(self):
        import inspect

        sig = inspect.signature(run)
        assert sig.parameters["applymode"].default == "calflagstrict"


# ---------------------------------------------------------------------------
# execute=False script generation
# ---------------------------------------------------------------------------


class TestScriptGeneration:
    def test_writes_script(self, tmp_path):
        ms = _make_ms(tmp_path)
        workdir = _make_workdir(tmp_path)
        result = run(str(ms), "3C147", "3C147", "ea05", str(workdir), execute=False)
        assert result["status"] == "ok"
        assert (workdir / "initial_bandpass.py").exists()

    def test_solve_uses_bp_field_applycal_uses_applycal_field(self, tmp_path):
        # The whole point of #2: gaincal/bandpass solve on bp_field, but the
        # Step 3 applycal must apply only to applycal_field.
        ms = _make_ms(tmp_path)
        workdir = _make_workdir(tmp_path)
        run(str(ms), "3C147", "J1331+3030", "ea05", str(workdir), execute=False)
        script = (workdir / "initial_bandpass.py").read_text()

        # Split at the applycal call so we can attribute field= occurrences.
        solve_part, _, applycal_part = script.partition("applycal(")
        assert "field='3C147'" in solve_part  # gaincal + bandpass
        assert "field='J1331+3030'" in applycal_part  # Step 3 applycal
        assert "field='3C147'" not in applycal_part

    def test_applycal_field_empty_is_honored(self, tmp_path):
        # field='' (all fields) is a valid, deliberate choice — not the default.
        ms = _make_ms(tmp_path)
        workdir = _make_workdir(tmp_path)
        run(str(ms), "3C147", "", "ea05", str(workdir), execute=False)
        script = (workdir / "initial_bandpass.py").read_text()
        _, _, applycal_part = script.partition("applycal(")
        assert "field=''" in applycal_part

    def test_default_applymode_in_script(self, tmp_path):
        ms = _make_ms(tmp_path)
        workdir = _make_workdir(tmp_path)
        run(str(ms), "3C147", "3C147", "ea05", str(workdir), execute=False)
        script = (workdir / "initial_bandpass.py").read_text()
        assert "applymode='calflagstrict'" in script

    def test_calflag_applymode_override_in_script(self, tmp_path):
        ms = _make_ms(tmp_path)
        workdir = _make_workdir(tmp_path)
        run(
            str(ms),
            "3C147",
            "3C147",
            "ea05",
            str(workdir),
            applymode="calflag",
            execute=False,
        )
        script = (workdir / "initial_bandpass.py").read_text()
        assert "applymode='calflag'" in script
        assert "applymode='calflagstrict'" not in script


# ---------------------------------------------------------------------------
# Response envelope
# ---------------------------------------------------------------------------


class TestResponse:
    def test_response_records_applycal_field_and_applymode(self, tmp_path):
        ms = _make_ms(tmp_path)
        workdir = _make_workdir(tmp_path)
        result = run(
            str(ms),
            "3C147",
            "J1331+3030",
            "ea05",
            str(workdir),
            applymode="calflag",
            execute=False,
        )
        data = result["data"]
        assert data["bp_field"] == "3C147"
        assert data["applycal_field"] == "J1331+3030"
        assert data["applymode"] == "calflag"
