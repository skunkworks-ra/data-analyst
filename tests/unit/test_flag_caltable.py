"""
Unit tests for ms_flag_caltable.

No CASA required. Tests cover:
- _resolve_mode: VisCal-type auto-routing, K refusal, override validation
- _build_script: mode-specific threshold kwargs, sigma embedding
- run: workdir/path/sigma validation, script generation (mocking VisCal read)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ms_inspect.exceptions import ComputationError
from ms_modify.flag_caltable import _build_script, _resolve_mode

# ---------------------------------------------------------------------------
# _resolve_mode
# ---------------------------------------------------------------------------


class TestResolveMode:
    def test_bandpass_routes_to_tfcrop(self):
        assert _resolve_mode("B", None) == "tfcrop"

    def test_gain_routes_to_rflag(self):
        assert _resolve_mode("G", None) == "rflag"

    def test_tsys_routes_to_rflag(self):
        assert _resolve_mode("T", None) == "rflag"

    def test_leakage_routes_to_rflag(self):
        assert _resolve_mode("Df", None) == "rflag"

    def test_first_token_used(self):
        # VisCal strings can carry extra tokens after the type
        assert _resolve_mode("B TSYS", None) == "tfcrop"

    def test_delay_refused(self):
        with pytest.raises(ComputationError, match="delay/position"):
            _resolve_mode("K", None)

    def test_kcross_refused(self):
        with pytest.raises(ComputationError, match="delay/position"):
            _resolve_mode("Kcross", None)

    def test_unknown_type_refused_without_override(self):
        with pytest.raises(ComputationError, match="auto-route"):
            _resolve_mode("ZZ", None)

    def test_override_wins_over_routing(self):
        # Even a K table can be forced if the caller insists on a valid mode
        assert _resolve_mode("K", "rflag") == "rflag"

    def test_invalid_override_rejected(self):
        with pytest.raises(ComputationError, match="rflag.*tfcrop"):
            _resolve_mode("G", "bogus")


# ---------------------------------------------------------------------------
# _build_script
# ---------------------------------------------------------------------------


class TestBuildScript:
    def test_tfcrop_uses_cutoff_kwargs(self):
        s = _build_script("cal.B", "tfcrop", "CPARAM", 5.0, True)
        assert "timecutoff=5.0" in s
        assert "freqcutoff=5.0" in s
        assert "timedevscale" not in s

    def test_rflag_uses_devscale_kwargs(self):
        s = _build_script("cal.G", "rflag", "CPARAM", 6.0, True)
        assert "timedevscale=6.0" in s
        assert "freqdevscale=6.0" in s
        assert "timecutoff" not in s

    def test_datacolumn_embedded(self):
        s = _build_script("cal.G", "rflag", "CPARAM", 5.0, False)
        assert "CPARAM" in s

    def test_summary_before_and_after(self):
        s = _build_script("cal.G", "rflag", "CPARAM", 5.0, True)
        assert s.count('mode="summary"') == 2

    def test_flagbackup_value_embedded(self):
        s = _build_script("cal.G", "rflag", "CPARAM", 5.0, False)
        assert "flagbackup=False" in s


# ---------------------------------------------------------------------------
# run — validation paths (no VisCal read reached)
# ---------------------------------------------------------------------------


class TestRunValidation:
    def _make_caltable(self, tmp_path) -> Path:
        ct = tmp_path / "test.G"
        ct.mkdir()
        (ct / "table.info").write_text("Type = Calibration\n")
        return ct

    def test_missing_caltable_raises(self, tmp_path):
        from ms_modify.flag_caltable import run

        with pytest.raises(ComputationError, match="not found"):
            run(str(tmp_path / "nope.G"), str(tmp_path), execute=False)

    def test_not_a_table_raises(self, tmp_path):
        from ms_modify.flag_caltable import run

        bare = tmp_path / "bare.G"
        bare.mkdir()  # no table.info
        with pytest.raises(ComputationError, match="not a CASA table"):
            run(str(bare), str(tmp_path), execute=False)

    def test_missing_workdir_raises(self, tmp_path):
        from ms_modify.flag_caltable import run

        ct = self._make_caltable(tmp_path)
        with pytest.raises(ComputationError, match="workdir does not exist"):
            run(str(ct), str(tmp_path / "nodir"), execute=False)

    def test_nonpositive_sigma_raises(self, tmp_path):
        from ms_modify.flag_caltable import run

        ct = self._make_caltable(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        with pytest.raises(ComputationError, match="sigma must be"):
            run(str(ct), str(workdir), sigma=0.0, execute=False)


# ---------------------------------------------------------------------------
# run — script generation with VisCal read mocked
# ---------------------------------------------------------------------------


class TestRunScriptGen:
    def _make_caltable(self, tmp_path, suffix=".G") -> Path:
        ct = tmp_path / f"test{suffix}"
        ct.mkdir()
        (ct / "table.info").write_text("Type = Calibration\n")
        return ct

    def test_generates_script_and_routes_mode(self, tmp_path, monkeypatch):
        import ms_modify.flag_caltable as mod

        monkeypatch.setattr(mod, "_read_viscal_type", lambda _p: "B")
        ct = self._make_caltable(tmp_path, ".B")
        workdir = tmp_path / "work"
        workdir.mkdir()
        result = mod.run(str(ct), str(workdir), execute=False)
        assert result["status"] == "ok"
        assert result["data"]["mode"]["value"] == "tfcrop"
        assert result["data"]["viscal_type"]["value"] == "B"
        assert (workdir / "flag_caltable.py").exists()

    def test_delay_table_refused_in_run(self, tmp_path, monkeypatch):
        import ms_modify.flag_caltable as mod

        monkeypatch.setattr(mod, "_read_viscal_type", lambda _p: "K")
        ct = self._make_caltable(tmp_path, ".K")
        workdir = tmp_path / "work"
        workdir.mkdir()
        with pytest.raises(ComputationError, match="delay/position"):
            mod.run(str(ct), str(workdir), execute=False)
