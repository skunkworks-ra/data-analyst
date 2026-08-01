"""
Unit tests for ms_modify/intents.py — _compute_intent_map logic.

Tests the pure intent-mapping function without CASA.
"""

from __future__ import annotations

from unittest.mock import patch

from ms_modify.intents import (
    _compute_intent_map,
    _pol_intents_for_field,
    _pol_sources_available,
)


def _make_field(fid: int, name: str, ra: float | None = 180.0, dec: float | None = 45.0):
    return {
        "field_id": fid,
        "name": name,
        "ra_deg": ra,
        "dec_deg": dec,
        "existing_intents": set(),
    }


class TestComputeIntentMap:
    """Tests for _compute_intent_map (no CASA)."""

    def test_primary_catalogue_flux_bandpass(self):
        """3C286 should match as flux + bandpass from primary catalogue."""
        fields = [_make_field(0, "3C286")]
        result = _compute_intent_map(fields)

        assert len(result) == 1
        # 3C286 is also the Category A pol angle standard, so the pol catalogue
        # contributes on top of the primary match rather than replacing it.
        assert result[0]["source"] == "primary_catalogue+pol_catalogue"
        assert "CALIBRATE_FLUX#ON_SOURCE" in result[0]["intents"]
        assert "CALIBRATE_BANDPASS#ON_SOURCE" in result[0]["intents"]

    def test_primary_catalogue_flux_only(self):
        """3C147 is flux-only — no bandpass intent."""
        fields = [_make_field(0, "3C147")]
        result = _compute_intent_map(fields)

        # 3C147 is flux-only in the primary catalogue and a dedicated leakage
        # calibrator in the pol catalogue.
        assert result[0]["source"] == "primary_catalogue+pol_catalogue"
        assert result[0]["intents"] == [
            "CALIBRATE_FLUX#ON_SOURCE",
            "CALIBRATE_POL_LEAKAGE#ON_SOURCE",
        ]

    def test_primary_catalogue_alias(self):
        """PKS1934-638 should match via alias normalisation."""
        fields = [_make_field(0, "1934-638")]
        result = _compute_intent_map(fields)

        assert result[0]["source"] == "primary_catalogue"
        assert "CALIBRATE_FLUX#ON_SOURCE" in result[0]["intents"]

    @patch("ms_modify.intents.vla_cone_search")
    def test_vla_cone_search_match(self, mock_cone):
        """VLA cone search match → CALIBRATE_PHASE."""
        from unittest.mock import MagicMock

        mock_result = MagicMock()
        mock_result.name = "J1407+2827"
        mock_result.alt_name = "OQ208"
        mock_cone.return_value = mock_result

        fields = [_make_field(0, "UNKNOWN_SOURCE", ra=211.75, dec=28.45)]
        result = _compute_intent_map(fields)

        assert result[0]["source"] == "vla_cone_search"
        assert result[0]["intents"] == ["CALIBRATE_PHASE#ON_SOURCE"]
        mock_cone.assert_called_once_with(211.75, 28.45, radius_arcsec=5.0)

    @patch("ms_modify.intents.vla_cone_search")
    def test_vla_cone_search_no_match(self, mock_cone):
        """VLA cone search returns None → default target."""
        mock_cone.return_value = None

        fields = [_make_field(0, "MY_TARGET", ra=100.0, dec=-30.0)]
        result = _compute_intent_map(fields)

        assert result[0]["source"] == "default_target"
        assert result[0]["intents"] == ["OBSERVE_TARGET#ON_SOURCE"]

    @patch("ms_modify.intents.vla_cone_search")
    def test_vla_cone_search_exception(self, mock_cone):
        """VLA cone search failure → graceful fallback to target."""
        mock_cone.side_effect = ConnectionError("network down")

        fields = [_make_field(0, "MY_TARGET", ra=100.0, dec=-30.0)]
        result = _compute_intent_map(fields)

        assert result[0]["source"] == "default_target"
        assert result[0]["intents"] == ["OBSERVE_TARGET#ON_SOURCE"]

    def test_no_coordinates(self):
        """Field with no coordinates → default target (skips cone search)."""
        fields = [_make_field(0, "UNKNOWN", ra=None, dec=None)]
        result = _compute_intent_map(fields)

        assert result[0]["source"] == "default_target"
        assert result[0]["intents"] == ["OBSERVE_TARGET#ON_SOURCE"]

    def test_multiple_fields_mixed(self):
        """Multiple fields: calibrator + unknown → correct assignments."""
        fields = [
            _make_field(0, "3C286"),
            _make_field(1, "MY_TARGET", ra=100.0, dec=-30.0),
        ]

        with patch("ms_modify.intents.vla_cone_search", return_value=None):
            result = _compute_intent_map(fields)

        assert len(result) == 2
        assert result[0]["source"] == "primary_catalogue+pol_catalogue"
        assert result[1]["source"] == "default_target"


