"""
Unit tests for util/telescope.py — TelescopeProfile + resolution.

Pure-Python paths only (no CASA): profile_from_name, band lookups,
alias resolution, ALMA receiver-band name parsing, YAML validity.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ms_inspect.util.telescope import (
    _SPECS,
    Band,
    TelescopeSpec,
    profile_from_name,
    resolve_alma_band,
)


# ---------------------------------------------------------------------------
# Alias resolution — one rule (DEFECT-001)
# ---------------------------------------------------------------------------
class TestAliasResolution:
    @pytest.mark.parametrize(
        "raw,canonical",
        [
            ("VLA", "VLA"),
            ("EVLA", "VLA"),
            ("JVLA", "VLA"),
            ("vla", "VLA"),
            ("  EVLA  ", "VLA"),
            ("Karl G. Jansky VLA", "VLA"),  # free-form; substring fallback
            ("ALMA", "ALMA"),
            ("MeerKAT", "MeerKAT"),
            ("MEERKAT+", "MeerKAT"),
            ("GMRT", "uGMRT"),
            ("UGMRT", "uGMRT"),
        ],
    )
    def test_known_names(self, raw, canonical):
        p = profile_from_name(raw)
        assert p is not None
        assert p.canonical == canonical

    def test_raw_name_preserved(self):
        assert profile_from_name("Karl G. Jansky VLA").raw_name == "Karl G. Jansky VLA"

    @pytest.mark.parametrize("raw", ["", "unknown", "NonsenseScope", "ATCA"])
    def test_unknown_returns_none(self, raw):
        assert profile_from_name(raw) is None

    def test_unknown_warns(self, caplog):
        import logging

        with caplog.at_level(logging.WARNING):
            profile_from_name("NonsenseScope")
        assert any("Unrecognised" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Band lookup — intervals, gaps, overlaps (DEFECT-003: code vs label)
# ---------------------------------------------------------------------------
class TestBandLookup:
    def test_vla_l_band_label_and_code(self):
        p = profile_from_name("VLA")
        assert p.band_label(1.5e9) == "L-band (1–2 GHz)"
        assert p.band_code(1.5e9) == "L"

    @pytest.mark.parametrize(
        "hz,code",
        [
            (70e6, "4"),
            (350e6, "P"),
            (3.0e9, "S"),
            (6.0e9, "C"),
            (10.0e9, "X"),
            (15.0e9, "Ku"),
            (22.0e9, "K"),
            (33.0e9, "Ka"),
            (44.0e9, "Q"),
        ],
    )
    def test_vla_codes(self, hz, code):
        assert profile_from_name("VLA").band_code(hz) == code

    def test_vla_gap_returns_none(self):
        # 600 MHz is between P (≤473 MHz) and L (≥1 GHz): a real receiver gap.
        p = profile_from_name("VLA")
        assert p.band_label(0.6e9) is None
        assert p.band_code(0.6e9) is None

    def test_vla_above_top_returns_none(self):
        assert profile_from_name("VLA").band_label(60e9) is None

    def test_sefd_keyed_by_code(self):
        # DEFECT-003 regression: SEFD keyed by code, not the display label.
        p = profile_from_name("VLA")
        assert p.sefd_for_freq(1.5e9) == 420.0  # L
        assert p.sefd_for_freq(6.0e9) == 310.0  # C

    def test_sefd_unavailable_band_returns_none(self):
        # Ku has a band but no SEFD entry.
        assert profile_from_name("VLA").sefd_for_freq(15e9) is None

    def test_ugmrt_400mhz_is_band3_not_band2(self):
        # Old ladder mislabelled 400 MHz as "Band 2"; interval model says Band 3.
        assert profile_from_name("uGMRT").band_code(400e6) == "3"

    def test_meerkat_above_s_returns_none(self):
        # Old ladder returned an "Unknown MeerKAT band" string; now honest None.
        assert profile_from_name("MeerKAT").band_label(5.0e9) is None


# ---------------------------------------------------------------------------
# ALMA — overlap + receiver-band name parsing
# ---------------------------------------------------------------------------
class TestAlma:
    def test_band7_by_frequency(self):
        p = profile_from_name("ALMA")
        assert p.band_code(330.57e9) == "7"

    def test_overlap_label_lists_both(self):
        p = profile_from_name("ALMA")
        label = p.band_label(100e9)  # 100 GHz is in Band 2 (67–116) and Band 3 (84–116)
        assert "Band 2" in label and "Band 3" in label

    def test_overlap_code_is_none(self):
        assert profile_from_name("ALMA").band_code(100e9) is None

    def test_spw_name_disambiguates_overlap(self):
        p = profile_from_name("ALMA")
        name = "X123#ALMA_RB_03#BB_1#SW-01#FULL_RES"
        assert p.band_code(100e9, name) == "3"
        assert p.band_label(100e9, name) == "Band 3 (84–116 GHz)"

    @pytest.mark.parametrize(
        "name,expected",
        [
            ("X391777171#ALMA_RB_07#BB_1#SW-01#FULL_RES", "7"),
            ("ALMA_RB_10#BB", "10"),
            ("foo_RB7_bar", "7"),
            ("no band token here", None),
            (None, None),
            ("", None),
        ],
    )
    def test_resolve_alma_band(self, name, expected):
        assert resolve_alma_band(name) == expected


# ---------------------------------------------------------------------------
# YAML data validity
# ---------------------------------------------------------------------------
class TestData:
    def test_all_telescopes_loaded(self):
        assert {"VLA", "ALMA", "MeerKAT", "uGMRT"} <= set(_SPECS)

    def test_band_intervals_valid(self):
        for spec in _SPECS.values():
            for b in spec.bands:
                assert b.max_ghz > b.min_ghz, f"{spec.canonical} {b.code}"

    def test_sefd_keys_reference_real_band_codes(self):
        for spec in _SPECS.values():
            codes = {b.code for b in spec.bands}
            for key in spec.sefd_jy:
                assert key in codes, f"{spec.canonical} SEFD key {key!r} has no band"

    def test_band_rejects_bad_interval(self):
        with pytest.raises(ValueError):
            Band(code="X", min_ghz=8.0, max_ghz=8.0, label="bad")

    def test_spec_requires_fields(self):
        with pytest.raises(ValidationError):
            TelescopeSpec(canonical="X")  # missing aliases/bands
