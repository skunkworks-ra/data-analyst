"""
Unit tests for util/vla_calibrators.py — parsing, cone search, caching.

No network access required: all tests use mock/inline data.
"""

from __future__ import annotations

import json

import pytest

from ms_inspect.util.vla_calibrators import (
    BandInfo,
    VLACalibratorEntry,
    _entry_from_dict,
    _entry_to_dict,
    _parse_entry,
    _parse_ra_dec,
    _parse_text,
    cone_search,
    identify_fields,
)

# ---------------------------------------------------------------------------
# Fixtures — sample calibrator text blocks
# ---------------------------------------------------------------------------

SAMPLE_ENTRY_3C48 = """\
0137+331   J2000  B 01h37m41.299431s 33d09'35.132990'' Aug01 3C48
0134+329   B1950  B 01h34m49.826400s 32d54'20.259000''
-----------------------------------------------------
BAND        A B C D    FLUX(Jy)    UVMIN(kL)  UVMAX(kL)
 90cm    P  S S S S      42.00
 20cm    L  X P P P      16.50                   40
  6cm    C  X S P P       5.48                  40
 3.7cm   X  X S P P       3.25
  2cm    U  X S P P       1.65
 1.3cm   K  X S S P       0.90
 0.7cm   Q  X S S P       0.52
"""

SAMPLE_ENTRY_SIMPLE = """\
1331+305   J2000  A 13h31m08.288061s 30d30'32.958850'' Aug01 3C286
1328+307   B1950  A 13h28m49.660000s 30d45'58.640000''
-----------------------------------------------------
BAND        A B C D    FLUX(Jy)    UVMIN(kL)  UVMAX(kL)
 20cm    L  P P P P      14.90
  6cm    C  P P P P       7.41
"""

SAMPLE_ENTRY_NO_FLUX = """\
0006-063   J2000  A 00h06m13.892894s -06d23'35.335300'' Aug01
0003-066   B1950  A 00h03m40.288800s -06d40'17.300000''
-----------------------------------------------------
BAND        A B C D    FLUX(Jy)    UVMIN(kL)  UVMAX(kL)
 20cm    L  S X X X       1.60           45
  6cm    C  P P P P       1.30
"""


# ---------------------------------------------------------------------------
# RA/Dec parsing
# ---------------------------------------------------------------------------


class TestParseRaDec:
    def test_3c48_coords(self):
        ra, dec = _parse_ra_dec("01h37m41.299431s", "33d09'35.132990''")
        # 3C48: RA ~ 24.42 deg, Dec ~ 33.16 deg
        assert abs(ra - 24.422) < 0.01
        assert abs(dec - 33.159) < 0.01

    def test_negative_dec(self):
        ra, dec = _parse_ra_dec("00h06m13.892894s", "-06d23'35.335300''")
        assert ra < 2.0
        assert dec < -6.0

    def test_3c286_coords(self):
        ra, dec = _parse_ra_dec("13h31m08.288061s", "30d30'32.958850''")
        # 3C286: RA ~ 202.78 deg, Dec ~ 30.51 deg
        assert abs(ra - 202.78) < 0.1
        assert abs(dec - 30.51) < 0.01


# ---------------------------------------------------------------------------
# Entry parsing
# ---------------------------------------------------------------------------


class TestParseEntry:
    def test_parse_3c48(self):
        lines = SAMPLE_ENTRY_3C48.strip().splitlines()
        entry = _parse_entry(lines)
        assert entry is not None
        assert entry.name == "0137+331"
        assert entry.alt_name == "3C48"
        assert entry.position_code == "B"
        assert abs(entry.ra_j2000_deg - 24.422) < 0.01
        assert abs(entry.dec_j2000_deg - 33.159) < 0.01
        assert "L" in entry.bands
        assert "P" in entry.bands
        assert entry.bands["L"].flux_jy == 16.50
        assert entry.bands["L"].uvmax_klambda == 40.0
        assert entry.bands["L"].uvmin_klambda is None
        assert entry.bands["P"].flux_jy == 42.00

    def test_parse_3c286(self):
        lines = SAMPLE_ENTRY_SIMPLE.strip().splitlines()
        entry = _parse_entry(lines)
        assert entry is not None
        assert entry.name == "1331+305"
        assert entry.alt_name == "3C286"
        assert entry.position_code == "A"
        assert "L" in entry.bands
        assert entry.bands["L"].qual_A == "P"
        assert entry.bands["L"].qual_D == "P"
        assert entry.bands["L"].flux_jy == 14.90

    def test_parse_no_altname(self):
        # Entry with no alt name — strip the alt name from the sample
        text = SAMPLE_ENTRY_NO_FLUX.strip()
        lines = text.splitlines()
        entry = _parse_entry(lines)
        assert entry is not None
        assert entry.name == "0006-063"
        # This entry has no alt name in the sample
        assert entry.alt_name is None

    def test_parse_uvmin(self):
        lines = SAMPLE_ENTRY_NO_FLUX.strip().splitlines()
        entry = _parse_entry(lines)
        assert entry is not None
        assert entry.bands["L"].uvmin_klambda == 45.0

    def test_parse_quality_codes(self):
        lines = SAMPLE_ENTRY_3C48.strip().splitlines()
        entry = _parse_entry(lines)
        assert entry is not None
        l_band = entry.bands["L"]
        assert l_band.qual_A == "X"
        assert l_band.qual_B == "P"
        assert l_band.qual_C == "P"
        assert l_band.qual_D == "P"

    def test_parse_all_bands(self):
        lines = SAMPLE_ENTRY_3C48.strip().splitlines()
        entry = _parse_entry(lines)
        assert entry is not None
        assert set(entry.bands.keys()) == {"P", "L", "C", "X", "U", "K", "Q"}

    def test_empty_lines_returns_none(self):
        assert _parse_entry([]) is None
        assert _parse_entry(["", "garbage"]) is None


