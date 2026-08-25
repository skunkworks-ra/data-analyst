"""
Unit tests for ms_field_list role resolution.

Written BEFORE the intent-derived-role change, deliberately. The VLA cases in
TestVlaRegression capture behaviour that already worked and must survive; they
were run green against the old catalogue-only code first. The behaviour-change
cases live in their own classes.

The modelled data is AB1345 / G55.7+3.4 (VLA, full intents) and the ALMA 3C286
Band 6 test run, where the intents say TARGET on a field the catalogue calls a
flux calibrator.

No CASA dependency — msmd is faked.
"""

from __future__ import annotations

import math
from contextlib import contextmanager

from ms_inspect.tools import fields as fields_mod
from ms_inspect.tools.fields import run as field_list_run


class FakeMsmd:
    """Minimal stand-in for casatools.msmetadata, for ms_field_list only."""

    def __init__(self, specs, spws_for_field=None, chan_freqs=None, wvr_spws=None):
        # specs: list of (name, intents, ra_deg, dec_deg) indexed by field id
        self._specs = specs
        # Frequency support is opt-in. When it is absent the accessors raise, as
        # they would on an msmd build lacking them, and the tool must degrade to
        # UNAVAILABLE rather than fail.
        self._spws_for_field = spws_for_field
        self._chan_freqs = chan_freqs or {}
        self._wvr_spws = wvr_spws

    def spwsforfield(self, fid):
        if self._spws_for_field is None:
            raise AttributeError("spwsforfield")
        return list(self._spws_for_field[fid])

    def chanfreqs(self, spw):
        if spw not in self._chan_freqs:
            raise RuntimeError(f"no such spw {spw}")
        return list(self._chan_freqs[spw])

    def wvrspws(self):
        if self._wvr_spws is None:
            raise AttributeError("wvrspws")
        return list(self._wvr_spws)

    def fieldnames(self):
        return [s[0] for s in self._specs]

    def phasecenter(self, fid):
        _, _, ra_deg, dec_deg = self._specs[fid]
        if ra_deg is None:
            return {}
        return {
            "type": "direction",
            "refer": "J2000",
            "m0": {"unit": "rad", "value": math.radians(ra_deg)},
            "m1": {"unit": "rad", "value": math.radians(dec_deg)},
        }

    def intentsforfield(self, fid):
        return list(self._specs[fid][1])

    def sourceidforfield(self, fids):
        return list(fids)

    def scannumbers(self):
        return list(range(1, len(self._specs) + 1))

    def fieldsforscan(self, snum):
        return [snum - 1]

    def timesforscans(self, snums):
        base = snums[0] * 1000.0
        return [base, base + 300.0]


def _patch(monkeypatch, specs, **msmd_kwargs):
    @contextmanager
    def fake_open(_ms_path):
        yield FakeMsmd(specs, **msmd_kwargs)

    monkeypatch.setattr(fields_mod, "open_msmd", fake_open)
    monkeypatch.setattr(fields_mod, "validate_ms_path", lambda p: p)


def _rec(result, name):
    for r in result["data"]["fields"]:
        if r["name"] == name:
            return r
    raise AssertionError(f"field {name!r} not in result")


def _val(rec, key):
    return rec[key]["value"]


def _flag(rec, key):
    return rec[key]["flag"]


# A normal VLA observation with complete intents. Nothing here is unusual;
# that is the point — this is the behaviour the change must not disturb.
_VLA_FULL_INTENTS = [
    ("3C286", ["CALIBRATE_FLUX#UNSPECIFIED", "CALIBRATE_BANDPASS#UNSPECIFIED"], 202.78, 30.51),
    ("J1925+2106", ["CALIBRATE_PHASE#UNSPECIFIED"], 291.38, 21.11),
    ("G55.7+3.4", ["OBSERVE_TARGET#UNSPECIFIED"], 292.63, 20.19),
]