class TestInferIntentsFromRole:
    """Tests for the promoted infer_intents_from_role function."""

    def test_flux_and_bandpass(self):
        from ms_inspect.util.calibrators import infer_intents_from_role

        result = infer_intents_from_role(["flux", "bandpass"])
        assert "CALIBRATE_FLUX#ON_SOURCE" in result
        assert "CALIBRATE_BANDPASS#ON_SOURCE" in result

    def test_flux_only(self):
        from ms_inspect.util.calibrators import infer_intents_from_role

        result = infer_intents_from_role(["flux"])
        assert result == ["CALIBRATE_FLUX#ON_SOURCE"]

    def test_unknown_role(self):
        from ms_inspect.util.calibrators import infer_intents_from_role

        result = infer_intents_from_role(["phase"])
        assert result == []

    def test_empty_roles(self):
        from ms_inspect.util.calibrators import infer_intents_from_role

        result = infer_intents_from_role([])
        assert result == []


class TestPolIntentsFromCatalogueIdentity:
    """Pol intents come from who the source IS, never from coverage or strategy."""

    def test_category_a_standard_gets_angle_only(self):
        """3C286 lists both roles; labelling it the leakage cal would mask a real one."""
        intents, cat_name = _pol_intents_for_field("3C286")

        assert intents == ["CALIBRATE_POL_ANGLE#ON_SOURCE"]
        assert "CALIBRATE_POL_LEAKAGE#ON_SOURCE" not in intents
        assert cat_name == "3C286"

    def test_dedicated_leakage_cal_gets_leakage(self):
        intents, cat_name = _pol_intents_for_field("J0319+4130")  # 3C84

        assert intents == ["CALIBRATE_POL_LEAKAGE#ON_SOURCE"]
        assert cat_name == "3C84"

    def test_uncatalogued_field_gets_nothing(self):
        """The phase cal is not a pol calibrator by default. Nominating it is the caller's job."""
        assert _pol_intents_for_field("J1822-0938") == ([], None)

    def test_target_gets_nothing(self):
        assert _pol_intents_for_field("3C391 C1") == ([], None)


