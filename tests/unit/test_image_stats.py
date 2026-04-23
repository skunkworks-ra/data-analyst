"""
Unit tests for ms_image_stats (src/ms_inspect/tools/image_stats.py).

No CASA required — tests the _extract_beam helper and the path-not-found
error path using a mock ia.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ms_inspect.tools.image_stats import _extract_beam


class TestExtractBeam:
    def test_single_beam(self):
        beam_info = {
            "major": {"value": 2.5, "unit": "arcsec"},
            "minor": {"value": 1.8, "unit": "arcsec"},
            "positionangle": {"value": 45.0, "unit": "deg"},
        }
        major, minor, pa = _extract_beam(beam_info)
        assert major == pytest.approx(2.5)
        assert minor == pytest.approx(1.8)
        assert pa == pytest.approx(45.0)

    def test_multi_beam_cube(self):
        beam_info = {
            "nChannels": 2,
            "nStokes": 1,
            "beams": {
                "*0": {
                    "*0": {
                        "major": {"value": 3.0, "unit": "arcsec"},
                        "minor": {"value": 2.0, "unit": "arcsec"},
                        "positionangle": {"value": 10.0, "unit": "deg"},
                    }
                },
                "*1": {
                    "*0": {
                        "major": {"value": 3.1, "unit": "arcsec"},
                        "minor": {"value": 2.1, "unit": "arcsec"},
                        "positionangle": {"value": 11.0, "unit": "deg"},
                    }
                },
            },
        }
        major, minor, pa = _extract_beam(beam_info)
        assert major == pytest.approx(3.0)
        assert minor == pytest.approx(2.0)
        assert pa == pytest.approx(10.0)

    def test_missing_beam_returns_none(self):
        major, minor, pa = _extract_beam({})
        assert major is None
        assert minor is None
        assert pa is None


class TestRunPathValidation:
    def test_missing_image_raises(self):
        from ms_inspect.exceptions import MSNotFoundError

        with pytest.raises(MSNotFoundError):
            from ms_inspect.tools.image_stats import run

            run("/nonexistent/path/target.image")

    def test_missing_psf_warns_and_continues(self, tmp_path):
        """A missing psf_path should add a warning but not abort."""
        image_dir = tmp_path / "target.image"
        image_dir.mkdir()

        fake_stats_robust = {"medabsdevmed": [0.001]}
        fake_stats = {"max": [0.5], "rms": [0.002]}
        fake_beam = {
            "major": {"value": 2.5, "unit": "arcsec"},
            "minor": {"value": 1.8, "unit": "arcsec"},
            "positionangle": {"value": 30.0, "unit": "deg"},
        }

        mock_ia = MagicMock()
        mock_ia.open.return_value = True
        mock_ia.statistics.side_effect = [fake_stats_robust, fake_stats]
        mock_ia.restoringbeam.return_value = fake_beam

        mock_casatools = MagicMock()
        mock_casatools.image.return_value = mock_ia

        with patch("ms_inspect.util.casa_context._require_casatools", return_value=mock_casatools):
            from ms_inspect.tools.image_stats import run

            result = run(str(image_dir), psf_path="/no/such/psf.psf")

        assert result["status"] == "ok"
        assert any("psf_path does not exist" in w for w in result["warnings"])
        assert result["data"]["rms_jy"]["value"] == pytest.approx(1.4826 * 0.001, rel=1e-4)
        assert result["data"]["peak_jy"]["value"] == pytest.approx(0.5)
        assert result["data"]["dynamic_range"]["value"] == pytest.approx(
            0.5 / (1.4826 * 0.001), rel=1e-3
        )