# The ALMA 3C286 Band 6 run: intents say TARGET on the catalogue's flux
# calibrator, and Ceres — the actual flux calibrator — carries flux intents.
_ALMA_CONTRADICTION = [
    ("3C286", ["OBSERVE_TARGET#ON_SOURCE"], 202.78, 30.51),
    ("Ceres", ["CALIBRATE_FLUX#ON_SOURCE"], 150.0, -10.0),
]


# A real VLA L-band setup for the same three fields: 1-2 GHz, no WVR windows.
# The role tests do not need it, but the flux-standard gate does — without a
# frequency the gate cannot run, and a regression test that never runs the gate
# proves nothing about it.
_VLA_SPWS = {0: [0, 1], 1: [0, 1], 2: [0, 1]}
_VLA_CHAN_FREQS = {
    0: [1.0e9, 1.2e9, 1.4e9],
    1: [1.6e9, 1.8e9, 2.0e9],
}


def _patch_vla(monkeypatch, specs=None):
    _patch(
        monkeypatch,
        specs or _VLA_FULL_INTENTS,
        spws_for_field=_VLA_SPWS,
        chan_freqs=_VLA_CHAN_FREQS,
    )


class TestVlaRegression:
    """Behaviour that worked before the change and must still work."""

    def test_all_fields_returned_with_names(self, monkeypatch):
        _patch(monkeypatch, _VLA_FULL_INTENTS)
        result = field_list_run("/fake.ms")
        assert result["data"]["n_fields"] == 3
        names = [r["name"] for r in result["data"]["fields"]]
        assert names == ["3C286", "J1925+2106", "G55.7+3.4"]

    def test_coordinates_survive(self, monkeypatch):
        _patch(monkeypatch, _VLA_FULL_INTENTS)
        rec = _rec(field_list_run("/fake.ms"), "3C286")
        assert _flag(rec, "ra_j2000_deg") == "COMPLETE"
        assert abs(_val(rec, "ra_j2000_deg") - 202.78) < 1e-4
        assert abs(_val(rec, "dec_j2000_deg") - 30.51) < 1e-4

    def test_raw_intents_pass_through_unchanged(self, monkeypatch):
        _patch(monkeypatch, _VLA_FULL_INTENTS)
        rec = _rec(field_list_run("/fake.ms"), "3C286")
        assert _flag(rec, "intents") == "COMPLETE"
        assert _val(rec, "intents") == [
            "CALIBRATE_BANDPASS#UNSPECIFIED",
            "CALIBRATE_FLUX#UNSPECIFIED",
        ]

    def test_catalogue_match_and_flux_standard_survive(self, monkeypatch):
        # With frequencies present the gate RUNS and passes: 3C286 at L band is
        # inside Perley-Butler's 0.05-50 GHz. Asserting the value alone would
        # not have caught the flag dropping to INFERRED.
        _patch_vla(monkeypatch)
        rec = _rec(field_list_run("/fake.ms"), "3C286")
        assert _val(rec, "calibrator_match") == "3C286"
        assert _val(rec, "flux_standard") == "Perley-Butler 2017"
        assert _flag(rec, "flux_standard") == "COMPLETE"
        assert rec["flux_standard_range_checked"] is True
        assert _val(rec, "resolved_source") is False

    def test_normal_vla_reduction_raises_no_flux_standard_warning(self, monkeypatch):
        _patch_vla(monkeypatch)
        text = " ".join(field_list_run("/fake.ms").get("warnings", []))
        assert "flux standard" not in text.lower()

    def test_agreeing_calibrator_keeps_its_role(self, monkeypatch):
        # 3C286 with flux+bandpass intents on a VLA MS: the catalogue said
        # flux+bandpass before the change and the intents say the same now.
        _patch(monkeypatch, _VLA_FULL_INTENTS)
        rec = _rec(field_list_run("/fake.ms"), "3C286")
        assert set(_val(rec, "field_role")) == {"flux", "bandpass"}
        assert _flag(rec, "field_role") == "COMPLETE"

    def test_agreement_raises_no_warning(self, monkeypatch):
        _patch(monkeypatch, _VLA_FULL_INTENTS)
        result = field_list_run("/fake.ms")
        text = " ".join(result.get("warnings", []))
        assert "disagree" not in text.lower()

    def test_uncatalogued_field_still_gets_no_catalogue_match(self, monkeypatch):
        _patch(monkeypatch, _VLA_FULL_INTENTS)
        rec = _rec(field_list_run("/fake.ms"), "G55.7+3.4")
        assert _flag(rec, "calibrator_match") == "UNAVAILABLE"
        assert _flag(rec, "flux_standard") == "UNAVAILABLE"


