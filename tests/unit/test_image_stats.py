"""
Unit tests for ms_image_stats (src/ms_inspect/tools/image_stats.py).

No CASA required — tests the _extract_beam helper and the path-not-found
error path using a mock ia.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ms_inspect.tools.image_stats import _extract_beam, _plane_labels


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


class TestPlaneLabels:
    def test_iquv_cube_order(self):
        # Image axes [RA, Dec, Stokes, Freq]; remaining axes [2, 3].
        # Stokes axis=2 (len 4), spectral axis=3 (len 2).
        labels = _plane_labels(
            remaining_axes=[2, 3],
            remaining_shape=[4, 2],
            stokes_pix_axis=2,
            spec_pix_axis=3,
            stokes_names=["I", "Q", "U", "V"],
        )
        # C-order: stokes varies slowest, channel fastest.
        assert len(labels) == 8
        assert labels[0] == {"stokes_index": 0, "stokes": "I", "chan": 0}
        assert labels[1] == {"stokes_index": 0, "stokes": "I", "chan": 1}
        assert labels[2] == {"stokes_index": 1, "stokes": "Q", "chan": 0}
        assert labels[-1] == {"stokes_index": 3, "stokes": "V", "chan": 1}

    def test_freq_first_axis_order(self):
        # Image axes [RA, Dec, Freq, Stokes]; remaining [2, 3].
        labels = _plane_labels(
            remaining_axes=[2, 3],
            remaining_shape=[3, 2],
            stokes_pix_axis=3,
            spec_pix_axis=2,
            stokes_names=["I", "V"],
        )
        assert labels[0] == {"chan": 0, "stokes_index": 0, "stokes": "I"}
        assert labels[1] == {"chan": 0, "stokes_index": 1, "stokes": "V"}
        assert labels[2] == {"chan": 1, "stokes_index": 0, "stokes": "I"}

    def test_no_stokes_names_falls_back_to_index(self):
        labels = _plane_labels([2], [2], stokes_pix_axis=2, spec_pix_axis=None)
        assert labels[0] == {"stokes_index": 0}
        assert labels[1] == {"stokes_index": 1}


class TestClassifyDetection:
    """Three descriptive levels, no pass/fail component.

    Covers the pure label function only. It does not cover the response
    envelope, which needs a real CASA image.
    """

    def test_thresholds(self):
        from ms_inspect.tools.image_stats import _classify_detection

        assert _classify_detection(3.0) == "undetected"
        assert _classify_detection(5.0) == "undetected"  # boundary, inclusive
        assert _classify_detection(7.0) == "marginal"
        assert _classify_detection(10.0) == "detection"  # boundary, inclusive
        assert _classify_detection(120.0) == "detection"
        assert _classify_detection(None) == "unknown"

    def test_returns_a_bare_label_not_a_gate(self):
        """The old contract returned (label, passed). Nothing may reintroduce it."""
        from ms_inspect.tools.image_stats import _classify_detection

        assert isinstance(_classify_detection(7.0), str)

    def test_low_constant_is_reachable(self):
        """With two levels both branches below 10 returned 'marginal' and the
        5.0 constant carried no information. Three levels make it usable."""
        from ms_inspect.tools.image_stats import (
            _P2N_MARGINAL,
            _P2N_UNDETECTED,
            _classify_detection,
        )

        assert _classify_detection(_P2N_UNDETECTED - 0.1) == "undetected"
        assert _classify_detection(_P2N_UNDETECTED + 0.1) == "marginal"
        assert _classify_detection(_P2N_MARGINAL) == "detection"


class TestNoGateFields:
    """The no-gates contract: this module must not ship a boolean verdict."""

    def test_source_has_no_detection_pass(self):
        import inspect

        from ms_inspect.tools import image_stats

        src = inspect.getsource(image_stats)
        assert "detection_pass" not in src
        assert "do not report this as a detection" not in src


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
