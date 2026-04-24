"""
Unit tests for Input models and helper functions (no CASA required).

Tests Pydantic input validation and utility functions:
- FlagSummaryInput, AntennaFlagFractionInput, ImageStatsInput
- _recommended_workers
- run_preflight helpers
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from ms_inspect.server import FlagSummaryInput, AntennaFlagFractionInput, ImageStatsInput
from ms_inspect.tools.flags import _recommended_workers, _get_n_workers


class TestFlagSummaryInput:
    def test_minimal_input(self):
        inp = FlagSummaryInput(ms_path="/data/test.ms")
        assert inp.ms_path == "/data/test.ms"
        assert inp.field == ""
        assert inp.spw == ""
        assert inp.include_per_scan is False

    def test_full_input(self):
        inp = FlagSummaryInput(
            ms_path="/data/test.ms",
            field="3C147",
            spw="0~3",
            include_per_scan=True,
        )
        assert inp.ms_path == "/data/test.ms"
        assert inp.field == "3C147"
        assert inp.spw == "0~3"
        assert inp.include_per_scan is True

    def test_whitespace_stripped(self):
        inp = FlagSummaryInput(ms_path="  /data/test.ms  ", field="  3C147  ")
        assert inp.ms_path == "/data/test.ms"
        assert inp.field == "3C147"

    def test_missing_ms_path_fails(self):
        with pytest.raises(ValidationError):
            FlagSummaryInput()

    def test_empty_ms_path_fails(self):
        with pytest.raises(ValidationError):
            FlagSummaryInput(ms_path="")

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            FlagSummaryInput(ms_path="/data/test.ms", extra_field="not_allowed")


class TestAntennaFlagFractionInput:
    def test_minimal_input(self):
        inp = AntennaFlagFractionInput(ms_path="/data/test.ms")
        assert inp.ms_path == "/data/test.ms"
        assert inp.n_workers is None

    def test_with_worker_count(self):
        inp = AntennaFlagFractionInput(ms_path="/data/test.ms", n_workers=2)
        assert inp.n_workers == 2

    def test_worker_count_bounds(self):
        # Valid: 1–8
        inp = AntennaFlagFractionInput(ms_path="/data/test.ms", n_workers=1)
        assert inp.n_workers == 1
        inp = AntennaFlagFractionInput(ms_path="/data/test.ms", n_workers=8)
        assert inp.n_workers == 8

    def test_worker_count_below_min_fails(self):
        with pytest.raises(ValidationError):
            AntennaFlagFractionInput(ms_path="/data/test.ms", n_workers=0)

    def test_worker_count_above_max_fails(self):
        with pytest.raises(ValidationError):
            AntennaFlagFractionInput(ms_path="/data/test.ms", n_workers=9)

    def test_missing_ms_path_fails(self):
        with pytest.raises(ValidationError):
            AntennaFlagFractionInput()

    def test_whitespace_stripped(self):
        inp = AntennaFlagFractionInput(ms_path="  /data/test.ms  ")
        assert inp.ms_path == "/data/test.ms"


class TestImageStatsInput:
    def test_minimal_input(self):
        inp = ImageStatsInput(image_path="/data/target.image")
        assert inp.image_path == "/data/target.image"
        assert inp.psf_path is None

    def test_with_psf_path(self):
        inp = ImageStatsInput(
            image_path="/data/target.image",
            psf_path="/data/target.psf",
        )
        assert inp.image_path == "/data/target.image"
        assert inp.psf_path == "/data/target.psf"

    def test_whitespace_stripped(self):
        inp = ImageStatsInput(
            image_path="  /data/target.image  ",
            psf_path="  /data/target.psf  ",
        )
        assert inp.image_path == "/data/target.image"
        assert inp.psf_path == "/data/target.psf"

    def test_missing_image_path_fails(self):
        with pytest.raises(ValidationError):
            ImageStatsInput()

    def test_empty_image_path_fails(self):
        with pytest.raises(ValidationError):
            ImageStatsInput(image_path="")

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            ImageStatsInput(image_path="/data/target.image", unknown_field="bad")


class TestRecommendedWorkers:
    def test_small_ms_single_worker(self):
        """Small MSs (< 100k rows) should use 1 worker."""
        assert _recommended_workers(10_000) == 1
        assert _recommended_workers(50_000) == 1

    def test_medium_ms_multiple_workers(self):
        """Medium MSs should parallelize: 100k rows → ~1 worker, 500k → ~5 workers."""
        result = _recommended_workers(500_000)
        assert result > 1, f"Expected > 1 worker for 500k rows, got {result}"

    def test_large_ms_capped_at_env_max(self):
        """Very large MSs capped at env-configured maximum (default 4)."""
        # With default env (4 workers), expect capped result
        result = _recommended_workers(1_000_000)
        assert 1 <= result <= 8

    def test_zero_rows(self):
        """Zero rows should return 1 worker."""
        assert _recommended_workers(0) == 1

    def test_respects_env_max(self):
        """Respects RADIO_MCP_WORKERS env cap."""
        with patch.dict("os.environ", {"RADIO_MCP_WORKERS": "2"}):
            result = _recommended_workers(1_000_000)
            assert result <= 2


class TestGetNWorkers:
    def test_default_workers(self):
        """Without env var, returns default (4)."""
        with patch.dict("os.environ", {}, clear=True):
            result = _get_n_workers()
            assert 1 <= result <= 8

    def test_env_override(self):
        """RADIO_MCP_WORKERS env var overrides default."""
        with patch.dict("os.environ", {"RADIO_MCP_WORKERS": "2"}):
            assert _get_n_workers() == 2

    def test_env_below_min_clamped(self):
        """Below 1 is clamped to 1."""
        with patch.dict("os.environ", {"RADIO_MCP_WORKERS": "0"}):
            assert _get_n_workers() == 1

    def test_env_above_max_clamped(self):
        """Above 8 is clamped to 8."""
        with patch.dict("os.environ", {"RADIO_MCP_WORKERS": "16"}):
            assert _get_n_workers() == 8

    def test_env_invalid_uses_default(self):
        """Invalid env var falls back to default."""
        with patch.dict("os.environ", {"RADIO_MCP_WORKERS": "not_a_number"}):
            result = _get_n_workers()
            assert 1 <= result <= 8


class TestRunPreflight:
    def test_valid_ms_returns_ok(self, tmp_path):
        """Valid MS returns status ok with data."""
        from ms_inspect.tools.flags import run_preflight

        ms = tmp_path / "test.ms"
        ms.mkdir()
        (ms / "table.info").write_text("Type = Measurement Set")

        # Mock CASA table calls
        mock_casatools = MagicMock()
        mock_table = MagicMock()

        # Main table mock
        mock_table.nrows.return_value = 100_000
        mock_table.getcolshapestring.return_value = ["[4, 64]"] * 100_000

        mock_casatools.table.return_value = mock_table

        with patch("ms_inspect.util.casa_context._require_casatools", return_value=mock_casatools):
            result = run_preflight(str(ms))

        assert result["status"] == "ok"
        assert "n_rows" in result["data"]
        assert "flag_col_shape" in result["data"]
        assert "recommended_workers" in result["data"]
        assert result["data"]["n_rows"]["value"] == 100_000

    def test_missing_ms_raises(self):
        """Missing MS raises MSNotFoundError."""
        from ms_inspect.tools.flags import run_preflight
        from ms_inspect.exceptions import MSNotFoundError

        with pytest.raises(MSNotFoundError):
            run_preflight("/nonexistent/path.ms")

    def test_estimates_runtime(self, tmp_path):
        """Runtime estimation is computed from row count."""
        from ms_inspect.tools.flags import run_preflight

        ms = tmp_path / "test.ms"
        ms.mkdir()
        (ms / "table.info").write_text("Type = Measurement Set")

        mock_casatools = MagicMock()
        mock_table = MagicMock()
        mock_table.nrows.return_value = 500_000_000  # 500M rows (triggers long runtime warning)
        mock_table.getcolshapestring.return_value = ["[4, 64]"] * 500_000_000

        mock_casatools.table.return_value = mock_table

        with patch("ms_inspect.util.casa_context._require_casatools", return_value=mock_casatools):
            result = run_preflight(str(ms))

        assert result["data"]["estimated_runtime_min"]["value"] > 10
