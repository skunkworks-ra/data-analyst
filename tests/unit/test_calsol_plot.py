"""
Unit tests for ms_calsol_plot (src/ms_inspect/tools/calsol_plot.py).

No CASA or real caltables required. The module reads caltable columns directly
(it does NOT call ms_calsol_stats), so these tests mock the two disk seams —
_read_solutions() and _call_provenance() — and verify:
- path validation (error envelope for missing/invalid caltable)
- view routing by VisCal type (G/B/K/Kcross/Df/Xf)
- HTML file is written and is valid markup
- response envelope fields
"""

from __future__ import annotations

from unittest.mock import patch

import numpy as np

from ms_inspect.tools import calsol_plot

_ANT_NAMES = ["ea01", "ea02", "ea03"]
_FIELD_NAMES = {0: "3C286", 1: "J0137"}
_N_ANT = 3


def _sol(vc: str, n_chan: int, n_poln: int = 2, n_spw: int = 2) -> dict:
    """Build a fake _read_solutions() result for the given VisCal type."""
    is_delay = vc.upper().startswith("K")
    chan_freqs = {s: (1.0 + 0.1 * s + 0.001 * np.arange(n_chan)).tolist() for s in range(n_spw)}
    rows = []
    for ant in range(_N_ANT):
        for spw in range(n_spw):
            if is_delay:
                param = np.full((n_poln, n_chan), 0.5)  # FPARAM: real delays (ns)
            else:
                param = np.full((n_poln, n_chan), 1.0 + 0.1j)  # CPARAM
            rows.append(
                {
                    "ant": ant,
                    "spw": spw,
                    "field": 0,
                    "time": 5e9 + 10.0 * ant,
                    "param": param,
                    "flag": np.zeros((n_poln, n_chan), dtype=bool),
                }
            )
    return {
        "vc": vc,
        "is_delay": is_delay,
        "is_freq_dep": vc in ("B", "Df", "Xf", "Bf"),
        "ant_names": _ANT_NAMES,
        "spw_ids": list(range(n_spw)),
        "field_names": _FIELD_NAMES,
        "n_poln": n_poln,
        "poln_labels": ["R", "L"] if n_poln == 2 else [f"P{i}" for i in range(n_poln)],
        "chan_freqs": chan_freqs,
        "rows": rows,
    }


_PROV = {"caltable": "fake.g", "ms_name": "fake.ms", "task": "gaincal", "params": {}}


def _run(tmp_path, sol) -> dict:
    caltable = tmp_path / "fake.g"
    caltable.mkdir(exist_ok=True)
    out = tmp_path / "plots"
    with (
        patch("ms_inspect.tools.calsol_plot._read_solutions", return_value=sol),
        patch("ms_inspect.tools.calsol_plot._call_provenance", return_value=_PROV),
    ):
        return calsol_plot.run(str(caltable), str(out))


class TestPathValidation:
    def test_missing_caltable_returns_error(self, tmp_path):
        result = calsol_plot.run(str(tmp_path / "nope.g"), str(tmp_path))
        assert result["status"] == "error"
        assert result["error_type"] == "CALTABLE_NOT_FOUND"

    def test_caltable_is_file_not_dir(self, tmp_path):
        f = tmp_path / "not_a_table.g"
        f.write_text("x")
        result = calsol_plot.run(str(f), str(tmp_path))
        assert result["status"] == "error"
        assert result["error_type"] == "CALTABLE_NOT_FOUND"


class TestViewRouting:
    def test_g_routes_to_time_view(self, tmp_path):
        result = _run(tmp_path, _sol("G", n_chan=1))
        assert result["data"]["view"]["value"] == "amp_phase_vs_time"

    def test_b_routes_to_freq_view(self, tmp_path):
        result = _run(tmp_path, _sol("B", n_chan=64))
        assert result["data"]["view"]["value"] == "amp_phase_vs_freq"

    def test_k_routes_to_delay_view(self, tmp_path):
        result = _run(tmp_path, _sol("K", n_chan=1))
        assert result["data"]["view"]["value"] == "delay_vs_antenna"

    def test_kcross_routes_to_kcross_view(self, tmp_path):
        result = _run(tmp_path, _sol("Kcross", n_chan=1))
        assert result["data"]["view"]["value"] == "kcross_vs_antenna"

    def test_df_routes_to_amp_vs_freq(self, tmp_path):
        result = _run(tmp_path, _sol("Df", n_chan=64))
        assert result["data"]["view"]["value"] == "amp_vs_freq"

    def test_xf_routes_to_phase_vs_freq(self, tmp_path):
        result = _run(tmp_path, _sol("Xf", n_chan=64))
        assert result["data"]["view"]["value"] == "phase_vs_freq"


class TestHtmlOutput:
    def test_html_file_created_g(self, tmp_path):
        result = _run(tmp_path, _sol("G", n_chan=1))
        assert result["status"] == "ok"
        html_path = result["data"]["html_path"]["value"]
        assert html_path.endswith("_plot.html")
        with open(html_path) as fh:
            assert len(fh.read()) > 1000

    def test_html_file_created_b(self, tmp_path):
        result = _run(tmp_path, _sol("B", n_chan=64))
        with open(result["data"]["html_path"]["value"]) as fh:
            assert len(fh.read()) > 1000

    def test_html_file_created_k(self, tmp_path):
        result = _run(tmp_path, _sol("K", n_chan=1))
        with open(result["data"]["html_path"]["value"]) as fh:
            assert len(fh.read()) > 1000

    def test_html_is_valid_markup(self, tmp_path):
        result = _run(tmp_path, _sol("G", n_chan=1))
        with open(result["data"]["html_path"]["value"]) as fh:
            html = fh.read().lower()
        assert "<html" in html
        assert "bokeh" in html

    def test_output_dir_created(self, tmp_path):
        result = _run(tmp_path, _sol("G", n_chan=1))
        assert result["status"] == "ok"
        assert (tmp_path / "plots").is_dir()


class TestResponseEnvelope:
    def test_status_ok(self, tmp_path):
        result = _run(tmp_path, _sol("G", n_chan=1))
        assert result["status"] == "ok"
        assert result["tool"] == calsol_plot.TOOL_NAME

    def test_viscal_returned(self, tmp_path):
        result = _run(tmp_path, _sol("G", n_chan=1))
        assert result["data"]["viscal"]["value"] == "G"

    def test_provenance_fields_returned(self, tmp_path):
        result = _run(tmp_path, _sol("G", n_chan=1))
        assert result["data"]["ms_name"]["value"] == "fake.ms"
        assert result["data"]["call_params"]["value"] == {}