# ---------------------------------------------------------------------------
# Full text parsing
# ---------------------------------------------------------------------------


class TestParseText:
    def test_parse_two_entries(self):
        text = SAMPLE_ENTRY_3C48 + "\n" + SAMPLE_ENTRY_SIMPLE
        entries = _parse_text(text)
        assert len(entries) == 2
        assert entries[0].name == "0137+331"
        assert entries[1].name == "1331+305"

    def test_parse_empty(self):
        assert _parse_text("") == []
        assert _parse_text("no calibrators here") == []


# ---------------------------------------------------------------------------
# Serialisation round-trip
# ---------------------------------------------------------------------------


class TestSerialisation:
    def test_round_trip(self):
        entry = VLACalibratorEntry(
            name="0137+331",
            alt_name="3C48",
            ra_j2000_deg=24.422,
            dec_j2000_deg=33.159,
            position_code="B",
            bands={
                "L": BandInfo("L", "X", "P", "P", "P", flux_jy=16.5),
            },
        )
        d = _entry_to_dict(entry)
        restored = _entry_from_dict(d)
        assert restored.name == entry.name
        assert restored.alt_name == entry.alt_name
        assert abs(restored.ra_j2000_deg - entry.ra_j2000_deg) < 1e-6
        assert "L" in restored.bands
        assert restored.bands["L"].flux_jy == 16.5

    def test_json_serialisable(self):
        entry = VLACalibratorEntry(
            name="test",
            alt_name=None,
            ra_j2000_deg=0.0,
            dec_j2000_deg=0.0,
            position_code="A",
            bands={},
        )
        d = _entry_to_dict(entry)
        # Should not raise
        json.dumps(d)


# ---------------------------------------------------------------------------
# Cone search (with mock catalogue)
# ---------------------------------------------------------------------------

MOCK_CATALOGUE = [
    VLACalibratorEntry(
        name="0137+331",
        alt_name="3C48",
        ra_j2000_deg=24.4221,
        dec_j2000_deg=33.1598,
        position_code="B",
        bands={"L": BandInfo("L", "X", "P", "P", "P", flux_jy=16.5)},
    ),
    VLACalibratorEntry(
        name="1331+305",
        alt_name="3C286",
        ra_j2000_deg=202.7845,
        dec_j2000_deg=30.5092,
        position_code="A",
        bands={"L": BandInfo("L", "P", "P", "P", "P", flux_jy=14.9)},
    ),
]


class TestConeSearch:
    @pytest.fixture(autouse=True)
    def _inject_catalogue(self, monkeypatch):
        """Inject mock catalogue so no network/cache access is needed."""
        import ms_inspect.util.vla_calibrators as mod

        monkeypatch.setattr(mod, "_catalogue", list(MOCK_CATALOGUE))

    def test_exact_match(self):
        result = cone_search(24.4221, 33.1598, radius_arcsec=5.0)
        assert result is not None
        assert result.name == "0137+331"
        assert result.alt_name == "3C48"
        assert result.separation_arcsec < 1.0

    def test_close_match(self):
        # Offset by ~1 arcsec
        result = cone_search(24.4224, 33.1601, radius_arcsec=5.0)
        assert result is not None
        assert result.name == "0137+331"
        assert result.separation_arcsec < 5.0

    def test_no_match_outside_radius(self):
        # Far away from any calibrator
        result = cone_search(100.0, 50.0, radius_arcsec=5.0)
        assert result is None

    def test_3c286_match(self):
        result = cone_search(202.7845, 30.5092, radius_arcsec=5.0)
        assert result is not None
        assert result.name == "1331+305"
        assert result.alt_name == "3C286"

    def test_declination_guard(self):
        result = cone_search(100.0, -50.0, radius_arcsec=5.0)
        assert result is not None
        assert "not valid" in result.note
        assert result.name == ""

    def test_declination_at_limit_passes(self):
        # At exactly -40 should still search
        result = cone_search(100.0, -40.0, radius_arcsec=5.0)
        # No match expected, but should not trigger the declination guard
        assert result is None

    def test_large_radius(self):
        # With a very large radius, should still pick the closest
        result = cone_search(24.5, 33.2, radius_arcsec=3600.0)
        assert result is not None
        assert result.name == "0137+331"


# ---------------------------------------------------------------------------
# identify_fields
# ---------------------------------------------------------------------------


class TestIdentifyFields:
    @pytest.fixture(autouse=True)
    def _inject_catalogue(self, monkeypatch):
        import ms_inspect.util.vla_calibrators as mod

        monkeypatch.setattr(mod, "_catalogue", list(MOCK_CATALOGUE))

    def test_identify_known_field(self):
        fields = [{"name": "3C48", "ra_deg": 24.4221, "dec_deg": 33.1598}]
        results = identify_fields(fields)
        assert len(results) == 1
        assert results[0].match is not None
        assert results[0].match.name == "0137+331"

    def test_identify_no_coords(self):
        fields = [{"name": "mystery", "ra_deg": None, "dec_deg": None}]
        results = identify_fields(fields)
        assert len(results) == 1
        assert results[0].match is None
        assert "No coordinates" in results[0].note

    def test_identify_multiple(self):
        fields = [
            {"name": "3C48", "ra_deg": 24.4221, "dec_deg": 33.1598},
            {"name": "target", "ra_deg": 100.0, "dec_deg": 50.0},
        ]
        results = identify_fields(fields)
        assert len(results) == 2
        assert results[0].match is not None
        assert results[1].match is None
