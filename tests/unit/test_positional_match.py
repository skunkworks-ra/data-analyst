"""
Unit tests for the VLA positional cross-match guard.

A solar-system body's phase centre is a position at one epoch, not an
identity. Cross-matching on it is meaningless at best and, near the ecliptic,
actively wrong — it can return a confident match on an unrelated calibrator.

Two call sites run the cone search:
  - ms_inspect/tools/fields.py  — reports the match
  - ms_modify/intents.py        — WRITES intents to the STATE subtable

The second is the dangerous one, so it gets its own invariant test.

No CASA dependency.
"""

from __future__ import annotations

from unittest.mock import patch

from ms_inspect.tools.fields import _vla_positional_match
from ms_inspect.util.calibrators import lookup as cal_lookup
from ms_modify.intents import _compute_intent_map


def _make_field(fid: int, name: str, ra: float | None = 180.0, dec: float | None = 45.0):
    return {
        "field_id": fid,
        "name": name,
        "ra_deg": ra,
        "dec_deg": dec,
        "existing_intents": set(),
    }


class TestSolarSystemSkipsCrossMatch:
    def test_solar_system_entry_is_not_searched(self):
        entry = cal_lookup("Ceres")
        assert entry is not None and entry.solar_system

        with patch("ms_inspect.tools.fields.vla_cone_search") as mock_search:
            result = _vla_positional_match(180.0, 45.0, entry)

        mock_search.assert_not_called()
        assert result["value"] is None
        assert result["flag"] == "UNAVAILABLE"

    def test_skip_states_its_reason(self):
        # An UNAVAILABLE with no explanation reads as "the search failed".
        # This one means "the search does not apply", which is different.
        entry = cal_lookup("Titan")
        result = _vla_positional_match(180.0, 45.0, entry)
        assert "solar-system body" in result["note"]
        assert "Titan" in result["note"]

    def test_every_solar_system_body_is_skipped(self):
        from ms_inspect.util.calibrators import CATALOGUE

        bodies = [e for e in CATALOGUE if e.solar_system]
        assert len(bodies) == 15
        for entry in bodies:
            with patch("ms_inspect.tools.fields.vla_cone_search") as mock_search:
                result = _vla_positional_match(180.0, 45.0, entry)
            mock_search.assert_not_called()
            assert result["flag"] == "UNAVAILABLE", entry.canonical_name

    def test_fixed_source_is_still_searched(self):
        # The guard must not suppress the cross-match for everything else.
        entry = cal_lookup("3C286")
        assert entry is not None and not entry.solar_system

        with patch("ms_inspect.tools.fields.vla_cone_search", return_value=None) as mock_search:
            result = _vla_positional_match(180.0, 45.0, entry)

        mock_search.assert_called_once()
        assert result["flag"] == "UNAVAILABLE"
        assert "No VLA calibrator" in result["note"]

    def test_uncatalogued_field_is_still_searched(self):
        # cal_entry is None for a science target; the search must still run.
        with patch("ms_inspect.tools.fields.vla_cone_search", return_value=None) as mock_search:
            _vla_positional_match(180.0, 45.0, None)
        mock_search.assert_called_once()

    def test_missing_coordinates_still_short_circuit(self):
        with patch("ms_inspect.tools.fields.vla_cone_search") as mock_search:
            result = _vla_positional_match(None, None, None)
        mock_search.assert_not_called()
        assert "No coordinates" in result["note"]


class TestIntentWritePathNeverCrossMatchesSolarSystem:
    """
    ms_set_intents WRITES to the MS. A cone-search match there would put
    CALIBRATE_PHASE on a flux calibrator permanently.

    The catalogue lookup runs first and the cone search sits in its else
    branch, so a body in the catalogue never reaches the search. These tests
    pin that ordering — it is load-bearing, not incidental.
    """

    def test_ceres_gets_flux_intent_not_phase(self):
        with patch("ms_modify.intents.vla_cone_search") as mock_search:
            out = _compute_intent_map([_make_field(0, "Ceres")])

        mock_search.assert_not_called()
        assert out[0]["intents"] == ["CALIBRATE_FLUX#ON_SOURCE"]
        assert "vla_cone_search" not in out[0]["source"]
        assert "primary_catalogue" in out[0]["source"]

    def test_no_solar_system_body_reaches_the_cone_search(self):
        from ms_inspect.util.calibrators import CATALOGUE

        bodies = [e.canonical_name for e in CATALOGUE if e.solar_system]
        fields = [_make_field(i, n) for i, n in enumerate(bodies)]

        with patch("ms_modify.intents.vla_cone_search") as mock_search:
            out = _compute_intent_map(fields)

        mock_search.assert_not_called()
        for rec in out:
            assert "CALIBRATE_PHASE#ON_SOURCE" not in rec["intents"], rec["name"]

    def test_unknown_field_does_reach_the_cone_search(self):
        # Guards against the ordering test above passing vacuously — it must
        # be possible to reach the search at all.
        with patch("ms_modify.intents.vla_cone_search", return_value=None) as mock_search:
            _compute_intent_map([_make_field(0, "NotACalibrator")])
        mock_search.assert_called_once()
