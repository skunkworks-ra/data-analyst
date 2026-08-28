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

from ms_modify.setjy import _build_setjy_block

# The default is now '' — resolve per field — so these fragment tests name
# the standard explicitly rather than importing a constant that no longer
# describes what the tool does.
_PB = "Perley-Butler 2017"

# ---------------------------------------------------------------------------
# _build_setjy_block
# ---------------------------------------------------------------------------


class TestBuildSetjyBlock:
    def test_contains_field_name(self):
        block = _build_setjy_block("3C286", _PB, False)
        assert "3C286" in block

    def test_contains_standard(self):
        block = _build_setjy_block("3C147", _PB, False)
        assert _PB in block

    def test_contains_setjy_call(self):
        block = _build_setjy_block("3C48", _PB, False)
        assert "setjy(" in block

    def test_usescratch_false_in_block(self):
        block = _build_setjy_block("3C286", _PB, False)
        assert "usescratch=False" in block

    def test_usescratch_true_in_block(self):
        block = _build_setjy_block("3C286", _PB, True)
        assert "usescratch=True" in block


# ---------------------------------------------------------------------------
# _get_field_names — numpy str_ coercion
# ---------------------------------------------------------------------------


class TestGetFieldNamesCoercion:
    def test_numpy_str_coerced_to_plain_str(self):
        """tb.getcol returns numpy str_ values; repr(np.str_) renders as
        np.str_('...') under numpy >= 2, breaking generated scripts."""
        import numpy as np

        from ms_modify.setjy import _get_field_names

        class FakeTable:
            def getcol(self, col):
                return np.array(["3C286", "J1925+2106"])

        from contextlib import contextmanager

        @contextmanager
        def fake_open_table(path):
            yield FakeTable()

        with patch("ms_modify.setjy.open_table", fake_open_table):
            names = _get_field_names("/fake.ms")
        assert all(type(n) is str for n in names)
        assert all("np." not in repr(n) for n in names)


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
        with (
            patch("ms_modify.setjy._get_field_names", return_value=["3C286"]),
            pytest.raises(ComputationError, match="workdir does not exist"),
        ):
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

    def test_exclude_fields_omits_overlap_field(self, tmp_path):
        from ms_modify.setjy import run

        ms = self._make_ms(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        # 3C286 is a catalogued flux cal; excluding it (pol cal overlap) must
        # keep it out of flux_fields and record it under excluded_fields.
        with patch("ms_modify.setjy._get_field_names", return_value=["3C286", "3C147"]):
            result = run(str(ms), str(workdir), exclude_fields="3C286", execute=False)
        flux_fields = result["data"]["flux_fields"]["value"]
        excluded = result["data"]["excluded_fields"]["value"]
        assert "3C286" not in flux_fields
        assert "3C147" in flux_fields
        assert "3C286" in excluded

    def test_exclude_field_not_written_to_script(self, tmp_path):
        from ms_modify.setjy import run

        ms = self._make_ms(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        with patch("ms_modify.setjy._get_field_names", return_value=["3C286", "3C147"]):
            run(str(ms), str(workdir), exclude_fields="3C286", execute=False)
        script = (workdir / "setjy.py").read_text()
        assert "3C147" in script
        assert "field='3C286'" not in script

    def test_unmatched_exclude_name_warns(self, tmp_path):
        from ms_modify.setjy import run

        ms = self._make_ms(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        with patch("ms_modify.setjy._get_field_names", return_value=["3C286"]):
            result = run(str(ms), str(workdir), exclude_fields="typo_name", execute=False)
        assert any("not found" in w for w in result["warnings"])

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
        assert any("usable flux standard" in w for w in result["warnings"])

    def test_script_contains_perley_butler(self, tmp_path):
        from ms_modify.setjy import run

        ms = self._make_ms(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        with patch("ms_modify.setjy._get_field_names", return_value=["3C286"]):
            run(str(ms), str(workdir), execute=False)
        script = (workdir / "setjy.py").read_text()
        assert "Perley-Butler 2017" in script

    def test_usescratch_defaults_true_in_script(self, tmp_path):
        from ms_modify.setjy import run

        ms = self._make_ms(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        with patch("ms_modify.setjy._get_field_names", return_value=["3C286"]):
            result = run(str(ms), str(workdir), execute=False)
        script = (workdir / "setjy.py").read_text()
        assert "usescratch=True" in script
        assert "usescratch=False" not in script
        assert result["data"]["usescratch"] is True

    def test_usescratch_true_threads_into_script_and_response(self, tmp_path):
        from ms_modify.setjy import run

        ms = self._make_ms(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        with patch("ms_modify.setjy._get_field_names", return_value=["3C286"]):
            result = run(str(ms), str(workdir), usescratch=True, execute=False)
        script = (workdir / "setjy.py").read_text()
        assert "usescratch=True" in script
        assert "usescratch=False" not in script
        assert result["data"]["usescratch"] is True


# ---------------------------------------------------------------------------
# Per-field flux standard resolution (design_docs/FLUX_STANDARD_DESIGN.md §2.2 / §2.4)
# ---------------------------------------------------------------------------


def _freqs(*spans):
    """Build the _get_field_frequencies return value for the given GHz spans."""
    out = []
    for span in spans:
        if span is None:
            out.append(
                {
                    "min_ghz": None,
                    "max_ghz": None,
                    "centre_ghz": None,
                    "n_spw": 0,
                    "excluded_spw": 0,
                }
            )
        else:
            lo, hi = span
            out.append(
                {
                    "min_ghz": lo,
                    "max_ghz": hi,
                    "centre_ghz": 0.5 * (lo + hi),
                    "n_spw": 1,
                    "excluded_spw": 0,
                }
            )
    return out


class TestPerFieldStandard:
    """
    The tool must reach the SAME answer as ms_field_list, and it must be able
    to express an MS that needs two standards at once.

    The existing TestSetjyRun cases run with no msmd at all, so the frequency
    gate never fires there. These patch it deliberately — a gate that is never
    exercised is not tested.
    """

    def _run(self, tmp_path, names, spans, **kwargs):
        from ms_modify.setjy import run

        ms = tmp_path / "test.ms"
        ms.mkdir(exist_ok=True)
        (ms / "table.info").write_text("Type = Measurement Set\n")
        workdir = tmp_path / "work"
        workdir.mkdir(exist_ok=True)
        with (
            patch("ms_modify.setjy._get_field_names", return_value=names),
            patch("ms_modify.setjy._get_field_frequencies", return_value=_freqs(*spans)),
        ):
            result = run(str(ms), str(workdir), execute=False, **kwargs)
        return result, (workdir / "setjy.py").read_text()

    def test_two_standards_in_one_ms(self, tmp_path):
        # The ALMA case, and the reason the run-level argument had to go.
        result, script = self._run(tmp_path, ["Ceres", "3C286"], [(224.0, 237.0), (1.0, 2.0)])
        assert "Butler-JPL-Horizons 2012" in script
        assert "Perley-Butler 2017" in script
        by_field = {r["field"]: r for r in result["data"]["flux_standard_resolution"]["value"]}
        assert by_field["Ceres"]["standard"] == "Butler-JPL-Horizons 2012"
        assert by_field["3C286"]["standard"] == "Perley-Butler 2017"

    def test_out_of_range_field_is_skipped_not_mis_scaled(self, tmp_path):
        # 3C286 at Band 6. The old tool wrote Perley-Butler here silently.
        result, script = self._run(tmp_path, ["3C286"], [(224.0, 237.0)])
        assert "field='3C286'" not in script
        assert result["data"]["n_flux_fields"] == 0
        reasons = result["data"]["skipped_no_standard"]["value"]
        assert [r["field"] for r in reasons] == ["3C286"]
        assert "0.05-50 GHz" in reasons[0]["reason"]

    def test_skipped_no_standard_is_not_merged_into_skipped_fields(self, tmp_path):
        # "not a flux calibrator" and "a flux cal we could not scale" are
        # different problems. Merging them hides the second.
        result, _ = self._run(tmp_path, ["3C286", "phase_cal"], [(224.0, 237.0), (224.0, 237.0)])
        assert result["data"]["skipped_fields"]["value"] == ["phase_cal"]
        assert [r["field"] for r in result["data"]["skipped_no_standard"]["value"]] == ["3C286"]

    def test_in_range_field_reports_the_gate_ran(self, tmp_path):
        result, _ = self._run(tmp_path, ["3C286"], [(1.0, 2.0)])
        assert result["data"]["n_range_checked"] == 1
        row = result["data"]["flux_standard_resolution"]["value"][0]
        assert row["flag"] == "COMPLETE"
        assert row["range_checked"] is True

    def test_constant_temperature_body_reports_no_range_check(self, tmp_path):
        # Ceres is scaled, but nothing was verified about the frequency. The
        # response must not let that read as a passed check.
        result, _ = self._run(tmp_path, ["Ceres"], [(224.0, 237.0)])
        assert result["data"]["n_flux_fields"] == 1
        assert result["data"]["n_range_checked"] == 0
        assert result["data"]["flux_standard_resolution"]["value"][0]["range_checked"] is False

    def test_unreadable_frequency_still_writes_a_script_and_warns(self, tmp_path):
        from ms_modify.setjy import run

        ms = tmp_path / "test.ms"
        ms.mkdir()
        (ms / "table.info").write_text("Type = Measurement Set\n")
        workdir = tmp_path / "work"
        workdir.mkdir()
        with (
            patch("ms_modify.setjy._get_field_names", return_value=["3C286"]),
            patch("ms_modify.setjy._get_field_frequencies", return_value=None),
        ):
            result = run(str(ms), str(workdir), execute=False)
        assert result["data"]["n_flux_fields"] == 1
        assert result["data"]["n_range_checked"] == 0
        assert any("WITHOUT checking" in w for w in result["warnings"])

    def test_the_note_travels_into_the_generated_script(self, tmp_path):
        # setjy.py is read months later, without the tool response.
        _, script = self._run(tmp_path, ["3C286"], [(1.0, 2.0)])
        assert "# 3C286:" in script
        assert "0.05-50 GHz" in script


class TestManualFluxPath:
    def _run(self, tmp_path, names, spans, **kwargs):
        return TestPerFieldStandard._run(self, tmp_path, names, spans, **kwargs)

    def test_source_with_no_casa_standard_is_skipped_without_a_manual_flux(self, tmp_path):
        # PKS0408-65 must NEVER fall back to some other standard.
        result, script = self._run(tmp_path, ["PKS0408-65"], [(1.0, 2.0)])
        assert "field='PKS0408-65'" not in script
        assert "Perley-Butler" not in script
        assert any("manual_flux" in w for w in result["warnings"])

    def test_manual_flux_emits_a_manual_setjy_call(self, tmp_path):
        result, script = self._run(
            tmp_path,
            ["PKS0408-65"],
            [(1.0, 2.0)],
            manual_flux={
                "PKS0408-65": {"fluxdensity": [17.1, 0, 0, 0], "spix": -1.18, "reffreq": "1.4GHz"}
            },
        )
        assert "standard='manual'" in script
        assert "fluxdensity=[17.1, 0, 0, 0]" in script
        assert "spix=-1.18" in script
        assert result["data"]["n_flux_fields"] == 1

    def test_only_supplied_keys_are_emitted(self, tmp_path):
        # A defaulted spix would be indistinguishable from a measured one.
        _, script = self._run(
            tmp_path,
            ["PKS0408-65"],
            [(1.0, 2.0)],
            manual_flux={"PKS0408-65": {"fluxdensity": [17.1, 0, 0, 0]}},
        )
        assert "fluxdensity=" in script
        assert "spix=" not in script
        assert "reffreq=" not in script

    def test_unused_manual_flux_entry_warns(self, tmp_path):
        result, _ = self._run(
            tmp_path, ["3C286"], [(1.0, 2.0)], manual_flux={"typo_name": {"fluxdensity": [1.0]}}
        )
        assert any("manual_flux entries not used" in w for w in result["warnings"])


class TestWholeRunOverride:
    def _run(self, tmp_path, names, spans, **kwargs):
        return TestPerFieldStandard._run(self, tmp_path, names, spans, **kwargs)

    def test_override_forces_the_standard_and_skips_the_gate(self, tmp_path):
        # An operator who names a standard is overruling the catalogue. Honour
        # it, but do not claim a frequency check happened.
        result, script = self._run(
            tmp_path, ["3C286"], [(224.0, 237.0)], standard="Perley-Butler 2017"
        )
        assert "Perley-Butler 2017" in script
        assert result["data"]["standard_mode"] == "override"
        assert result["data"]["n_range_checked"] == 0
        assert result["data"]["flux_standard_resolution"]["value"][0]["flag"] == "INFERRED"

    def test_default_is_per_field_not_override(self, tmp_path):
        result, _ = self._run(tmp_path, ["3C286"], [(1.0, 2.0)])
        assert result["data"]["standard_mode"] == "per_field"
