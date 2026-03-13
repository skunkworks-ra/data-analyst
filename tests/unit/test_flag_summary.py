"""
Unit tests for ms_flag_summary (src/ms_inspect/tools/flag_summary.py).

casatasks is mocked — no CASA installation required.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from ms_inspect.tools import flag_summary


def _make_ms(tmp_path, name="test.ms"):
    ms = tmp_path / name
    ms.mkdir()
    (ms / "table.info").write_text("Type = Measurement Set")
    return ms


def _make_summary(overall_frac: float = 0.1) -> dict:
    """Build a minimal flagdata(mode='summary') return value."""
    total = 1000
    flagged = int(total * overall_frac)
    return {
        "total": {"flagged": flagged, "total": total},
        "field": {
            "3C147": {"flagged": 50, "total": 500},
            "J0521": {"flagged": 50, "total": 500},
        },
        "spw": {
            "0": {"flagged": 60, "total": 400},
            "1": {"flagged": 40, "total": 600},
        },
        "antenna": {
            "ea01": {"flagged": 10, "total": 200},
            "ea02": {"flagged": 990, "total": 1000},  # fully flagged → warning
        },
        "scan": {
            "1": {"flagged": 50, "total": 500},
            "2": {"flagged": 50, "total": 500},
        },
    }


class TestRun:
    def test_basic_output_structure(self, tmp_path):
        ms = _make_ms(tmp_path)
        summary = _make_summary(0.1)

        mock_casatasks = MagicMock()
        mock_casatasks.flagdata.return_value = summary

        with patch.dict("sys.modules", {"casatasks": mock_casatasks}):
            result = flag_summary.run(str(ms))

        assert result["status"] == "ok"
        assert "total_flag_fraction" in result["data"]
        assert "per_field" in result["data"]
        assert "per_spw" in result["data"]
        assert "per_antenna" in result["data"]

    def test_overall_fraction_correct(self, tmp_path):
        ms = _make_ms(tmp_path)
        summary = _make_summary(0.2)

        mock_casatasks = MagicMock()
        mock_casatasks.flagdata.return_value = summary

        with patch.dict("sys.modules", {"casatasks": mock_casatasks}):
            result = flag_summary.run(str(ms))

        assert abs(result["data"]["total_flag_fraction"]["value"] - 0.2) < 0.01

    def test_fully_flagged_antenna_warns(self, tmp_path):
        ms = _make_ms(tmp_path)
        summary = _make_summary()
        summary["antenna"]["ea02"] = {"flagged": 1000, "total": 1000}

        mock_casatasks = MagicMock()
        mock_casatasks.flagdata.return_value = summary

        with patch.dict("sys.modules", {"casatasks": mock_casatasks}):
            result = flag_summary.run(str(ms))

        assert any("ea02" in w for w in result["warnings"])

    def test_per_spw_sorted(self, tmp_path):
        ms = _make_ms(tmp_path)
        summary = _make_summary()
        summary["spw"] = {"2": {"flagged": 5, "total": 100}, "0": {"flagged": 10, "total": 100}}

        mock_casatasks = MagicMock()
        mock_casatasks.flagdata.return_value = summary

        with patch.dict("sys.modules", {"casatasks": mock_casatasks}):
            result = flag_summary.run(str(ms))

        spw_ids = [s["spw_id"] for s in result["data"]["per_spw"]]
        assert spw_ids == sorted(spw_ids)

    def test_field_and_spw_passed_to_flagdata(self, tmp_path):
        ms = _make_ms(tmp_path)

        mock_casatasks = MagicMock()
        mock_casatasks.flagdata.return_value = _make_summary()

        with patch.dict("sys.modules", {"casatasks": mock_casatasks}):
            flag_summary.run(str(ms), field="3C147", spw="0~3")

        call_kwargs = mock_casatasks.flagdata.call_args[1]
        assert call_kwargs["field"] == "3C147"
        assert call_kwargs["spw"] == "0~3"

    def test_flagdata_exception_returns_error_envelope(self, tmp_path):
        ms = _make_ms(tmp_path)

        mock_casatasks = MagicMock()
        mock_casatasks.flagdata.side_effect = RuntimeError("CASA crash")

        with patch.dict("sys.modules", {"casatasks": mock_casatasks}):
            result = flag_summary.run(str(ms))

        assert result["status"] == "error"
        assert "CASA crash" in result["message"]
