"""
Unit tests for util/calibrators.py — catalogue lookup, normalisation,
resolved-source warnings.

No CASA dependency.
"""

from __future__ import annotations

import pytest

from ms_inspect.util.calibrators import (
    CATALOGUE,
    CalibratorEntry,
    UVRangeEntry,
    _normalise,
    infer_intents_from_role,
    is_known_calibrator,
    lookup,
    resolved_warning_message,
)


# ---------------------------------------------------------------------------
# Name normalisation
# ---------------------------------------------------------------------------


class TestNormalise:
    def test_lowercase(self):
        assert _normalise("3C286") == "3c286"

    def test_strip_separators(self):
        assert _normalise("PKS1934-638") == "pks1934638"
        assert _normalise("PKS 1934-638") == "pks1934638"
        assert _normalise("PKS_1934_638") == "pks1934638"

    def test_strip_whitespace(self):
        assert _normalise("  3C286  ") == "3c286"

    def test_plus_sign(self):
        assert _normalise("0137+331") == "0137331"

    def test_dot(self):
        assert _normalise("J0137.5+3309") == "j013753309"


# ---------------------------------------------------------------------------
# Catalogue lookup
# ---------------------------------------------------------------------------


class TestLookup:
    def test_canonical_name(self):
        entry = lookup("3C286")
        assert entry is not None
        assert entry.canonical_name == "3C286"

    def test_alias(self):
        entry = lookup("1331+305")
        assert entry is not None
        assert entry.canonical_name == "3C286"

    def test_case_insensitive(self):
        entry = lookup("3c286")
        assert entry is not None
        assert entry.canonical_name == "3C286"

    def test_separator_insensitive(self):
        entry = lookup("PKS 1934-638")
        assert entry is not None
        assert entry.canonical_name == "PKS1934-638"

    def test_miss(self):
        assert lookup("J9999+9999") is None

    def test_all_catalogue_entries_findable(self):
        """Every canonical name and alias should be findable."""
        for entry in CATALOGUE:
            assert lookup(entry.canonical_name) is entry
            for alias in entry.aka:
                found = lookup(alias)
                assert found is entry, f"{alias} did not resolve to {entry.canonical_name}"

    def test_3c48(self):
        entry = lookup("3C48")
        assert entry is not None
        assert entry.canonical_name == "3C48"

    def test_pks0408(self):
        entry = lookup("PKS0408-65")
        assert entry is not None
        assert entry.canonical_name == "PKS0408-65"

    def test_resolved_sources(self):
        for name in ["CasA", "CygA", "TauA", "VirA"]:
            entry = lookup(name)
            assert entry is not None
            assert entry.resolved is True


class TestIsKnownCalibrator:
    def test_known(self):
        assert is_known_calibrator("3C286") is True

    def test_unknown(self):
        assert is_known_calibrator("MY_TARGET") is False


# ---------------------------------------------------------------------------
# Intent inference
# ---------------------------------------------------------------------------


class TestInferIntentsFromRole:
    def test_flux_and_bandpass(self):
        result = infer_intents_from_role(["flux", "bandpass"])
        assert "CALIBRATE_FLUX#ON_SOURCE" in result
        assert "CALIBRATE_BANDPASS#ON_SOURCE" in result

    def test_flux_only(self):
        result = infer_intents_from_role(["flux"])
        assert result == ["CALIBRATE_FLUX#ON_SOURCE"]

    def test_unmapped_role(self):
        assert infer_intents_from_role(["phase"]) == []

    def test_empty(self):
        assert infer_intents_from_role([]) == []


# ---------------------------------------------------------------------------
# Resolved-source warnings
# ---------------------------------------------------------------------------


# Fixture entry for resolved-source tests
_RESOLVED_ENTRY = CalibratorEntry(
    canonical_name="CasA",
    aka=["cas-a"],
    role=["flux"],
    telescopes=["VLA"],
    resolved=True,
    flux_standard="Perley-Butler-2017",
    safe_uv_range_klambda={
        "L-band (1-2 GHz)": UVRangeEntry(max_klambda=0.5, reference="test ref"),
    },
    casa_model_available=True,
    casa_model_name="CasA_Epoch2010.0",
)

_UNRESOLVED_ENTRY = CalibratorEntry(
    canonical_name="3C286",
    aka=[],
    role=["flux", "bandpass"],
    telescopes=["VLA"],
    resolved=False,
    flux_standard="Perley-Butler-2017",
)


class TestResolvedWarningMessage:
    def test_unresolved_returns_none(self):
        assert resolved_warning_message(_UNRESOLVED_ENTRY, 100.0, "L-band") is None

    def test_resolved_exceeds_uv_range(self):
        msg = resolved_warning_message(_RESOLVED_ENTRY, 5.0, "L-band (1-2 GHz)")
        assert msg is not None
        assert "WARNING" in msg
        assert "5.0 kλ" in msg
        assert "≤0.5 kλ" in msg
        assert "setjy" in msg

    def test_resolved_within_safe_range(self):
        msg = resolved_warning_message(_RESOLVED_ENTRY, 0.3, "L-band (1-2 GHz)")
        assert msg is not None
        assert "ADVISORY" in msg
        assert "within the safe range" in msg

    def test_resolved_unknown_band(self):
        msg = resolved_warning_message(_RESOLVED_ENTRY, 5.0, "Q-band (40-50 GHz)")
        assert msg is not None
        assert "WARNING" in msg
        assert "not in the catalogue" in msg

    def test_resolved_no_band_name(self):
        msg = resolved_warning_message(_RESOLVED_ENTRY, 5.0, None)
        assert msg is not None
        assert "WARNING" in msg
        assert "unknown" in msg

    def test_band_matching_partial_name(self):
        # "L-band" should match "L-band (1-2 GHz)" in the catalogue
        msg = resolved_warning_message(_RESOLVED_ENTRY, 5.0, "L-band")
        assert msg is not None
        assert "WARNING" in msg
        assert "≤0.5 kλ" in msg