class TestRoleComesFromIntents:
    def test_phase_calibrator_gets_a_role_at_last(self, monkeypatch):
        # Not in the catalogue, so before the change this had NO role at all.
        _patch(monkeypatch, _VLA_FULL_INTENTS)
        rec = _rec(field_list_run("/fake.ms"), "J1925+2106")
        assert _val(rec, "field_role") == ["phase"]
        assert _flag(rec, "field_role") == "COMPLETE"

    def test_target_is_a_role_not_a_blank(self, monkeypatch):
        _patch(monkeypatch, _VLA_FULL_INTENTS)
        rec = _rec(field_list_run("/fake.ms"), "G55.7+3.4")
        assert _val(rec, "field_role") == ["target"]
        assert _flag(rec, "field_role") == "COMPLETE"

    def test_alma_and_vla_intent_suffixes_agree(self, monkeypatch):
        # ON_SOURCE vs UNSPECIFIED must not change the answer.
        _patch(monkeypatch, [("G55.7+3.4", ["OBSERVE_TARGET#ON_SOURCE"], 292.63, 20.19)])
        rec = _rec(field_list_run("/fake.ms"), "G55.7+3.4")
        assert _val(rec, "field_role") == ["target"]


class TestCatalogueFallbackIsPerField:
    def test_field_without_intents_falls_back_when_coverage_is_high(self, monkeypatch):
        # Two of three fields have intents, so the old 50% gate was NOT tripped
        # and the third field got nothing. It must now use the catalogue.
        specs = [
            ("J1925+2106", ["CALIBRATE_PHASE#UNSPECIFIED"], 291.38, 21.11),
            ("G55.7+3.4", ["OBSERVE_TARGET#UNSPECIFIED"], 292.63, 20.19),
            ("3C286", [], 202.78, 30.51),
        ]
        _patch(monkeypatch, specs)
        rec = _rec(field_list_run("/fake.ms"), "3C286")
        assert set(_val(rec, "field_role")) == {"flux", "bandpass"}
        assert _flag(rec, "field_role") == "INFERRED"

    def test_fallback_note_says_it_is_suitability_not_evidence(self, monkeypatch):
        specs = [
            ("J1925+2106", ["CALIBRATE_PHASE#UNSPECIFIED"], 291.38, 21.11),
            ("G55.7+3.4", ["OBSERVE_TARGET#UNSPECIFIED"], 292.63, 20.19),
            ("3C286", [], 202.78, 30.51),
        ]
        _patch(monkeypatch, specs)
        rec = _rec(field_list_run("/fake.ms"), "3C286")
        assert "no intents" in rec["field_role"]["note"].lower()

    def test_no_intents_and_no_catalogue_is_unavailable(self, monkeypatch):
        _patch(monkeypatch, [("SomeTarget", [], 10.0, 10.0)])
        rec = _rec(field_list_run("/fake.ms"), "SomeTarget")
        assert _val(rec, "field_role") is None
        assert _flag(rec, "field_role") == "UNAVAILABLE"


