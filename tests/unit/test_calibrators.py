"""
Unit tests for util/calibrators.py — catalogue lookup, normalisation,
resolved-source warnings.

No CASA dependency.
"""

from __future__ import annotations

from ms_inspect.util.calibrators import (
    CATALOGUE,
    CalibratorEntry,
    UVRangeEntry,
    _normalise,
    infer_intents_from_role,
    is_known_calibrator,
    lookup,
    resolve_flux_standard,
    resolved_warning_message,
    role_from_intents,
    roles_disagree,
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
    flux_standard="Perley-Butler 2017",
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
    flux_standard="Perley-Butler 2017",
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


# ---------------------------------------------------------------------------
# Catalogue data invariants
#
# These guard the data itself, not the lookup logic. Each one exists because
# the value it checks was wrong at some point and the error was silent.
# ---------------------------------------------------------------------------

# CASA's own standard strings, spelled as setjy accepts them.
_CASA_STANDARDS = {
    "Perley-Butler 2017",
    "Stevens-Reynolds 2016",
    "Butler-JPL-Horizons 2012",
}


class TestCatalogueDataInvariants:
    def test_every_standard_is_a_real_casa_string(self):
        # A hyphen before the year (our old spelling) is silently accepted by
        # the catalogue and rejected by setjy.
        for entry in CATALOGUE:
            if entry.flux_standard is None:
                continue
            assert entry.flux_standard in _CASA_STANDARDS, (
                f"{entry.canonical_name} names a standard CASA does not accept: "
                f"{entry.flux_standard!r}"
            )

    def test_no_alias_maps_to_two_different_sources(self):
        # _normalise strips separators, so two unrelated names can collapse
        # onto one key. Whichever entry is listed first would silently win.
        seen: dict[str, str] = {}
        for entry in CATALOGUE:
            for name in [entry.canonical_name, *entry.aka]:
                key = _normalise(name)
                other = seen.setdefault(key, entry.canonical_name)
                assert other == entry.canonical_name, (
                    f"alias {name!r} normalises to {key!r}, which already belongs to {other}"
                )

    def test_frequency_ranges_are_ordered_and_positive(self):
        for entry in CATALOGUE:
            if entry.freq_range_ghz is None:
                continue
            lo, hi = entry.freq_range_ghz
            assert 0.0 < lo < hi, f"{entry.canonical_name} has range {entry.freq_range_ghz}"

    def test_per_source_ranges_are_not_all_the_standard_range(self):
        # The defect this replaces was a single per-standard range. If every
        # Perley-Butler source shares one range, that regression is back.
        pb = {e.freq_range_ghz for e in CATALOGUE if e.flux_standard == "Perley-Butler 2017"}
        assert len(pb) > 1, "Perley-Butler sources must carry per-source ranges"

    def test_fornax_a_is_the_narrow_case(self):
        # Fornax A is valid over 0.2-0.5 GHz. Under a per-standard range it
        # would pass at 50 GHz, which is the bug in miniature.
        entry = lookup("Fornax A")
        assert entry is not None
        assert entry.freq_range_ghz == (0.2, 0.5)

    def test_source_with_no_casa_standard_is_none_not_a_string(self):
        entry = lookup("PKS0408-65")
        assert entry is not None
        assert entry.flux_standard is None, (
            "PKS0408-65 has no CASA standard; it must route to a manual flux"
        )

    def test_all_twenty_perley_butler_sources_are_present(self):
        pb = [e.canonical_name for e in CATALOGUE if e.flux_standard == "Perley-Butler 2017"]
        assert len(pb) == 20, f"expected 20 Perley-Butler 2017 sources, found {len(pb)}: {pb}"


class TestSolarSystemEntries:
    def test_fifteen_bodies_present(self):
        bodies = [e for e in CATALOGUE if e.solar_system]
        assert len(bodies) == 15

    def test_all_use_the_horizons_standard_except_lutetia(self):
        # CASA matches 19 body names in setObjNum (FluxCalc_SS_JPL_Butler.cc:100-148)
        # and Lutetia is not one of them, so the standard would fail on it.
        for entry in CATALOGUE:
            if not entry.solar_system:
                continue
            if entry.canonical_name == "Lutetia":
                assert entry.flux_standard is None
            else:
                assert entry.flux_standard == "Butler-JPL-Horizons 2012"

    def test_only_the_four_frequency_dependent_bodies_carry_a_range(self):
        # Read from CASA source, not the docs, which state no numbers. Only
        # Venus/Jupiter/Uranus/Neptune have a frequency-dependent brightness
        # temperature; everything else is a constant-Tb uniform disk.
        with_range = {
            e.canonical_name: e.freq_range_ghz
            for e in CATALOGUE
            if e.solar_system and e.freq_range_ghz is not None
        }
        assert with_range == {
            "Venus": (0.303, 350.0),
            "Jupiter": (4.84, 299.8),
            "Uranus": (4.84, 428.3),
            "Neptune": (4.0, 1000.0),
        }

    def test_constant_temperature_marker_partitions_the_bodies(self):
        # The marker and a range are mutually exclusive by construction: a body
        # either has something to extrapolate or it does not. Lutetia is in
        # neither set because CASA has no model for it at all.
        const = {e.canonical_name for e in CATALOGUE if e.constant_brightness_temperature}
        assert const == {
            "Mars",
            "Io",
            "Europa",
            "Ganymede",
            "Callisto",
            "Titan",
            "Ceres",
            "Pallas",
            "Vesta",
            "Juno",
        }
        for entry in CATALOGUE:
            if entry.constant_brightness_temperature:
                assert entry.freq_range_ghz is None, entry.canonical_name

    def test_fixed_sources_never_carry_the_constant_temperature_marker(self):
        # It describes a solar-system thermal model. A quasar must never get it,
        # or it would be exempted from the frequency gate.
        for entry in CATALOGUE:
            if not entry.solar_system:
                assert entry.constant_brightness_temperature is False, entry.canonical_name

    def test_pks1934_range_is_recorded_as_ours(self):
        # CASA codes no bounds for this source and extrapolates silently, so a
        # gate needs a number. The note must say the number is not CASA's.
        entry = lookup("PKS1934-638")
        assert entry is not None
        assert entry.freq_range_ghz == (1.0, 50.0)
        assert entry.notes is not None
        assert "OURS, NOT CASA" in entry.notes

    def test_all_marked_resolved(self):
        # Apparent diameter is ephemeris-dependent and even Lutetia is
        # resolved on ALMA long baselines. False would fail silently.
        for entry in CATALOGUE:
            if entry.solar_system:
                assert entry.resolved is True

    def test_ceres_resolves(self):
        # The ALMA test dataset's flux calibrator, absent before this change.
        entry = lookup("Ceres")
        assert entry is not None
        assert entry.solar_system is True
        assert "flux" in entry.role

    def test_fixed_sources_are_not_marked_solar_system(self):
        entry = lookup("3C286")
        assert entry is not None
        assert entry.solar_system is False


# ---------------------------------------------------------------------------
# Intent -> role mapping
# ---------------------------------------------------------------------------


class TestRoleFromIntents:
    def test_suffix_is_ignored(self):
        # ALMA writes ON_SOURCE, the VLA writes UNSPECIFIED. Same role.
        assert role_from_intents(["CALIBRATE_FLUX#ON_SOURCE"]) == ["flux"]
        assert role_from_intents(["CALIBRATE_FLUX#UNSPECIFIED"]) == ["flux"]

    def test_bare_intent_without_suffix(self):
        assert role_from_intents(["OBSERVE_TARGET"]) == ["target"]

    def test_multiple_roles_are_sorted_and_deduplicated(self):
        got = role_from_intents(
            [
                "CALIBRATE_BANDPASS#ON_SOURCE",
                "CALIBRATE_FLUX#ON_SOURCE",
                "CALIBRATE_FLUX#UNSPECIFIED",
            ]
        )
        assert got == ["bandpass", "flux"]

    def test_technical_intents_yield_no_role(self):
        # These ride along on calibrators and targets alike. Mapping them would
        # give every ALMA field a role.
        assert role_from_intents(["CALIBRATE_ATMOSPHERE#ON_SOURCE"]) == []
        assert role_from_intents(["CALIBRATE_POINTING#ON_SOURCE"]) == []
        assert role_from_intents(["CALIBRATE_WVR#ON_SOURCE"]) == []

    def test_technical_intents_do_not_mask_a_real_one(self):
        got = role_from_intents(["CALIBRATE_ATMOSPHERE#ON_SOURCE", "OBSERVE_TARGET#ON_SOURCE"])
        assert got == ["target"]

    def test_unrecognised_intent_is_ignored_not_guessed(self):
        assert role_from_intents(["SOMETHING_NEW#ON_SOURCE"]) == []

    def test_empty_input(self):
        assert role_from_intents([]) == []

    def test_round_trips_with_infer_intents_from_role(self):
        # The two functions are inverses over the catalogue's own vocabulary.
        for role in (["flux"], ["bandpass"], ["flux", "bandpass"]):
            assert role_from_intents(infer_intents_from_role(role)) == sorted(role)


class TestRolesDisagree:
    def test_disjoint_sets_disagree(self):
        # The ALMA 3C286 case: intents say target, catalogue says flux cal.
        assert roles_disagree(["target"], ["flux", "bandpass"]) is True

    def test_overlap_is_a_narrower_truth_not_a_contradiction(self):
        assert roles_disagree(["bandpass"], ["flux", "bandpass"]) is False

    def test_identical_sets_agree(self):
        assert roles_disagree(["flux", "bandpass"], ["flux", "bandpass"]) is False

    def test_nothing_to_compare_against_is_not_disagreement(self):
        assert roles_disagree([], ["flux"]) is False
        assert roles_disagree(["target"], []) is False
        assert roles_disagree([], []) is False


# ---------------------------------------------------------------------------
# Flux standard resolution (FLUX_STANDARD_DESIGN.md 2.2)
# ---------------------------------------------------------------------------


class TestResolveFluxStandard:
    def test_no_catalogue_entry_is_unavailable(self):
        res = resolve_flux_standard(None, 1.0, 2.0)
        assert res.standard is None
        assert res.flag == "UNAVAILABLE"
        assert res.range_checked is False
        assert res.needs_manual_flux is False

    def test_source_with_no_casa_standard_is_complete_not_unavailable(self):
        # We KNOW the answer and the answer is "there is no standard". That is
        # not a gap, and a caller must not read it as one and pick a fallback.
        res = resolve_flux_standard(lookup("PKS0408-65"), 1.0, 2.0)
        assert res.standard is None
        assert res.flag == "COMPLETE"
        assert res.needs_manual_flux is True
        assert "manual" in res.note.lower()

    def test_lutetia_routes_to_manual_flux(self):
        res = resolve_flux_standard(lookup("Lutetia"), 215.0, 235.0)
        assert res.standard is None
        assert res.needs_manual_flux is True

    def test_constant_temperature_body_keeps_its_standard(self):
        # Ceres is the ALMA test dataset's flux calibrator. No range exists, so
        # no gate can run, but the standard is still correct.
        res = resolve_flux_standard(lookup("Ceres"), 215.0, 235.0)
        assert res.standard == "Butler-JPL-Horizons 2012"
        assert res.flag == "COMPLETE"
        assert res.range_checked is False

    def test_constant_temperature_note_states_what_it_cannot_see(self):
        # Without this clause the marker is a silent pass -- the same failure
        # mode as CASA's own silent extrapolation.
        res = resolve_flux_standard(lookup("Ceres"), 215.0, 235.0)
        assert "cannot see" in res.note

    def test_unreadable_frequency_is_inferred_not_complete(self):
        # A check that did not run is not a check that passed.
        res = resolve_flux_standard(lookup("3C286"), None, None)
        assert res.standard == "Perley-Butler 2017"
        assert res.flag == "INFERRED"
        assert res.range_checked is False
        assert "NOT checked" in res.note

    def test_inside_the_range_passes_and_says_so(self):
        res = resolve_flux_standard(lookup("3C286"), 1.0, 2.0)
        assert res.standard == "Perley-Butler 2017"
        assert res.flag == "COMPLETE"
        assert res.range_checked is True
        assert "0.05-50 GHz" in res.note
        assert "1-2 GHz" in res.note

    def test_entirely_outside_the_range_yields_no_standard(self):
        # 3C286 at ALMA Band 6. Perley-Butler stops at 50 GHz; this is the
        # defect that started the whole change.
        res = resolve_flux_standard(lookup("3C286"), 215.0, 235.0)
        assert res.standard is None
        assert res.flag == "UNAVAILABLE"
        assert res.range_checked is True
        assert "entirely outside" in res.note

    def test_a_span_that_straddles_an_edge_fails(self):
        # Virgo A is valid to 3 GHz. Half a band is not a usable flux scale,
        # and the note must distinguish this from missing the range entirely.
        res = resolve_flux_standard(lookup("VirA"), 1.0, 10.0)
        assert res.standard is None
        assert res.flag == "UNAVAILABLE"
        assert res.range_checked is True
        assert "partially overlaps" in res.note

    def test_solar_system_body_with_a_range_is_gated_like_any_other(self):
        # Uranus at Band 6 passes; Uranus at L band does not.
        assert resolve_flux_standard(lookup("Uranus"), 215.0, 235.0).standard is not None
        low = resolve_flux_standard(lookup("Uranus"), 1.0, 2.0)
        assert low.standard is None
        assert low.range_checked is True

    def test_every_catalogue_entry_resolves_without_raising(self):
        # The resolver is called per field on every MS. A single entry shape it
        # cannot handle would break ms_field_list on unrelated data.
        for entry in CATALOGUE:
            for lo, hi in [(None, None), (1.0, 2.0), (215.0, 235.0)]:
                res = resolve_flux_standard(entry, lo, hi)
                assert res.flag in {"COMPLETE", "INFERRED", "UNAVAILABLE"}
                assert res.note


class TestRangeProvenanceIsStated:
    """
    A range CASA enforces and a range only we enforce carry different risk. If
    the note does not say which it is, a reader cannot tell whether ignoring the
    gate means "CASA will warn me" or "nothing will warn me".
    """

    def test_perley_butler_range_is_declared_as_casas(self):
        # These ranges come from the ValidFreqRange keyword in CASA's own
        # PerleyButler2017Coeffs table, not from us. CASA warns outside them —
        # and then returns the extrapolated value regardless.
        res = resolve_flux_standard(lookup("3C286"), 219.0, 235.0)
        assert "CASA's own" in res.note
        assert "PerleyButler2017Coeffs" in res.note
        assert "RANGE IS OURS" not in res.note

    def test_stevens_reynolds_range_is_declared_as_ours(self):
        res = resolve_flux_standard(lookup("PKS1934-638"), 60.0, 70.0)
        assert "RANGE IS OURS" in res.note

    def test_horizons_range_is_declared_as_casas(self):
        res = resolve_flux_standard(lookup("Jupiter"), 1.0, 2.0)
        assert "CASA's own" in res.note
        assert "RANGE IS OURS" not in res.note

    def test_every_out_of_range_verdict_states_a_provenance(self):
        # No entry may produce a bare range with no account of where it came
        # from. The out-of-range verdict is UNAVAILABLE with range_checked set;
        # anything else is a different branch and is not this test's business.
        #
        # The counter matters: if the flag or the branch structure changes, this
        # sweep would silently check nothing at all rather than fail.
        checked = 0
        for entry in CATALOGUE:
            if entry.freq_range_ghz is None:
                continue
            for lo, hi in [(0.01, 0.02), (900.0, 1100.0)]:
                res = resolve_flux_standard(entry, lo, hi)
                if res.flag == "UNAVAILABLE" and res.range_checked:
                    assert "OURS" in res.note or "CASA's own" in res.note, entry.canonical_name
                    checked += 1
        assert checked > 30, f"sweep exercised only {checked} out-of-range verdicts"


class TestCatalogueMatchesCasaCoefficientTable:
    """
    The Perley-Butler ranges are copies of CASA's ValidFreqRange keywords. A
    copy can drift from its source, and two entries already had: 3C123 read
    0.06 where CASA says 0.05, and J0133-3629 was spelled J0133-3649.

    These are pinned as literals rather than read from the live table, because
    the table needs casatools and a casadata install. The values were taken from
    the shipped PerleyButler2017Coeffs table.
    """

    CASA_RANGES = {
        "3C123": (0.05, 50.0),
        "3C138": (0.2, 50.0),
        "3C147": (0.05, 50.0),
        "3C196": (0.05, 50.0),
        "3C286": (0.05, 50.0),
        "3C295": (0.05, 50.0),
        "3C353": (0.2, 4.0),
        "3C380": (0.05, 4.0),
        "3C444": (0.2, 12.0),
        "3C48": (0.05, 50.0),
        "CasA": (0.2, 4.0),
        "CygA": (0.05, 12.0),
        "ForA": (0.2, 0.5),
        "HerA": (0.2, 12.0),
        "HydraA": (0.05, 12.0),
        "J0133-3629": (0.2, 4.0),
        "J0444-2809": (0.2, 2.0),
        "PicA": (0.2, 4.0),
        "TauA": (0.05, 4.0),
        "VirA": (0.05, 3.0),
    }

    def test_every_range_matches_casa(self):
        for name, expected in self.CASA_RANGES.items():
            entry = lookup(name)
            assert entry is not None, f"{name} missing from the catalogue"
            assert entry.freq_range_ghz == expected, name

    def test_all_twenty_casa_sources_are_present(self):
        ours = {e.canonical_name for e in CATALOGUE if e.flux_standard == "Perley-Butler 2017"}
        assert ours == set(self.CASA_RANGES)

    def test_the_old_j0133_spelling_still_matches(self):
        # An MS written before the fix may carry the typo as its field name.
        assert lookup("J0133-3649").canonical_name == "J0133-3629"
