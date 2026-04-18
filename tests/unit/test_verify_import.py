"""
Unit tests for ms_inspect/tools/verify_import.py — filesystem-only checks.

No CASA required.
"""

from __future__ import annotations

from pathlib import Path

from ms_inspect.tools.verify_import import run


class TestVerifyImport:
    def _make_valid_ms(self, path: Path) -> Path:
        path.mkdir()
        (path / "table.info").write_text("Type = Measurement Set\n")
        return path

    def _make_flag_file(self, path: Path, n_commands: int = 5) -> Path:
        lines = [
            f"antenna='ea01' timerange='...' reason='ONLINE_SHADOW_{i}'" for i in range(n_commands)
        ]
        path.write_text("\n".join(lines) + "\n")
        return path

    def test_all_valid(self, tmp_path):
        ms = self._make_valid_ms(tmp_path / "obs.ms")
        ff = self._make_flag_file(tmp_path / "obs.flagonline.txt")
        result = run(str(ms), str(ff))
        assert result["data"]["ms_exists"]["value"] is True
        assert result["data"]["ms_valid"]["value"] is True
        assert result["data"]["flag_file_exists"]["value"] is True
        assert result["data"]["flag_file_n_commands"]["value"] == 5
        assert result["data"]["ready_for_preflag"]["value"] is True

    def test_ms_missing(self, tmp_path):
        ff = self._make_flag_file(tmp_path / "obs.flagonline.txt")
        result = run(str(tmp_path / "obs.ms"), str(ff))
        assert result["data"]["ms_exists"]["value"] is False
        assert result["data"]["ms_valid"]["value"] is False
        assert result["data"]["ready_for_preflag"]["value"] is False

    def test_ms_dir_no_table_info(self, tmp_path):
        ms = tmp_path / "obs.ms"
        ms.mkdir()
        ff = self._make_flag_file(tmp_path / "obs.flagonline.txt")
        result = run(str(ms), str(ff))
        assert result["data"]["ms_exists"]["value"] is True
        assert result["data"]["ms_valid"]["value"] is False
        assert result["data"]["ready_for_preflag"]["value"] is False

    def test_flag_file_missing(self, tmp_path):
        ms = self._make_valid_ms(tmp_path / "obs.ms")
        result = run(str(ms), str(tmp_path / "obs.flagonline.txt"))
        assert result["data"]["flag_file_exists"]["value"] is False
        assert result["data"]["ready_for_preflag"]["value"] is False

    def test_flag_file_empty(self, tmp_path):
        ms = self._make_valid_ms(tmp_path / "obs.ms")
        ff = tmp_path / "obs.flagonline.txt"
        ff.write_text("# just a comment\n\n")
        result = run(str(ms), str(ff))
        assert result["data"]["flag_file_exists"]["value"] is True
        assert result["data"]["flag_file_n_commands"]["value"] == 0
        assert result["data"]["ready_for_preflag"]["value"] is False

    def test_status_ok(self, tmp_path):
        ms = self._make_valid_ms(tmp_path / "obs.ms")
        ff = self._make_flag_file(tmp_path / "obs.flagonline.txt")
        result = run(str(ms), str(ff))
        assert result["status"] == "ok"