class TestDisagreement:
    def test_alma_3c286_takes_the_intent_role(self, monkeypatch):
        _patch(monkeypatch, _ALMA_CONTRADICTION)
        rec = _rec(field_list_run("/fake.ms"), "3C286")
        assert _val(rec, "field_role") == ["target"]
        assert _flag(rec, "field_role") == "COMPLETE"

    def test_catalogue_answer_stays_visible(self, monkeypatch):
        _patch(monkeypatch, _ALMA_CONTRADICTION)
        rec = _rec(field_list_run("/fake.ms"), "3C286")
        assert set(_val(rec, "catalogue_role")) == {"flux", "bandpass"}

    def test_disagreement_is_warned_loudly(self, monkeypatch):
        _patch(monkeypatch, _ALMA_CONTRADICTION)
        result = field_list_run("/fake.ms")
        hits = [w for w in result["warnings"] if "3C286" in w and "disagree" in w.lower()]
        assert hits, result["warnings"]
        # Both sides must be named, or the reader cannot judge it.
        assert "target" in hits[0]
        assert "flux" in hits[0]

    def test_overlapping_roles_do_not_warn(self, monkeypatch):
        # Used as bandpass only; catalogue says flux+bandpass. Overlap, so the
        # intents are a narrower truth, not a contradiction.
        _patch(monkeypatch, [("3C286", ["CALIBRATE_BANDPASS#UNSPECIFIED"], 202.78, 30.51)])
        result = field_list_run("/fake.ms")
        assert not [w for w in result["warnings"] if "disagree" in w.lower()]

    def test_ceres_is_the_flux_calibrator_it_claims_to_be(self, monkeypatch):
        _patch(monkeypatch, _ALMA_CONTRADICTION)
        rec = _rec(field_list_run("/fake.ms"), "Ceres")
        assert _val(rec, "field_role") == ["flux"]
        assert _val(rec, "flux_standard") == "Butler-JPL-Horizons 2012"


class TestIntentCoverageIsReportedNotGated:
    def test_coverage_is_a_fraction_with_its_inputs(self, monkeypatch):
        # Was a boolean `heuristic_intents`. A threshold verdict hid the number
        # and, once roles went per field, described no field's role correctly.
        specs = [
            ("3C286", ["CALIBRATE_FLUX#UNSPECIFIED"], 202.78, 30.51),
            ("G55.7+3.4", [], 292.63, 20.19),
        ]
        _patch(monkeypatch, specs)
        data = field_list_run("/fake.ms")["data"]
        assert data["intent_coverage_fraction"] == 0.5
        assert data["n_fields_with_intents"] == 1
        assert data["n_fields"] == 2
        assert "heuristic_intents" not in data

    def test_full_coverage_reports_one(self, monkeypatch):
        _patch(monkeypatch, _VLA_FULL_INTENTS)
        assert field_list_run("/fake.ms")["data"]["intent_coverage_fraction"] == 1.0

    def test_low_coverage_still_warns_about_coverage(self, monkeypatch):
        specs = [
            ("3C286", [], 202.78, 30.51),
            ("J1925+2106", [], 291.38, 21.11),
            ("G55.7+3.4", [], 292.63, 20.19),
        ]
        _patch(monkeypatch, specs)
        result = field_list_run("/fake.ms")
        assert any("coverage" in w for w in result["warnings"])

    def test_role_resolution_is_identical_regardless_of_coverage(self, monkeypatch):
        # 3C286 has no intents in both cases. Under the old code the first
        # tripped the 50% gate and the second did not, and only the first got
        # a catalogue role. The answer must not depend on the other fields.
        low = [("3C286", [], 202.78, 30.51), ("G55.7+3.4", [], 292.63, 20.19)]
        high = [
            ("3C286", [], 202.78, 30.51),
            ("G55.7+3.4", ["OBSERVE_TARGET#UNSPECIFIED"], 292.63, 20.19),
            ("J1925+2106", ["CALIBRATE_PHASE#UNSPECIFIED"], 291.38, 21.11),
        ]
        _patch(monkeypatch, low)
        low_rec = _rec(field_list_run("/fake.ms"), "3C286")
        _patch(monkeypatch, high)
        high_rec = _rec(field_list_run("/fake.ms"), "3C286")

        assert low_rec["field_role"] == high_rec["field_role"]


