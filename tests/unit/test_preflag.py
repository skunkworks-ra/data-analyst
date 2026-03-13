"""
Unit tests for ms_apply_preflag and ms_online_flag_stats.

No CASA required. Tests cover:
- _build_cmds_content: flag command list construction
- online_flags.run: text parsing logic
- preflag.run: workdir validation, script/cmds file creation
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from ms_modify.preflag import _build_cmds_content

# ---------------------------------------------------------------------------
# _build_cmds_content
# ---------------------------------------------------------------------------


class TestBuildCmdsContent:
    def test_no_online_file_includes_shadow_clip_extend(self):
        content = _build_cmds_content("", 0.0, True)
        lines = [ln for ln in content.splitlines() if ln.strip()]
        assert any("shadow" in ln for ln in lines)
        assert any("clip" in ln for ln in lines)
        assert any("extend" in ln for ln in lines)

    def test_do_tfcrop_true_includes_tfcrop(self):
        content = _build_cmds_content("", 0.0, True)
        assert "tfcrop" in content

    def test_do_tfcrop_false_excludes_tfcrop(self):
        content = _build_cmds_content("", 0.0, False)
        assert "tfcrop" not in content

    def test_shadow_tolerance_is_embedded(self):
        content = _build_cmds_content("", 42.5, False)
        assert "42.5" in content

    def test_missing_online_file_adds_warning_comment(self, tmp_path):
        content = _build_cmds_content(str(tmp_path / "nonexistent.txt"), 0.0, False)
        assert "WARNING" in content

    def test_online_file_lines_prepended(self, tmp_path):
        flag_file = tmp_path / "online.txt"
        flag_file.write_text("mode='online' reason='OFFLINE'\n# comment\n\n")
        content = _build_cmds_content(str(flag_file), 0.0, False)
        lines = [ln for ln in content.splitlines() if ln.strip()]
        # Online command should be first non-empty line
        assert lines[0] == "mode='online' reason='OFFLINE'"

    def test_comment_lines_in_online_file_are_skipped(self, tmp_path):
        flag_file = tmp_path / "online.txt"
        flag_file.write_text("# this is a comment\nmode='online'\n")
        content = _build_cmds_content(str(flag_file), 0.0, False)
        assert "# this is a comment" not in content
        assert "mode='online'" in content


# ---------------------------------------------------------------------------
# preflag.run — workdir validation and file creation
# ---------------------------------------------------------------------------


class TestPreflagRun:
    def test_missing_workdir_raises(self, tmp_path):
        from ms_inspect.exceptions import ComputationError

        # We need a fake MS path; validate_ms_path will fail first
        # so we test workdir validation by using a valid MS-like path
        # Actually validate_ms_path is called before workdir check,
        # so we need to mock it or use a real directory as ms_path
        # → use tmp_path as both ms and workdir (missing workdir)
        ms_fake = tmp_path / "fake.ms"
        ms_fake.mkdir()
        (ms_fake / "table.info").write_text("Type = Measurement Set\n")

        with pytest.raises(ComputationError, match="workdir does not exist"):
            from ms_modify.preflag import run

            run(
                ms_path=str(ms_fake),
                workdir=str(tmp_path / "nonexistent_workdir"),
                cal_fields="3C147",
            )

    def test_execute_false_writes_scripts(self, tmp_path):
        ms_fake = tmp_path / "fake.ms"
        ms_fake.mkdir()
        (ms_fake / "table.info").write_text("Type = Measurement Set\n")
        workdir = tmp_path / "work"
        workdir.mkdir()

        from ms_modify.preflag import run

        result = run(
            ms_path=str(ms_fake),
            workdir=str(workdir),
            cal_fields="3C147",
            execute=False,
        )

        assert result["status"] == "ok"
        assert (workdir / "preflag_cmds.txt").exists()
        assert (workdir / "preflag.py").exists()

    def test_script_contains_ms_path(self, tmp_path):
        ms_fake = tmp_path / "fake.ms"
        ms_fake.mkdir()
        (ms_fake / "table.info").write_text("Type = Measurement Set\n")
        workdir = tmp_path / "work"
        workdir.mkdir()

        from ms_modify.preflag import run

        run(
            ms_path=str(ms_fake),
            workdir=str(workdir),
            cal_fields="3C147",
            execute=False,
        )

        script = (workdir / "preflag.py").read_text()
        assert str(ms_fake) in script

    def test_script_contains_cal_fields(self, tmp_path):
        ms_fake = tmp_path / "fake.ms"
        ms_fake.mkdir()
        (ms_fake / "table.info").write_text("Type = Measurement Set\n")
        workdir = tmp_path / "work"
        workdir.mkdir()

        from ms_modify.preflag import run

        run(
            ms_path=str(ms_fake),
            workdir=str(workdir),
            cal_fields="3C147,3C286",
            execute=False,
        )

        script = (workdir / "preflag.py").read_text()
        assert "3C147,3C286" in script

    def test_n_flag_commands_in_response(self, tmp_path):
        ms_fake = tmp_path / "fake.ms"
        ms_fake.mkdir()
        (ms_fake / "table.info").write_text("Type = Measurement Set\n")
        workdir = tmp_path / "work"
        workdir.mkdir()

        from ms_modify.preflag import run

        result = run(
            ms_path=str(ms_fake),
            workdir=str(workdir),
            cal_fields="3C147",
            do_tfcrop=True,
            execute=False,
        )

        # shadow + clip + tfcrop + extend = 4
        n = result["data"]["n_flag_commands"]["value"]
        assert n == 4

    def test_n_flag_commands_no_tfcrop(self, tmp_path):
        ms_fake = tmp_path / "fake.ms"
        ms_fake.mkdir()
        (ms_fake / "table.info").write_text("Type = Measurement Set\n")
        workdir = tmp_path / "work"
        workdir.mkdir()

        from ms_modify.preflag import run

        result = run(
            ms_path=str(ms_fake),
            workdir=str(workdir),
            cal_fields="3C147",
            do_tfcrop=False,
            execute=False,
        )

        # shadow + clip + extend = 3
        n = result["data"]["n_flag_commands"]["value"]
        assert n == 3


# ---------------------------------------------------------------------------
# ms_online_flag_stats
# ---------------------------------------------------------------------------


class TestOnlineFlagStats:
    def _write_flag_file(self, tmp_path, content: str) -> Path:
        p = tmp_path / "online.flagonline.txt"
        p.write_text(textwrap.dedent(content))
        return p

    def test_missing_file_raises(self, tmp_path):
        from ms_inspect.exceptions import MSNotFoundError
        from ms_inspect.tools.online_flags import run

        with pytest.raises(MSNotFoundError):
            run(str(tmp_path / "nonexistent.txt"))

    def test_counts_commands(self, tmp_path):
        p = self._write_flag_file(
            tmp_path,
            """\
            mode='online' antenna='ea01' reason='OFFLINE'
            mode='online' antenna='ea02' reason='OFFLINE'
        """,
        )
        from ms_inspect.tools.online_flags import run

        result = run(str(p))
        assert result["data"]["n_commands"]["value"] == 2

    def test_reason_breakdown(self, tmp_path):
        p = self._write_flag_file(
            tmp_path,
            """\
            mode='online' antenna='ea01' reason='OFFLINE'
            mode='online' antenna='ea02' reason='NOT_ON_SOURCE'
            mode='online' antenna='ea03' reason='OFFLINE'
        """,
        )
        from ms_inspect.tools.online_flags import run

        result = run(str(p))
        breakdown = result["data"]["reason_breakdown"]["value"]
        assert breakdown["OFFLINE"] == 2
        assert breakdown["NOT_ON_SOURCE"] == 1

    def test_antenna_extraction(self, tmp_path):
        p = self._write_flag_file(
            tmp_path,
            """\
            mode='online' antenna='ea01' reason='OFFLINE'
            mode='online' antenna='ea03' reason='OFFLINE'
        """,
        )
        from ms_inspect.tools.online_flags import run

        result = run(str(p))
        ants = result["data"]["antennas_flagged"]["value"]
        assert "ea01" in ants
        assert "ea03" in ants
        assert result["data"]["n_antennas_flagged"]["value"] == 2

    def test_comment_lines_ignored(self, tmp_path):
        p = self._write_flag_file(
            tmp_path,
            """\
            # This is a comment
            mode='online' antenna='ea01' reason='OFFLINE'
        """,
        )
        from ms_inspect.tools.online_flags import run

        result = run(str(p))
        assert result["data"]["n_commands"]["value"] == 1

    def test_empty_file_returns_zero(self, tmp_path):
        p = self._write_flag_file(tmp_path, "")
        from ms_inspect.tools.online_flags import run

        result = run(str(p))
        assert result["data"]["n_commands"]["value"] == 0
        assert result["data"]["n_antennas_flagged"]["value"] == 0
