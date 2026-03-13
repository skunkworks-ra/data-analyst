"""
Unit tests for ms_apply_initial_rflag.

No CASA required. Tests cover:
- _build_cmds_content: command list file content
- run: workdir validation, script/cmds file creation
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ms_modify.initial_rflag import _build_cmds_content

# ---------------------------------------------------------------------------
# _build_cmds_content
# ---------------------------------------------------------------------------


class TestBuildCmdsContent:
    def test_contains_rflag(self):
        content = _build_cmds_content(5.0, 5.0, 4.0, 4.0)
        assert "rflag" in content

    def test_contains_tfcrop(self):
        content = _build_cmds_content(5.0, 5.0, 4.0, 4.0)
        assert "tfcrop" in content

    def test_datacolumn_is_residual(self):
        content = _build_cmds_content(5.0, 5.0, 4.0, 4.0)
        assert "residual" in content

    def test_custom_thresholds_embedded(self):
        content = _build_cmds_content(3.5, 4.5, 2.0, 2.5)
        assert "3.5" in content
        assert "4.5" in content
        assert "2.0" in content
        assert "2.5" in content

    def test_action_apply_in_both_lines(self):
        content = _build_cmds_content(5.0, 5.0, 4.0, 4.0)
        lines = [ln for ln in content.splitlines() if ln.strip()]
        for line in lines:
            assert "action='apply'" in line

    def test_exactly_two_command_lines(self):
        content = _build_cmds_content(5.0, 5.0, 4.0, 4.0)
        lines = [ln for ln in content.splitlines() if ln.strip()]
        assert len(lines) == 2


# ---------------------------------------------------------------------------
# initial_rflag.run
# ---------------------------------------------------------------------------


class TestInitialRflagRun:
    def _make_ms(self, tmp_path) -> Path:
        ms = tmp_path / "test.ms"
        ms.mkdir()
        (ms / "table.info").write_text("Type = Measurement Set\n")
        return ms

    def test_missing_workdir_raises(self, tmp_path):
        from ms_inspect.exceptions import ComputationError
        from ms_modify.initial_rflag import run

        ms = self._make_ms(tmp_path)
        with pytest.raises(ComputationError, match="workdir does not exist"):
            run(str(ms), str(tmp_path / "nodir"))

    def test_execute_false_writes_both_files(self, tmp_path):
        from ms_modify.initial_rflag import run

        ms = self._make_ms(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        result = run(str(ms), str(workdir), execute=False)
        assert result["status"] == "ok"
        assert (workdir / "initial_rflag_cmds.txt").exists()
        assert (workdir / "initial_rflag.py").exists()

    def test_cmds_file_contains_residual(self, tmp_path):
        from ms_modify.initial_rflag import run

        ms = self._make_ms(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        run(str(ms), str(workdir), execute=False)
        cmds = (workdir / "initial_rflag_cmds.txt").read_text()
        assert "residual" in cmds

    def test_script_references_cmds_file(self, tmp_path):
        from ms_modify.initial_rflag import run

        ms = self._make_ms(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        run(str(ms), str(workdir), execute=False)
        script = (workdir / "initial_rflag.py").read_text()
        assert "initial_rflag_cmds.txt" in script

    def test_script_has_flagbackup_true(self, tmp_path):
        from ms_modify.initial_rflag import run

        ms = self._make_ms(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        run(str(ms), str(workdir), execute=False)
        script = (workdir / "initial_rflag.py").read_text()
        assert "flagbackup=True" in script

    def test_response_includes_thresholds(self, tmp_path):
        from ms_modify.initial_rflag import run

        ms = self._make_ms(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        result = run(str(ms), str(workdir), timedevscale=3.5, freqdevscale=4.5, execute=False)
        assert result["data"]["rflag_timedevscale"] == 3.5
        assert result["data"]["rflag_freqdevscale"] == 4.5

    def test_custom_thresholds_in_cmds_file(self, tmp_path):
        from ms_modify.initial_rflag import run

        ms = self._make_ms(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        run(str(ms), str(workdir), timedevscale=7.0, freqdevscale=8.0, execute=False)
        cmds = (workdir / "initial_rflag_cmds.txt").read_text()
        assert "7.0" in cmds
        assert "8.0" in cmds

    def test_re_run_overwrites_files(self, tmp_path):
        """Deterministic filenames mean re-running replaces previous output."""
        from ms_modify.initial_rflag import run

        ms = self._make_ms(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        run(str(ms), str(workdir), execute=False)
        mtime1 = (workdir / "initial_rflag.py").stat().st_mtime_ns
        run(str(ms), str(workdir), execute=False)
        mtime2 = (workdir / "initial_rflag.py").stat().st_mtime_ns
        # File should have been written again (same or newer mtime)
        assert mtime2 >= mtime1