# Frequency is per field, not per MS: 3C286 here is observed in the two science
# windows only, Ceres in one of them. An MS-wide read would give both the same
# answer and would be wrong for Ceres.
_ALMA_SPWS = {0: [0, 1, 2], 1: [1]}
_ALMA_CHAN_FREQS = {
    # ALMA Band 6, two science windows, frequencies in Hz.
    1: [2.240e11, 2.245e11, 2.250e11],
    2: [2.360e11, 2.365e11, 2.370e11],
    # A water-vapour-radiometer window. Real, and nowhere near the science band.
    0: [1.8310e11, 1.8315e11],
}


class TestObservingFrequency:
    """Stage 1 of the flux-standard work: read the frequency, per field."""

    def test_span_covers_only_the_spws_the_field_was_observed_in(self, monkeypatch):
        _patch(
            monkeypatch,
            _ALMA_CONTRADICTION,
            spws_for_field=_ALMA_SPWS,
            chan_freqs=_ALMA_CHAN_FREQS,
            wvr_spws=[0],
        )
        result = field_list_run("/fake.ms")

        c286 = _val(_rec(result, "3C286"), "observing_frequency")
        assert c286["n_spw"] == 2
        assert abs(c286["min_ghz"] - 224.0) < 1e-6
        assert abs(c286["max_ghz"] - 237.0) < 1e-6
        assert abs(c286["centre_ghz"] - 230.5) < 1e-6

        # Ceres saw one window. Its span must be narrower, not the MS-wide span.
        ceres = _val(_rec(result, "Ceres"), "observing_frequency")
        assert ceres["n_spw"] == 1
        assert abs(ceres["min_ghz"] - 224.0) < 1e-6
        assert abs(ceres["max_ghz"] - 225.0) < 1e-6

    def test_wvr_window_is_excluded_and_the_exclusion_is_reported(self, monkeypatch):
        _patch(
            monkeypatch,
            _ALMA_CONTRADICTION,
            spws_for_field=_ALMA_SPWS,
            chan_freqs=_ALMA_CHAN_FREQS,
            wvr_spws=[0],
        )
        rec = _rec(field_list_run("/fake.ms"), "3C286")
        # 183 GHz would have become the reported minimum had it leaked through.
        assert _val(rec, "observing_frequency")["min_ghz"] > 200.0
        assert "WVR" in rec["observing_frequency"]["note"]

    def test_without_a_wvr_accessor_no_window_is_dropped(self, monkeypatch):
        # A VLA MS: msmd has no wvrspws(), so the tool must not silently drop
        # spectral windows it cannot classify.
        _patch(
            monkeypatch,
            _ALMA_CONTRADICTION,
            spws_for_field=_ALMA_SPWS,
            chan_freqs=_ALMA_CHAN_FREQS,
        )
        rec = _rec(field_list_run("/fake.ms"), "3C286")
        assert _val(rec, "observing_frequency")["n_spw"] == 3

    def test_missing_spw_metadata_degrades_to_unavailable(self, monkeypatch):
        # The pre-existing fake has no frequency accessors at all.
        _patch(monkeypatch, _VLA_FULL_INTENTS)
        rec = _rec(field_list_run("/fake.ms"), "3C286")
        assert _flag(rec, "observing_frequency") == "UNAVAILABLE"
        assert _val(rec, "observing_frequency") is None

    def test_unreadable_spw_does_not_lose_the_readable_ones(self, monkeypatch):
        _patch(
            monkeypatch,
            _ALMA_CONTRADICTION,
            spws_for_field={0: [1, 99], 1: [1]},
            chan_freqs={1: [2.240e11, 2.250e11]},
        )
        rec = _rec(field_list_run("/fake.ms"), "3C286")
        assert _flag(rec, "observing_frequency") == "COMPLETE"
        assert abs(_val(rec, "observing_frequency")["max_ghz"] - 225.0) < 1e-6


