"""
Unit tests for ms_calsol_plot (src/ms_inspect/tools/calsol_plot.py).

No CASA or real caltables required. Tests mock calsol_stats.run() and verify:
- path validation
- NPZ array contents and shapes
- HTML file is written and non-empty
- response envelope fields
- error propagation from calsol_stats
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import numpy as np

from ms_inspect.tools import calsol_plot

# ---------------------------------------------------------------------------
# Shared fake stats responses
# ---------------------------------------------------------------------------

_ANT_NAMES = ["ea01", "ea02", "ea03"]
_SPW_IDS = [0, 1]
_FIELD_IDS = [0, 1]
_FIELD_NAMES = ["3C286", "J0137"]
_N_ANT = 3
_N_SPW = 2
_N_FIELD = 2


def _fld(value, flag="COMPLETE"):
    return {"value": value, "flag": flag}


def _g_stats() -> dict:
    shape = (_N_ANT, _N_SPW, _N_FIELD)
    return {
        "status": "ok",
        "data": {
            "table_type": _fld("G"),
            "n_antennas": _fld(_N_ANT),
            "n_spw": _fld(_N_SPW),
            "n_field": _fld(_N_FIELD),
            "ant_names": _fld(_ANT_NAMES),
            "spw_ids": _fld(_SPW_IDS),
            "field_ids": _fld(_FIELD_IDS),
            "field_names": _fld(_FIELD_NAMES),
            "flagged_frac": _fld(np.zeros(shape).tolist()),
            "snr_mean": _fld(np.full(shape, 30.0).tolist()),
            "amp_mean": _fld(np.ones(shape).tolist()),
            "amp_std": _fld(np.full(shape, 0.01).tolist()),
            "phase_mean_deg": _fld(np.zeros(shape).tolist()),
            "phase_rms_deg": _fld(np.full(shape, 5.0).tolist()),
            "overall_flagged_frac": _fld(0.0),
            "n_antennas_lost": _fld(0),
            "antennas_lost": _fld([]),
        },
        "warnings": [],
        "provenance": {"casa_calls": [], "casatools_version": "test"},
    }


def _b_stats() -> dict:
    n_chan = 8
    shape = (_N_ANT, _N_SPW, _N_FIELD)
    shape_4d = (_N_ANT, _N_SPW, _N_FIELD, n_chan)
    return {
        "status": "ok",
        "data": {
            "table_type": _fld("B"),
            "n_antennas": _fld(_N_ANT),
            "n_spw": _fld(_N_SPW),
            "n_field": _fld(_N_FIELD),
            "ant_names": _fld(_ANT_NAMES),
            "spw_ids": _fld(_SPW_IDS),
            "field_ids": _fld(_FIELD_IDS),
            "field_names": _fld(_FIELD_NAMES),
            "flagged_frac": _fld(np.zeros(shape).tolist()),
            "snr_mean": _fld(np.full(shape, 50.0).tolist()),
            "amp_mean": _fld(np.ones(shape).tolist()),
            "amp_std": _fld(np.full(shape, 0.005).tolist()),
            "phase_mean_deg": _fld(np.zeros(shape).tolist()),
            "phase_rms_deg": _fld(np.full(shape, 2.0).tolist()),
            "amp_array": _fld(np.ones(shape_4d).tolist()),
            "overall_flagged_frac": _fld(0.0),
            "n_antennas_lost": _fld(0),
            "antennas_lost": _fld([]),
        },
        "warnings": [],
        "provenance": {"casa_calls": [], "casatools_version": "test"},
    }


def _k_stats() -> dict:
    n_corr = 2
    shape = (_N_ANT, _N_SPW, _N_FIELD)
    shape_4d = (_N_ANT, _N_SPW, _N_FIELD, n_corr)
    return {
        "status": "ok",
        "data": {
            "table_type": _fld("K"),
            "n_antennas": _fld(_N_ANT),
            "n_spw": _fld(_N_SPW),
            "n_field": _fld(_N_FIELD),
            "ant_names": _fld(_ANT_NAMES),
            "spw_ids": _fld(_SPW_IDS),
            "field_ids": _fld(_FIELD_IDS),
            "field_names": _fld(_FIELD_NAMES),
            "flagged_frac": _fld(np.zeros(shape).tolist()),
            "snr_mean": _fld(np.full(shape, 20.0).tolist()),
            "delay_ns": _fld(np.random.uniform(-5, 5, shape_4d).tolist()),
            "delay_rms_ns": _fld(np.full((_N_SPW, _N_FIELD), 1.5).tolist()),
            "overall_flagged_frac": _fld(0.0),
            "n_antennas_lost": _fld(0),
            "antennas_lost": _fld([]),
        },
        "warnings": [],
        "provenance": {"casa_calls": [], "casatools_version": "test"},
    }


# ---------------------------------------------------------------------------
# Path validation
# ---------------------------------------------------------------------------


class TestPathValidation:
    def test_missing_caltable_returns_error(self, tmp_path):
        result = calsol_plot.run(str(tmp_path / "nonexistent.g"), str(tmp_path / "out"))
        assert result["status"] == "error"
        assert result["error_type"] == "CALTABLE_NOT_FOUND"

    def test_caltable_is_file_not_dir(self, tmp_path):
        f = tmp_path / "notatable.g"
        f.write_text("x")
        result = calsol_plot.run(str(f), str(tmp_path / "out"))
        assert result["status"] == "error"
        assert result["error_type"] == "CALTABLE_NOT_FOUND"

    def test_error_from_calsol_stats_propagates(self, tmp_path):
        tbl = tmp_path / "gain.g"
        tbl.mkdir()
        bad_stats = {
            "status": "error",
            "error_type": "CASA_OPEN_FAILED",
            "message": "lock",
        }
        with patch("ms_inspect.tools.calsol_plot.calsol_stats.run", return_value=bad_stats):
            result = calsol_plot.run(str(tbl), str(tmp_path / "out"))
        assert result["status"] == "error"
        assert result["error_type"] == "CASA_OPEN_FAILED"


# ---------------------------------------------------------------------------
# NPZ output
# ---------------------------------------------------------------------------


class TestNpzOutput:
    def _run_g(self, tmp_path):
        tbl = tmp_path / "gain.g"
        tbl.mkdir()
        out = tmp_path / "out"
        with patch("ms_inspect.tools.calsol_plot.calsol_stats.run", return_value=_g_stats()):
            result = calsol_plot.run(str(tbl), str(out))
        return result, out

    def test_npz_file_created(self, tmp_path):
        result, out = self._run_g(tmp_path)
        assert result["status"] == "ok"
        assert Path(result["data"]["npz_path"]["value"]).exists()

    def test_npz_contains_expected_arrays(self, tmp_path):
        result, out = self._run_g(tmp_path)
        npz = np.load(result["data"]["npz_path"]["value"], allow_pickle=True)
        for key in (
            "ant_names",
            "spw_ids",
            "field_ids",
            "field_names",
            "flagged_frac",
            "snr_mean",
            "amp_mean",
            "phase_rms_deg",
        ):
            assert key in npz, f"Missing key: {key}"

    def test_npz_amp_mean_shape(self, tmp_path):
        result, out = self._run_g(tmp_path)
        npz = np.load(result["data"]["npz_path"]["value"])
        assert npz["amp_mean"].shape == (_N_ANT, _N_SPW, _N_FIELD)

    def test_npz_ant_names(self, tmp_path):
        result, out = self._run_g(tmp_path)
        npz = np.load(result["data"]["npz_path"]["value"], allow_pickle=True)
        assert list(npz["ant_names"]) == _ANT_NAMES

    def test_npz_b_contains_amp_array(self, tmp_path):
        tbl = tmp_path / "BP.b"
        tbl.mkdir()
        out = tmp_path / "out"
        with patch("ms_inspect.tools.calsol_plot.calsol_stats.run", return_value=_b_stats()):
            result = calsol_plot.run(str(tbl), str(out))
        npz = np.load(result["data"]["npz_path"]["value"])
        assert "amp_array" in npz
        assert npz["amp_array"].shape == (_N_ANT, _N_SPW, _N_FIELD, 8)

    def test_npz_k_contains_delay_arrays(self, tmp_path):
        tbl = tmp_path / "delay.k"
        tbl.mkdir()
        out = tmp_path / "out"
        with patch("ms_inspect.tools.calsol_plot.calsol_stats.run", return_value=_k_stats()):
            result = calsol_plot.run(str(tbl), str(out))
        npz = np.load(result["data"]["npz_path"]["value"])
        assert "delay_ns" in npz
        assert "delay_rms_ns" in npz
        assert npz["delay_rms_ns"].shape == (_N_SPW, _N_FIELD)


# ---------------------------------------------------------------------------
# HTML output
# ---------------------------------------------------------------------------


class TestHtmlOutput:
    def _run(self, tmp_path, stats_fn, stem):
        tbl = tmp_path / stem
        tbl.mkdir()
        out = tmp_path / "out"
        with patch("ms_inspect.tools.calsol_plot.calsol_stats.run", return_value=stats_fn()):
            return calsol_plot.run(str(tbl), str(out))

    def test_html_file_created_g(self, tmp_path):
        result = self._run(tmp_path, _g_stats, "gain.g")
        assert result["status"] == "ok"
        html_path = Path(result["data"]["html_path"]["value"])
        assert html_path.exists()
        assert html_path.stat().st_size > 1000

    def test_html_file_created_b(self, tmp_path):
        result = self._run(tmp_path, _b_stats, "BP.b")
        html_path = Path(result["data"]["html_path"]["value"])
        assert html_path.exists()
        assert html_path.stat().st_size > 1000

    def test_html_file_created_k(self, tmp_path):
        result = self._run(tmp_path, _k_stats, "delay.k")
        html_path = Path(result["data"]["html_path"]["value"])
        assert html_path.exists()
        assert html_path.stat().st_size > 1000

    def test_html_is_valid_markup(self, tmp_path):
        result = self._run(tmp_path, _g_stats, "gain.g")
        html = Path(result["data"]["html_path"]["value"]).read_text()
        assert "<html" in html.lower()
        assert "bokeh" in html.lower()


# ---------------------------------------------------------------------------
# Response envelope
# ---------------------------------------------------------------------------


class TestResponseEnvelope:
    def _run_g(self, tmp_path):
        tbl = tmp_path / "gain.g"
        tbl.mkdir()
        with patch("ms_inspect.tools.calsol_plot.calsol_stats.run", return_value=_g_stats()):
            return calsol_plot.run(str(tbl), str(tmp_path / "out"))

    def test_status_ok(self, tmp_path):
        assert self._run_g(tmp_path)["status"] == "ok"

    def test_table_type_returned(self, tmp_path):
        result = self._run_g(tmp_path)
        assert result["data"]["table_type"]["value"] == "G"

    def test_axis_dimensions_returned(self, tmp_path):
        result = self._run_g(tmp_path)
        assert result["data"]["n_antennas"]["value"] == _N_ANT
        assert result["data"]["n_spw"]["value"] == _N_SPW
        assert result["data"]["n_field"]["value"] == _N_FIELD

    def test_output_dir_created(self, tmp_path):
        tbl = tmp_path / "gain.g"
        tbl.mkdir()
        out = tmp_path / "nested" / "output"
        with patch("ms_inspect.tools.calsol_plot.calsol_stats.run", return_value=_g_stats()):
            result = calsol_plot.run(str(tbl), str(out))
        assert result["status"] == "ok"
        assert out.exists()
