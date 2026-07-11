"""
Unit tests for ms_setjy_polcal (script-generation path; no CASA).

The execute=True probe runs CASA setjy and is integration-only. These tests
cover the execute=False path: pol terms fit from the catalogue, and the
self-contained probe→fit→apply script that is emitted.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


def _make_ms(tmp_path) -> Path:
    ms = tmp_path / "test.ms"
    ms.mkdir()
    (ms / "table.info").write_text("Type = Measurement Set\n")
    return ms


def _make_workdir(tmp_path) -> Path:
    workdir = tmp_path / "work"
    workdir.mkdir()
    return workdir


class TestRunValidation:
    def test_missing_workdir_raises(self, tmp_path):
        from ms_inspect.exceptions import ComputationError
        from ms_modify.setjy_polcal import run

        ms = _make_ms(tmp_path)
        with pytest.raises(ComputationError, match="workdir does not exist"):
            run(str(ms), "3C286", str(tmp_path / "nodir"), reffreq_ghz=1.5)

    def test_unknown_calibrator_raises(self, tmp_path):
        from ms_inspect.exceptions import ComputationError
        from ms_modify.setjy_polcal import run

        ms = _make_ms(tmp_path)
        workdir = _make_workdir(tmp_path)
        with pytest.raises(ComputationError, match="lookup failed"):
            run(str(ms), "J9999+0000", str(workdir), reffreq_ghz=1.5)


class TestScriptGeneration:
    def _run(self, tmp_path, **kw):
        from ms_modify.setjy_polcal import run

        ms = _make_ms(tmp_path)
        workdir = _make_workdir(tmp_path)
        result = run(str(ms), "3C286", str(workdir), reffreq_ghz=1.5, **kw)
        script = (workdir / "setjy_polcal.py").read_text()
        return result, script

    def test_3c286_succeeds_without_catalogue_flux(self, tmp_path):
        # 3C286's 2019 epoch has no Stokes I; the tool must still produce a script.
        result, _ = self._run(tmp_path)
        assert result["status"] == "ok"
        assert result["data"]["stokes_i_source"].startswith("Perley-Butler 2017")

    def test_script_is_valid_python(self, tmp_path):
        _, script = self._run(tmp_path)
        ast.parse(script)  # raises SyntaxError if malformed

    def test_script_probes_perley_butler_and_applies_manual(self, tmp_path):
        _, script = self._run(tmp_path)
        # Probe with PB virtual model, then apply the manual polarized model.
        assert (
            "standard='Perley-Butler 2017'" in script or 'standard="Perley-Butler 2017"' in script
        )
        assert "usescratch=False" in script  # probe
        assert 'standard="manual"' in script  # apply
        assert "usescratch=True" in script  # apply writes MODEL_DATA
        assert "spwsforfield" in script  # SPW auto-discovery
        assert "np.linalg.lstsq" in script  # in-script Stokes I fit

    def test_pol_coeffs_embedded_as_literals(self, tmp_path):
        result, script = self._run(tmp_path)
        # The polindex/polangle from the catalogue must appear in the script.
        c0 = result["data"]["polindex_c0"]
        assert f"{c0}" in script or "polindex = [" in script

    def test_min_chunk_mhz_threaded(self, tmp_path):
        result, script = self._run(tmp_path, min_chunk_mhz=16.0)
        assert result["data"]["min_chunk_mhz"] == 16.0
        assert "min_chunk_mhz = 16.0" in script

    def test_polcoeffs_match_2019_lband(self, tmp_path):
        # ~9.8% pol, PA ~33° near 1.5 GHz from the 2019 table.
        result, _ = self._run(tmp_path)
        assert result["data"]["polindex_c0"] == pytest.approx(0.099, abs=0.01)
