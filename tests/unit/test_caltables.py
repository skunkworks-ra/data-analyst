"""
Unit tests for ms_verify_caltables (src/ms_inspect/tools/caltables.py).

All casatools calls are mocked — no CASA installation required.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from ms_inspect.tools import caltables

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tb_mock(n_rows: int, col_names: list[str]):
    """Return a mock table context manager that reports n_rows and col_names."""
    tb = MagicMock()
    tb.__enter__ = MagicMock(return_value=tb)
    tb.__exit__ = MagicMock(return_value=False)
    tb.nrows.return_value = n_rows
    tb.colnames.return_value = col_names
    return tb


# ---------------------------------------------------------------------------
# _check_table unit tests
# ---------------------------------------------------------------------------


class TestCheckTable:
    def test_path_not_exists(self, tmp_path):
        result = caltables._check_table(str(tmp_path / "nonexistent.g"), ["CPARAM"])
        assert result["exists"] is False
        assert result["valid"] is False
        assert "CPARAM" in result["missing_cols"]

    def test_path_is_file_not_dir(self, tmp_path):
        f = tmp_path / "notatable"
        f.write_text("x")
        result = caltables._check_table(str(f), ["CPARAM"])
        assert result["exists"] is False
        assert result["valid"] is False

    def test_empty_table(self, tmp_path):
        tbl = tmp_path / "empty.g"
        tbl.mkdir()
        tb_mock = _make_tb_mock(n_rows=0, col_names=["CPARAM"])
        with patch("ms_inspect.tools.caltables.open_table", return_value=tb_mock):
            result = caltables._check_table(str(tbl), ["CPARAM"])
        assert result["exists"] is True
        assert result["n_rows"] == 0
        assert result["valid"] is False

    def test_missing_required_col(self, tmp_path):
        tbl = tmp_path / "nocol.g"
        tbl.mkdir()
        tb_mock = _make_tb_mock(n_rows=10, col_names=["TIME", "ANTENNA1"])
        with patch("ms_inspect.tools.caltables.open_table", return_value=tb_mock):
            result = caltables._check_table(str(tbl), ["CPARAM"])
        assert result["valid"] is False
        assert "CPARAM" in result["missing_cols"]

    def test_valid_table(self, tmp_path):
        tbl = tmp_path / "valid.g"
        tbl.mkdir()
        tb_mock = _make_tb_mock(n_rows=25, col_names=["TIME", "CPARAM", "FLAG"])
        with patch("ms_inspect.tools.caltables.open_table", return_value=tb_mock):
            result = caltables._check_table(str(tbl), ["CPARAM"])
        assert result["valid"] is True
        assert result["n_rows"] == 25

    def test_open_exception(self, tmp_path):
        tbl = tmp_path / "bad.g"
        tbl.mkdir()
        tb_mock = MagicMock()
        tb_mock.__enter__ = MagicMock(side_effect=RuntimeError("lock"))
        tb_mock.__exit__ = MagicMock(return_value=False)
        with patch("ms_inspect.tools.caltables.open_table", return_value=tb_mock):
            result = caltables._check_table(str(tbl), ["CPARAM"])
        assert result["valid"] is False
        assert "error" in result


# ---------------------------------------------------------------------------
# run() integration-style unit tests
# ---------------------------------------------------------------------------


class TestRun:
    def test_both_valid(self, tmp_path):
        ms = tmp_path / "test.ms"
        ms.mkdir()
        ig = tmp_path / "init_gain.g"
        ig.mkdir()
        bp = tmp_path / "BP0.b"
        bp.mkdir()

        ig_mock = _make_tb_mock(10, ["CPARAM", "FLAG"])
        bp_mock = _make_tb_mock(5, ["CPARAM", "FLAG"])

        def _open_table_side(path, **kwargs):
            if "init_gain" in path:
                return ig_mock
            return bp_mock

        with patch("ms_inspect.tools.caltables.open_table", side_effect=_open_table_side):
            result = caltables.run(str(ms), str(ig), str(bp))

        assert result["status"] == "ok"
        assert result["data"]["caltables_valid"]["value"] is True
        assert result["data"]["init_gain_table"]["valid"]["value"] is True
        assert result["data"]["bp_table"]["valid"]["value"] is True

    def test_init_gain_missing(self, tmp_path):
        ms = tmp_path / "test.ms"
        ms.mkdir()
        bp = tmp_path / "BP0.b"
        bp.mkdir()

        bp_mock = _make_tb_mock(5, ["CPARAM"])

        with patch("ms_inspect.tools.caltables.open_table", return_value=bp_mock):
            result = caltables.run(str(ms), str(tmp_path / "init_gain.g"), str(bp))

        assert result["status"] == "ok"
        assert result["data"]["caltables_valid"]["value"] is False
        assert result["data"]["init_gain_table"]["exists"]["value"] is False
        assert any("init_gain" in w for w in result["warnings"])

    def test_bp_uses_fparam(self, tmp_path):
        """BP0.b with FPARAM (not CPARAM) should still be valid."""
        ms = tmp_path / "test.ms"
        ms.mkdir()
        ig = tmp_path / "init_gain.g"
        ig.mkdir()
        bp = tmp_path / "BP0.b"
        bp.mkdir()

        ig_mock = _make_tb_mock(10, ["CPARAM"])
        bp_mock = _make_tb_mock(5, ["FPARAM", "FLAG"])

        def _side(path, **kwargs):
            if "init_gain" in path:
                return ig_mock
            return bp_mock

        with patch("ms_inspect.tools.caltables.open_table", side_effect=_side):
            result = caltables.run(str(ms), str(ig), str(bp))

        assert result["data"]["caltables_valid"]["value"] is True

    def test_completeness_summary_unavailable_when_invalid(self, tmp_path):
        ms = tmp_path / "test.ms"
        ms.mkdir()
        result = caltables.run(
            str(ms),
            str(tmp_path / "missing_gain.g"),
            str(tmp_path / "missing_bp.b"),
        )
        assert result["completeness_summary"] == "UNAVAILABLE"
