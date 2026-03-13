"""
Unit tests for ms_setjy.

No CASA required. Tests cover:
- _build_setjy_block: script fragment generation
- run: workdir validation, catalogue cross-match warnings
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from ms_modify.setjy import _DEFAULT_STANDARD, _build_setjy_block

# ---------------------------------------------------------------------------
# _build_setjy_block
# ---------------------------------------------------------------------------

class TestBuildSetjyBlock:
    def test_contains_field_name(self):
        block = _build_setjy_block("3C286", _DEFAULT_STANDARD)
        assert "3C286" in block

    def test_contains_standard(self):
        block = _build_setjy_block("3C147", _DEFAULT_STANDARD)
        assert _DEFAULT_STANDARD in block

    def test_contains_setjy_call(self):
        block = _build_setjy_block("3C48", _DEFAULT_STANDARD)
        assert "setjy(" in block


# ---------------------------------------------------------------------------
# ms_setjy.run — workdir and catalogue logic (mocked CASA reads)
# ---------------------------------------------------------------------------

class TestSetjyRun:
    def _make_ms(self, tmp_path) -> Path:
        ms = tmp_path / "test.ms"
        ms.mkdir()
        (ms / "table.info").write_text("Type = Measurement Set\n")
        return ms

    def test_missing_workdir_raises(self, tmp_path):
        from ms_inspect.exceptions import ComputationError
        from ms_modify.setjy import run
        ms = self._make_ms(tmp_path)
        with patch("ms_modify.setjy._get_field_names", return_value=["3C286"]), pytest.raises(ComputationError, match="workdir does not exist"):
            run(str(ms), str(tmp_path / "nodir"))

    def test_known_flux_field_included(self, tmp_path):
        from ms_modify.setjy import run
        ms = self._make_ms(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        with patch("ms_modify.setjy._get_field_names", return_value=["3C286", "J0319+4130"]):
            result = run(str(ms), str(workdir), execute=False)
        flux_fields = result["data"]["flux_fields"]["value"]
        assert "3C286" in flux_fields

    def test_unknown_field_is_skipped(self, tmp_path):
        from ms_modify.setjy import run
        ms = self._make_ms(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        with patch("ms_modify.setjy._get_field_names", return_value=["J1331+3030", "phase_cal"]):
            result = run(str(ms), str(workdir), execute=False)
        skipped = result["data"]["skipped_fields"]["value"]
        assert "phase_cal" in skipped

    def test_script_written_execute_false(self, tmp_path):
        from ms_modify.setjy import run
        ms = self._make_ms(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        with patch("ms_modify.setjy._get_field_names", return_value=["3C147"]):
            run(str(ms), str(workdir), execute=False)
        assert (workdir / "setjy.py").exists()

    def test_resolved_source_triggers_warning(self, tmp_path):
        """A resolved flux calibrator in the catalogue should produce a warning."""
        from ms_inspect.util.calibrators import CATALOGUE
        from ms_modify.setjy import run

        # Find a resolved flux calibrator in the catalogue
        resolved_flux = next(
            (e.canonical_name for e in CATALOGUE if e.resolved and "flux" in e.role),
            None,
        )
        if resolved_flux is None:
            pytest.skip("No resolved flux calibrator found in catalogue")

        ms = self._make_ms(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        with patch("ms_modify.setjy._get_field_names", return_value=[resolved_flux]):
            result = run(str(ms), str(workdir), execute=False)
        assert len(result["warnings"]) > 0
        all_warnings = " ".join(result["warnings"])
        assert resolved_flux in all_warnings

    def test_no_flux_fields_triggers_warning(self, tmp_path):
        from ms_modify.setjy import run
        ms = self._make_ms(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        with patch("ms_modify.setjy._get_field_names", return_value=["J1234+5678"]):
            result = run(str(ms), str(workdir), execute=False)
        assert result["data"]["n_flux_fields"] == 0
        assert any("No flux standard" in w for w in result["warnings"])

    def test_script_contains_perley_butler(self, tmp_path):
        from ms_modify.setjy import run
        ms = self._make_ms(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        with patch("ms_modify.setjy._get_field_names", return_value=["3C286"]):
            run(str(ms), str(workdir), execute=False)
        script = (workdir / "setjy.py").read_text()
        assert "Perley-Butler 2017" in script
