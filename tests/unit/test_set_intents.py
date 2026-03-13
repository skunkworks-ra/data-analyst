"""
Unit tests for ms_modify/intents.py — _compute_intent_map logic.

Tests the pure intent-mapping function without CASA.
"""

from __future__ import annotations

from unittest.mock import patch

from ms_modify.intents import _compute_intent_map


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
        assert result[0]["source"] == "primary_catalogue"
        assert "CALIBRATE_FLUX#ON_SOURCE" in result[0]["intents"]
        assert "CALIBRATE_BANDPASS#ON_SOURCE" in result[0]["intents"]

    def test_primary_catalogue_flux_only(self):
        """3C147 is flux-only — no bandpass intent."""
        fields = [_make_field(0, "3C147")]
        result = _compute_intent_map(fields)

        assert result[0]["source"] == "primary_catalogue"
        assert result[0]["intents"] == ["CALIBRATE_FLUX#ON_SOURCE"]

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
        assert result[0]["source"] == "primary_catalogue"
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
