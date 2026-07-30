"""
Unit tests for ms_inspect/tools/workflow_status.py — filesystem-only checks
plus the UNAVAILABLE-vs-incomplete distinction on read failures.

No CASA required; CASA-backed reads are mocked.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from ms_inspect.tools.workflow_status import run


class TestWorkflowStatus:
    def _make_valid_ms(self, path: Path) -> Path:
        path.mkdir()
        (path / "table.info").write_text("Type = Measurement Set\n")
        return path

    def test_ms_missing_raises(self, tmp_path):
        # validate_ms_path() (shared with every other ms_inspect tool)
        # raises MSNotFoundError before run() gets a chance to report
        # ms_valid=False itself; this is pre-existing behaviour, not part
        # of this change.
        import pytest

        from ms_inspect.exceptions import MSNotFoundError

        with pytest.raises(MSNotFoundError):
            run(str(tmp_path / "missing.ms"), str(tmp_path / "work"))

    def test_state_subtable_absent_is_incomplete_not_unavailable(self, tmp_path):
        # STATE directory genuinely does not exist -> legitimately "not
        # populated yet", not a read failure.
        ms = self._make_valid_ms(tmp_path / "obs.ms")
        result = run(str(ms), str(tmp_path / "work"))
        assert result["data"]["intents_populated"]["value"] is False
        assert result["data"]["intents_populated"]["flag"] == "COMPLETE"
        assert result["data"]["next_recommended_step"] == "set_intents"

    def test_state_read_failure_yields_unavailable_not_incomplete(self, tmp_path):
        # STATE directory exists (so the stage may well be done) but the
        # read itself blows up -- this must surface as UNAVAILABLE, not as
        # a false "intents not populated".
        ms = self._make_valid_ms(tmp_path / "obs.ms")
        (ms / "STATE").mkdir()

        @contextmanager
        def _broken_open_table(*args, **kwargs):
            raise RuntimeError("table locked by another process")
            yield  # pragma: no cover

        with patch(
            "ms_inspect.tools.workflow_status.open_table",
            side_effect=_broken_open_table,
        ):
            result = run(str(ms), str(tmp_path / "work"))

        assert result["data"]["intents_populated"]["value"] is False
        assert result["data"]["intents_populated"]["flag"] == "UNAVAILABLE"
        assert "table locked" in result["data"]["intents_populated"]["note"]
        # Must not be silently treated as "incomplete" for step derivation.
        assert result["data"]["next_recommended_step"] == "unknown_probe_failed:intents_populated"

    def test_corrected_data_read_failure_yields_unavailable(self, tmp_path):
        # Reach the corrected_populated probe by satisfying every earlier
        # gate, then break only the MAIN-table read.
        ms = self._make_valid_ms(tmp_path / "obs.ms")
        (ms / "STATE").mkdir()
        workdir = tmp_path / "work"
        workdir.mkdir()
        (workdir / "calibrators.ms").mkdir()
        (workdir / "calibrators.ms" / "table.info").write_text("Type = Measurement Set\n")
        (workdir / "gain_curves.gc").mkdir()
        (workdir / "opacities.opac").mkdir()
        (workdir / "init_gain.g").mkdir()
        (workdir / "BP0.b").mkdir()

        @contextmanager
        def _selective_open_table(table_path, *args, **kwargs):
            if table_path.endswith("STATE"):
                # Let the STATE probe report "populated" normally via a
                # minimal fake table object.
                class _FakeTb:
                    def nrows(self):
                        return 1

                yield _FakeTb()
            else:
                raise RuntimeError("permission denied")

        with patch(
            "ms_inspect.tools.workflow_status.open_table",
            side_effect=_selective_open_table,
        ):
            result = run(str(ms), str(workdir))

        assert result["data"]["corrected_populated"]["value"] is False
        assert result["data"]["corrected_populated"]["flag"] == "UNAVAILABLE"
        assert "permission denied" in result["data"]["corrected_populated"]["note"]
        assert result["data"]["next_recommended_step"] == "unknown_probe_failed:corrected_populated"