class TestFluxStandardIsGatedOnFrequency:
    """
    Stage 2: the standard is resolved from the field's own frequency, not
    echoed from the catalogue.

    The ALMA cases here are the defect that started the change: ms_field_list
    reported 'Perley-Butler 2017' COMPLETE on a 230 GHz field, where that scale
    stops at 50 GHz.
    """

    def _alma(self, monkeypatch):
        _patch(
            monkeypatch,
            _ALMA_CONTRADICTION,
            spws_for_field=_ALMA_SPWS,
            chan_freqs=_ALMA_CHAN_FREQS,
            wvr_spws=[0],
        )

    def test_3c286_at_band_6_gets_no_standard(self, monkeypatch):
        self._alma(monkeypatch)
        rec = _rec(field_list_run("/fake.ms"), "3C286")
        assert _val(rec, "flux_standard") is None
        assert _flag(rec, "flux_standard") == "UNAVAILABLE"
        assert rec["flux_standard_range_checked"] is True

    def test_out_of_range_warns_and_names_both_numbers(self, monkeypatch):
        # The operator has to pick a different source. A note alone would not
        # reach them.
        self._alma(monkeypatch)
        text = " ".join(field_list_run("/fake.ms").get("warnings", []))
        assert "0.05-50 GHz" in text
        assert "224-237 GHz" in text

    def test_ceres_keeps_its_standard_with_no_range_check(self, monkeypatch):
        self._alma(monkeypatch)
        rec = _rec(field_list_run("/fake.ms"), "Ceres")
        assert _val(rec, "flux_standard") == "Butler-JPL-Horizons 2012"
        assert _flag(rec, "flux_standard") == "COMPLETE"
        assert rec["flux_standard_range_checked"] is False

    def test_constant_temperature_body_produces_a_note_not_a_warning(self, monkeypatch):
        # The user's call: no range exists because of a CASA modelling choice,
        # not because our metadata is short. Warning here would fire on every
        # ALMA dataset and train the operator to ignore warnings.
        self._alma(monkeypatch)
        result = field_list_run("/fake.ms")
        rec = _rec(result, "Ceres")
        assert "brightness temperature" in rec["flux_standard"]["note"]
        # Ceres still carries its pre-existing catalogue note (apparent
        # diameter), which is a different subject. Nothing about the flux
        # standard may reach the warning list.
        ceres_warnings = [w for w in result.get("warnings", []) if w.startswith("[Ceres]")]
        assert not any("flux standard" in w.lower() for w in ceres_warnings)
        assert not any("brightness temperature" in w for w in ceres_warnings)

    def test_unreadable_frequency_is_inferred_not_complete(self, monkeypatch):
        # No frequency accessors at all. The standard is still reported, but as
        # INFERRED — the gate did not run, and that is not a pass.
        _patch(monkeypatch, _VLA_FULL_INTENTS)
        rec = _rec(field_list_run("/fake.ms"), "3C286")
        assert _val(rec, "flux_standard") == "Perley-Butler 2017"
        assert _flag(rec, "flux_standard") == "INFERRED"
        assert rec["flux_standard_range_checked"] is False

    def test_uncatalogued_field_reports_no_standard_and_no_warning(self, monkeypatch):
        # A science target is not a failure to resolve a flux standard.
        _patch_vla(monkeypatch)
        result = field_list_run("/fake.ms")
        rec = _rec(result, "G55.7+3.4")
        assert _flag(rec, "flux_standard") == "UNAVAILABLE"
        assert rec["flux_standard_range_checked"] is False
        target_warnings = [w for w in result.get("warnings", []) if w.startswith("[G55.7+3.4]")]
        assert not any("flux standard" in w.lower() for w in target_warnings)
