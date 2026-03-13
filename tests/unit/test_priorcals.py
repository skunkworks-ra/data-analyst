"""
Unit tests for ms_generate_priorcals and ms_verify_priorcals.

No CASA required. Tests cover:
- priorcals.run: script generation, workdir validation
- priorcals_check.run: table existence and validation logic
"""

from __future__ import annotations

from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# ms_generate_priorcals
# ---------------------------------------------------------------------------


class TestGeneratePriorcals:
    def _make_ms(self, tmp_path) -> Path:
        ms = tmp_path / "test.ms"
        ms.mkdir()
        (ms / "table.info").write_text("Type = Measurement Set\n")
        return ms

    def test_missing_workdir_raises(self, tmp_path):
        from ms_inspect.exceptions import ComputationError
        from ms_modify.priorcals import run

        ms = self._make_ms(tmp_path)
        with pytest.raises(ComputationError, match="workdir does not exist"):
            run(str(ms), str(tmp_path / "nodir"))

    def test_execute_false_writes_script(self, tmp_path):
        from ms_modify.priorcals import run

        ms = self._make_ms(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        result = run(str(ms), str(workdir), execute=False)
        assert result["status"] == "ok"
        assert (workdir / "priorcals.py").exists()

    def test_script_contains_all_four_gencal_calls(self, tmp_path):
        from ms_modify.priorcals import run

        ms = self._make_ms(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        run(str(ms), str(workdir), execute=False)
        script = (workdir / "priorcals.py").read_text()
        for caltype in ("gc", "opac", "rq", "antpos"):
            assert f'caltype="{caltype}"' in script or f"caltype='{caltype}'" in script

    def test_script_contains_ms_path(self, tmp_path):
        from ms_modify.priorcals import run

        ms = self._make_ms(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        run(str(ms), str(workdir), execute=False)
        script = (workdir / "priorcals.py").read_text()
        assert str(ms) in script

    def test_response_includes_expected_tables(self, tmp_path):
        from ms_modify.priorcals import run

        ms = self._make_ms(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        result = run(str(ms), str(workdir), execute=False)
        expected = result["data"]["expected_tables"]["value"]
        assert any("gain_curves.gc" in t for t in expected)
        assert any("opacities.opac" in t for t in expected)

    def test_script_contains_rq_mjd_threshold(self, tmp_path):
        from ms_modify.priorcals import _RQ_MJD_THRESHOLD, run

        ms = self._make_ms(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        run(str(ms), str(workdir), execute=False)
        script = (workdir / "priorcals.py").read_text()
        assert str(_RQ_MJD_THRESHOLD) in script


# ---------------------------------------------------------------------------
# ms_verify_priorcals — _check_table logic
# ---------------------------------------------------------------------------


class TestVerifyPriorcals:
    def _make_ms(self, tmp_path) -> Path:
        ms = tmp_path / "test.ms"
        ms.mkdir()
        (ms / "table.info").write_text("Type = Measurement Set\n")
        return ms

    def test_missing_workdir_raises(self, tmp_path):
        from ms_inspect.exceptions import ComputationError
        from ms_inspect.tools.priorcals_check import run

        ms = self._make_ms(tmp_path)
        with pytest.raises(ComputationError, match="workdir does not exist"):
            run(str(ms), str(tmp_path / "nodir"))

    def test_missing_tables_reported(self, tmp_path):
        from ms_inspect.tools.priorcals_check import _check_table

        result = _check_table(str(tmp_path / "nonexistent.gc"))
        assert not result["exists"]
        assert not result["valid"]
        assert result["n_rows"] == 0

    def test_all_missing_reports_n_missing(self, tmp_path):
        from ms_inspect.tools.priorcals_check import run

        ms = self._make_ms(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        result = run(str(ms), str(workdir), table_names=["gain_curves.gc", "antpos.ap"])
        assert result["data"]["n_missing"]["value"] == 2
        assert not result["data"]["all_valid"]["value"]

    def test_custom_table_names_respected(self, tmp_path):
        from ms_inspect.tools.priorcals_check import run

        ms = self._make_ms(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        result = run(str(ms), str(workdir), table_names=["gain_curves.gc"])
        assert result["data"]["n_checked"]["value"] == 1

    def test_default_checks_four_tables(self, tmp_path):
        from ms_inspect.tools.priorcals_check import run

        ms = self._make_ms(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        result = run(str(ms), str(workdir))
        assert result["data"]["n_checked"]["value"] == 4
