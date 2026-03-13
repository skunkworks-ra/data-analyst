"""
Unit tests for ms_apply_rflag (src/ms_modify/rflag.py).

No CASA or MS required — workdir/script generation is tested purely.
"""

from __future__ import annotations

import pytest

from ms_modify import rflag


def _make_ms(tmp_path, name="test.ms"):
    """Create a minimal fake MS directory with table.info."""
    ms = tmp_path / name
    ms.mkdir()
    (ms / "table.info").write_text("Type = Measurement Set")
    return ms


class TestBuildScript:
    def test_script_contains_rflag_call(self, tmp_path):
        script = rflag._build_script(
            ms_str="/data/cal.ms",
            field_sel="3C147",
            spw="0~3",
            datacolumn="corrected",
            timedevscale=5.0,
            freqdevscale=5.0,
            workdir=str(tmp_path),
        )
        assert "flagdata" in script
        assert "rflag" in script
        assert "apply" in script
        assert "corrected" in script
        assert "3C147" in script
        assert "timedevscale=5.0" in script

    def test_script_contains_backup(self, tmp_path):
        script = rflag._build_script(
            ms_str="/data/cal.ms",
            field_sel="",
            spw="",
            datacolumn="corrected",
            timedevscale=5.0,
            freqdevscale=5.0,
            workdir=str(tmp_path),
        )
        assert "before_rflag" in script
        assert "flagmanager" in script

    def test_script_is_valid_python(self, tmp_path):
        script = rflag._build_script(
            ms_str="/data/cal.ms",
            field_sel="",
            spw="",
            datacolumn="corrected",
            timedevscale=4.0,
            freqdevscale=3.5,
            workdir=str(tmp_path),
        )
        compile(script, "<string>", "exec")  # raises SyntaxError on bad code


class TestRun:
    def test_execute_false_writes_script(self, tmp_path):
        ms = _make_ms(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()

        result = rflag.run(str(ms), str(workdir), execute=False)

        assert result["status"] == "ok"
        script = workdir / "apply_rflag.py"
        assert script.exists()
        assert "apply_rflag.py" in result["data"]["script_path"]["value"]
        assert any("Script written" in w for w in result["warnings"])

    def test_execute_false_no_casa_needed(self, tmp_path):
        """execute=False must work without casatasks importable."""
        ms = _make_ms(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        result = rflag.run(str(ms), str(workdir))
        assert result["status"] == "ok"

    def test_workdir_missing_raises(self, tmp_path):
        ms = _make_ms(tmp_path)
        from ms_inspect.exceptions import RadioMSError
        with pytest.raises(RadioMSError):
            rflag.run(str(ms), str(tmp_path / "nonexistent"))

    def test_custom_scales_in_script(self, tmp_path):
        ms = _make_ms(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        rflag.run(str(ms), str(workdir), timedevscale=3.0, freqdevscale=4.0, execute=False)
        script = (workdir / "apply_rflag.py").read_text()
        assert "3.0" in script
        assert "4.0" in script
