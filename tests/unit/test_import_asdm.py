"""
Unit tests for ms_create/import_asdm.py — script generation and validation logic.

Tests run without CASA (execute=False paths only).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ms_create.exceptions import ASDMNotFoundError
from ms_create.import_asdm import _build_script, _resolve_ms_name, run


class TestResolveMsName:
    def test_default_uses_asdm_stem(self, tmp_path):
        asdm = tmp_path / "my_obs.asdm"
        result = _resolve_ms_name(asdm, "", tmp_path)
        assert result == tmp_path / "my_obs.ms"

    def test_explicit_name_without_suffix(self, tmp_path):
        asdm = tmp_path / "any.asdm"
        result = _resolve_ms_name(asdm, "custom_obs", tmp_path)
        assert result == tmp_path / "custom_obs.ms"

    def test_explicit_name_with_ms_suffix(self, tmp_path):
        asdm = tmp_path / "any.asdm"
        result = _resolve_ms_name(asdm, "custom_obs.ms", tmp_path)
        assert result == tmp_path / "custom_obs.ms"


class TestBuildScript:
    def test_fixed_parameters_in_script(self):
        script = _build_script("/data/obs.asdm", "/work/obs.ms", "/work/obs.flagonline.txt", False)
        assert 'ocorr_mode="co"' in script
        assert "savecmds=True" in script
        assert "applyflags=False" in script
        assert "with_pointing_correction=False" in script

    def test_pointing_correction_flag(self):
        script = _build_script("/data/obs.asdm", "/work/obs.ms", "/work/obs.flagonline.txt", True)
        assert "with_pointing_correction=True" in script

    def test_paths_in_script(self):
        script = _build_script("/data/obs.asdm", "/work/obs.ms", "/work/obs.flagonline.txt", False)
        assert "/data/obs.asdm" in script
        assert "/work/obs.ms" in script
        assert "/work/obs.flagonline.txt" in script


class TestRun:
    def test_asdm_not_found_raises(self, tmp_path):
        with pytest.raises(ASDMNotFoundError):
            run(str(tmp_path / "nonexistent.asdm"), str(tmp_path))

    def test_asdm_is_file_not_dir_raises(self, tmp_path):
        fake_asdm = tmp_path / "fake.asdm"
        fake_asdm.write_text("not a directory")
        with pytest.raises(ASDMNotFoundError):
            run(str(fake_asdm), str(tmp_path))

    def test_workdir_not_found_raises(self, tmp_path):
        asdm = tmp_path / "obs.asdm"
        asdm.mkdir()
        from ms_inspect.exceptions import ComputationError

        with pytest.raises(ComputationError):
            run(str(asdm), str(tmp_path / "nonexistent_workdir"))

    def test_script_written_on_generate(self, tmp_path):
        asdm = tmp_path / "obs.asdm"
        asdm.mkdir()
        result = run(str(asdm), str(tmp_path), execute=False)
        script_path = Path(result["data"]["script_path"]["value"])
        assert script_path.exists()

    def test_ms_path_unavailable_when_not_executed(self, tmp_path):
        asdm = tmp_path / "obs.asdm"
        asdm.mkdir()
        result = run(str(asdm), str(tmp_path), execute=False)
        assert result["data"]["ms_path"]["flag"] == "UNAVAILABLE"
        assert result["data"]["online_flag_file"]["flag"] == "UNAVAILABLE"

    def test_fixed_params_in_response(self, tmp_path):
        asdm = tmp_path / "obs.asdm"
        asdm.mkdir()
        result = run(str(asdm), str(tmp_path), execute=False)
        assert result["data"]["ocorr_mode"]["value"] == "co"
        assert result["data"]["savecmds"]["value"] is True
        assert result["data"]["applyflags"]["value"] is False

    def test_custom_ms_name(self, tmp_path):
        asdm = tmp_path / "obs.asdm"
        asdm.mkdir()
        result = run(str(asdm), str(tmp_path), ms_name="my_custom", execute=False)
        script_path = Path(result["data"]["script_path"]["value"])
        script_content = script_path.read_text()
        assert "my_custom.ms" in script_content

    def test_with_pointing_correction_default_false(self, tmp_path):
        asdm = tmp_path / "obs.asdm"
        asdm.mkdir()
        result = run(str(asdm), str(tmp_path), execute=False)
        assert result["data"]["with_pointing_correction"]["value"] is False

    def test_status_ok(self, tmp_path):
        asdm = tmp_path / "obs.asdm"
        asdm.mkdir()
        result = run(str(asdm), str(tmp_path), execute=False)
        assert result["status"] == "ok"
