"""
Unit tests for ms_apply_initial_rflag.

No CASA required. Tests cover:
- _build_script: generated driver script content (two direct flagdata passes)
- run: workdir validation, script file creation, the MODEL_DATA guard

`run()` now opens the MS to check for MODEL_DATA before writing a script (the
whole tool computes on datacolumn='residual' = CORRECTED - MODEL, which is
meaningless without it — T8). `open_table` is monkeypatched throughout this
class (MODEL_DATA present by default; the one negative test overrides it) so
the guard's two branches are exercised without a real MS or casatools ever
actually running, consistent with the rest of this suite.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

from ms_modify.initial_rflag import _build_script


@contextmanager
def _fake_table(colnames: list[str]):
    class _T:
        def colnames(self):
            return colnames

    yield _T()


# ---------------------------------------------------------------------------
# _build_script
# ---------------------------------------------------------------------------


class TestBuildScript:
    def test_contains_rflag(self):
        script = _build_script("/data/x.ms", "0", 5.0, 5.0, 4.0, 4.0)
        assert "rflag" in script

    def test_contains_tfcrop(self):
        script = _build_script("/data/x.ms", "0", 5.0, 5.0, 4.0, 4.0)
        assert "tfcrop" in script

    def test_datacolumn_is_residual(self):
        script = _build_script("/data/x.ms", "0", 5.0, 5.0, 4.0, 4.0)
        assert 'datacolumn="residual"' in script

    def test_field_embedded(self):
        script = _build_script("/data/x.ms", "3C147", 5.0, 5.0, 4.0, 4.0)
        assert "3C147" in script

    def test_custom_thresholds_embedded(self):
        script = _build_script("/data/x.ms", "0", 3.5, 4.5, 2.0, 2.5)
        assert "3.5" in script
        assert "4.5" in script
        assert "2.0" in script
        assert "2.5" in script

    def test_no_list_mode(self):
        # The whole point of the fix: never use flagdata(mode='list') — it
        # aborts with KeyError 'nreport' in CASA 6.7.5. The list-mode call is
        # the only place that uses inpfile, so its absence proves the switch.
        script = _build_script("/data/x.ms", "0", 5.0, 5.0, 4.0, 4.0)
        assert "inpfile" not in script

    def test_uses_action_apply(self):
        # Direct (non-list) flagdata calls must pass action='apply'.
        script = _build_script("/data/x.ms", "0", 5.0, 5.0, 4.0, 4.0)
        assert 'action="apply"' in script

    def test_saves_flag_version_once(self):
        script = _build_script("/data/x.ms", "0", 5.0, 5.0, 4.0, 4.0)
        assert "flagmanager" in script
        assert "before_initial_rflag" in script

    def test_flagbackup_false_on_passes(self):
        script = _build_script("/data/x.ms", "0", 5.0, 5.0, 4.0, 4.0)
        assert "flagbackup=False" in script
        assert "flagbackup=True" not in script


# ---------------------------------------------------------------------------
# initial_rflag.run
# ---------------------------------------------------------------------------


class TestInitialRflagRun:
    @pytest.fixture(autouse=True)
    def _model_data_present(self, monkeypatch):
        """MODEL_DATA present by default; the one negative test overrides this."""
        from ms_modify import initial_rflag

        monkeypatch.setattr(
            initial_rflag,
            "open_table",
            lambda path, **kw: _fake_table(["DATA", "CORRECTED_DATA", "MODEL_DATA"]),
        )

    def _make_ms(self, tmp_path) -> Path:
        ms = tmp_path / "test.ms"
        ms.mkdir()
        (ms / "table.info").write_text("Type = Measurement Set\n")
        return ms

    def test_missing_workdir_raises(self, tmp_path):
        from ms_inspect.exceptions import ComputationError
        from ms_modify.initial_rflag import run

        ms = self._make_ms(tmp_path)
        with pytest.raises(ComputationError, match="workdir does not exist"):
            run(str(ms), str(tmp_path / "nodir"), "3C147")

    def test_empty_field_raises(self, tmp_path):
        from ms_inspect.exceptions import ComputationError
        from ms_modify.initial_rflag import run

        ms = self._make_ms(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        with pytest.raises(ComputationError, match="field is required"):
            run(str(ms), str(workdir), "", execute=False)

    def test_execute_false_writes_script(self, tmp_path):
        from ms_modify.initial_rflag import run

        ms = self._make_ms(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        result = run(str(ms), str(workdir), "3C147", execute=False)
        assert result["status"] == "ok"
        assert (workdir / "initial_rflag.py").exists()
        # The retired cmds file must no longer be written.
        assert not (workdir / "initial_rflag_cmds.txt").exists()

    def test_script_contains_residual(self, tmp_path):
        from ms_modify.initial_rflag import run

        ms = self._make_ms(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        run(str(ms), str(workdir), "3C147", execute=False)
        script = (workdir / "initial_rflag.py").read_text()
        assert "residual" in script

    def test_script_not_list_mode(self, tmp_path):
        from ms_modify.initial_rflag import run

        ms = self._make_ms(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        run(str(ms), str(workdir), "3C147", execute=False)
        script = (workdir / "initial_rflag.py").read_text()
        assert 'mode="list"' not in script

    def test_response_includes_thresholds(self, tmp_path):
        from ms_modify.initial_rflag import run

        ms = self._make_ms(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        result = run(
            str(ms), str(workdir), "3C147", timedevscale=3.5, freqdevscale=4.5, execute=False
        )
        assert result["data"]["rflag_timedevscale"] == 3.5
        assert result["data"]["rflag_freqdevscale"] == 4.5

    def test_custom_thresholds_in_script(self, tmp_path):
        from ms_modify.initial_rflag import run

        ms = self._make_ms(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        run(str(ms), str(workdir), "3C147", timedevscale=7.0, freqdevscale=8.0, execute=False)
        script = (workdir / "initial_rflag.py").read_text()
        assert "7.0" in script
        assert "8.0" in script

    def test_re_run_overwrites_files(self, tmp_path):
        """Deterministic filenames mean re-running replaces previous output."""
        from ms_modify.initial_rflag import run

        ms = self._make_ms(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        run(str(ms), str(workdir), "3C147", execute=False)
        mtime1 = (workdir / "initial_rflag.py").stat().st_mtime_ns
        run(str(ms), str(workdir), "3C147", execute=False)
        mtime2 = (workdir / "initial_rflag.py").stat().st_mtime_ns
        assert mtime2 >= mtime1

    def test_missing_model_data_refuses_to_write_script(self, tmp_path, monkeypatch):
        """T8: the G55 run's turn 5 — ms_verify_model had already reported
        MODEL_DATA absent; the tool wrote initial_rflag.py anyway and CASA
        rejected it 7s later. The guard must refuse before writing anything."""
        from ms_inspect.exceptions import ComputationError
        from ms_modify import initial_rflag

        monkeypatch.setattr(
            initial_rflag,
            "open_table",
            lambda path, **kw: _fake_table(["DATA", "CORRECTED_DATA"]),
        )
        ms = self._make_ms(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        with pytest.raises(ComputationError, match="MODEL_DATA column not present"):
            initial_rflag.run(str(ms), str(workdir), "3C147", execute=False)
        assert not (workdir / "initial_rflag.py").exists()