class TestCallerNomination:
    """The tool never nominates a leakage cal; the caller does, and it is recorded."""

    def test_nomination_by_name_adds_the_intent(self):
        fields = [_make_field(0, "MY_PHASE_CAL", ra=100.0, dec=-30.0)]

        with patch("ms_modify.intents.vla_cone_search", return_value=None):
            result = _compute_intent_map(fields, pol_leakage_fields=("MY_PHASE_CAL",))

        assert "CALIBRATE_POL_LEAKAGE#ON_SOURCE" in result[0]["intents"]
        assert "caller_nominated_leakage" in result[0]["source"]

    def test_nomination_by_field_id_works_too(self):
        fields = [_make_field(7, "MY_PHASE_CAL", ra=100.0, dec=-30.0)]

        with patch("ms_modify.intents.vla_cone_search", return_value=None):
            result = _compute_intent_map(fields, pol_leakage_fields=("7",))

        assert "CALIBRATE_POL_LEAKAGE#ON_SOURCE" in result[0]["intents"]

    def test_nomination_does_not_disturb_other_fields(self):
        fields = [
            _make_field(0, "3C286"),
            _make_field(1, "MY_PHASE_CAL", ra=100.0, dec=-30.0),
        ]

        with patch("ms_modify.intents.vla_cone_search", return_value=None):
            result = _compute_intent_map(fields, pol_leakage_fields=("MY_PHASE_CAL",))

        assert "CALIBRATE_POL_LEAKAGE#ON_SOURCE" not in result[0]["intents"]
        assert "CALIBRATE_POL_LEAKAGE#ON_SOURCE" in result[1]["intents"]

    def test_nominating_a_catalogued_angle_cal_as_leakage_is_honoured(self):
        """An explicit choice overrides the identity default, and says so."""
        fields = [_make_field(0, "3C286")]

        result = _compute_intent_map(fields, pol_leakage_fields=("3C286",))

        assert "CALIBRATE_POL_ANGLE#ON_SOURCE" in result[0]["intents"]
        assert "CALIBRATE_POL_LEAKAGE#ON_SOURCE" in result[0]["intents"]
        assert "caller_nominated_leakage" in result[0]["source"]

    def test_no_nomination_leaves_an_uncatalogued_field_pol_free(self):
        """The regression this guards: no auto-promotion of the phase cal."""
        fields = [_make_field(0, "MY_PHASE_CAL", ra=100.0, dec=-30.0)]

        with patch("ms_modify.intents.vla_cone_search", return_value=None):
            result = _compute_intent_map(fields)

        assert not any("POL" in i for i in result[0]["intents"])


class TestPolSourcesAvailable:
    """The tool reports what is available so the skill can nominate."""

    def test_reports_catalogued_sources_and_what_was_assigned(self):
        fields = [_make_field(0, "3C286"), _make_field(9, "J0319+4130")]
        intent_map = _compute_intent_map(fields)

        summary = _pol_sources_available(fields, intent_map, band_ghz=4.6)

        assert summary["angle_intent_assigned"] is True
        assert summary["leakage_intent_assigned"] is True
        names = {c["catalogue_source"] for c in summary["catalogued_pol_sources"]}
        assert names == {"3C286", "3C84"}

    def test_reports_the_absence_of_a_leakage_cal_as_a_fact(self):
        fields = [_make_field(0, "3C286"), _make_field(1, "MY_TARGET", ra=100.0, dec=-30.0)]

        with patch("ms_modify.intents.vla_cone_search", return_value=None):
            intent_map = _compute_intent_map(fields)
        summary = _pol_sources_available(fields, intent_map, band_ghz=4.6)

        assert summary["angle_intent_assigned"] is True
        assert summary["leakage_intent_assigned"] is False
        assert "pol_leakage_fields" in summary["note"]
        assert [f["name"] for f in summary["uncatalogued_fields"]] == ["MY_TARGET"]

    def test_effective_role_is_resolved_at_the_observing_band(self):
        """3C84 is a zero-pol leakage cal low down and polarized higher up."""
        fields = [_make_field(0, "J0319+4130")]
        intent_map = _compute_intent_map(fields)

        low = _pol_sources_available(fields, intent_map, band_ghz=1.5)
        high = _pol_sources_available(fields, intent_map, band_ghz=22.0)

        assert low["catalogued_pol_sources"][0]["effective_role_at_band"] == "leakage_zero_pol"
        assert high["catalogued_pol_sources"][0]["effective_role_at_band"] != "leakage_zero_pol"

    def test_band_unavailable_is_stated_not_guessed(self):
        fields = [_make_field(0, "3C286")]
        intent_map = _compute_intent_map(fields)

        summary = _pol_sources_available(fields, intent_map, band_ghz=None)

        assert "unavailable" in summary["catalogued_pol_sources"][0]["effective_role_at_band"]
