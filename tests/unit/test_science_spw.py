"""
Unit tests for util/science_spw.py.

The reference fixture reproduces the real spectral-window layout of
uid___A002_X85c183_X10a (ALMA Band 6, 56 windows), measured with msmd. That
dataset is the reason this module exists: tools were offering its pointing and
water-vapour windows for bandpass solving, and inferring Band 5 from a 183 GHz
water-vapour window.

No CASA required — the fixture is the measured layout, not a live MS.
"""

from __future__ import annotations

import pytest

from ms_inspect.util.science_spw import (
    NoScienceSpwError,
    select_science_spws,
)

# --- the real X10a layout ----------------------------------------------------

_SCIENCE = [
    "OBSERVE_TARGET#ON_SOURCE",
    "CALIBRATE_BANDPASS#ON_SOURCE",
    "CALIBRATE_PHASE#ON_SOURCE",
    "CALIBRATE_AMPLI#ON_SOURCE",
    "CALIBRATE_FLUX#ON_SOURCE",
    "CALIBRATE_POLARIZATION#ON_SOURCE",
    "CALIBRATE_WVR#ON_SOURCE",  # present on EVERY window, incl. science
]
_POINTING = ["CALIBRATE_POINTING#ON_SOURCE", "CALIBRATE_WVR#ON_SOURCE"]
_ATMOS = [
    "CALIBRATE_ATMOSPHERE#ON_SOURCE",
    "CALIBRATE_ATMOSPHERE#OFF_SOURCE",
    "CALIBRATE_SIDEBAND_RATIO#ON_SOURCE",
    "CALIBRATE_WVR#ON_SOURCE",
    "CALIBRATE_WVR#OFF_SOURCE",
]


@pytest.fixture
def x10a():
    """n_chan, intents, names, spws_with_data as measured on the real MS."""
    n_chan, intents, names = {}, {}, {}

    n_chan[0], intents[0], names[0] = 4, [], "WVR#NOMINAL"
    # 1-8 pointing, 9-16 atmosphere: FULL_RES 128ch alternating with CH_AVG 1ch
    for spw in range(1, 17):
        n_chan[spw] = 128 if spw % 2 == 1 else 1
        intents[spw] = _POINTING if spw <= 8 else _ATMOS
        kind = "FULL_RES" if spw % 2 == 1 else "CH_AVG"
        names[spw] = f"ALMA_RB_06#BB_{(spw - 1) // 2 % 4 + 1}#SW-01#{kind}"
    # 17-24 science: FULL_RES 64ch alternating with CH_AVG 1ch
    for spw in range(17, 25):
        n_chan[spw] = 64 if spw % 2 == 1 else 1
        intents[spw] = _SCIENCE
        kind = "FULL_RES" if spw % 2 == 1 else "CH_AVG"
        names[spw] = f"ALMA_RB_06#BB_{(spw - 17) // 2 + 1}#SW-01#{kind}"
    # 25-55 per-antenna WVR: no intents, no DATA_DESCRIPTION row
    for spw in range(25, 56):
        n_chan[spw], intents[spw], names[spw] = 4, [], f"WVR#Antenna_{spw - 25}"

    with_data = set(range(0, 25))  # 0-24 have rows; 25-55 do not
    return n_chan, intents, names, with_data


class TestReferenceDataset:
    def test_selects_exactly_the_four_full_resolution_science_windows(self, x10a):
        n_chan, intents, names, with_data = x10a
        sel = select_science_spws(
            n_chan=n_chan,
            intents_per_spw=intents,
            spw_names=names,
            spws_with_data=with_data,
        )
        assert sel.science == [17, 19, 21, 23]
        assert sel.spw_string == "17,19,21,23"
        assert sel.method == "intent"

    def test_pointing_and_atmospheric_windows_are_excluded(self, x10a):
        """The defect this module exists to fix: 1-16 look like ordinary
        full-resolution science windows on every measure except intent."""
        n_chan, intents, names, with_data = x10a
        sel = select_science_spws(
            n_chan=n_chan, intents_per_spw=intents, spw_names=names, spws_with_data=with_data
        )
        assert set(range(1, 17)).isdisjoint(sel.science)
        # All 16 land in the non-science-intent bucket, not the no-intent one.
        assert sel.excluded_non_science_intent == list(range(1, 17))

    def test_water_vapour_windows_land_in_the_no_intent_bucket(self, x10a):
        n_chan, intents, names, with_data = x10a
        sel = select_science_spws(
            n_chan=n_chan, intents_per_spw=intents, spw_names=names, spws_with_data=with_data
        )
        assert sel.excluded_no_intent == [0] + list(range(25, 56))

    def test_channel_averaged_science_windows_are_dropped(self, x10a):
        """18/20/22/24 carry intents IDENTICAL to 17/19/21/23, so only the
        channel count separates them."""
        n_chan, intents, names, with_data = x10a
        sel = select_science_spws(
            n_chan=n_chan, intents_per_spw=intents, spw_names=names, spws_with_data=with_data
        )
        assert sel.excluded_single_channel == [18, 20, 22, 24]

    def test_cross_check_passes_on_the_reference_layout(self, x10a):
        n_chan, intents, names, with_data = x10a
        sel = select_science_spws(
            n_chan=n_chan, intents_per_spw=intents, spw_names=names, spws_with_data=with_data
        )
        assert sel.cross_check["wvr_name_agrees"] is True
        assert sel.cross_check["data_description_is_subset_of_no_intent"] is True
        assert sel.warnings == []


class TestWvrIntentTrap:
    def test_calibrate_wvr_alone_never_makes_a_window_science(self):
        """CALIBRATE_WVR is on every window of a real ALMA MS. Treating it as a
        science intent would select all 56."""
        sel_input = {
            "n_chan": {0: 128, 1: 128},
            "intents_per_spw": {
                0: ["CALIBRATE_WVR#ON_SOURCE"],
                1: ["OBSERVE_TARGET#ON_SOURCE", "CALIBRATE_WVR#ON_SOURCE"],
            },
        }
        sel = select_science_spws(**sel_input)
        assert sel.science == [1]
        assert sel.excluded_non_science_intent == [0]


class TestSingleChannelRule:
    def test_single_channel_dropped_even_with_a_science_intent(self):
        sel = select_science_spws(
            n_chan={0: 1, 1: 64},
            intents_per_spw={0: ["OBSERVE_TARGET"], 1: ["OBSERVE_TARGET"]},
        )
        assert sel.science == [1]
        assert sel.excluded_single_channel == [0]

    def test_all_single_channel_raises_rather_than_returning_empty(self):
        """An empty spw string means 'all windows' to CASA, so returning one
        would silently undo the filter."""
        with pytest.raises(NoScienceSpwError) as exc:
            select_science_spws(
                n_chan={0: 1, 1: 1},
                intents_per_spw={0: ["OBSERVE_TARGET"], 1: ["OBSERVE_TARGET"]},
            )
        assert "single-channel" in str(exc.value)

    def test_no_science_intent_anywhere_raises(self):
        with pytest.raises(NoScienceSpwError):
            select_science_spws(
                n_chan={0: 64, 1: 64},
                intents_per_spw={0: _POINTING, 1: _ATMOS},
            )


class TestIntentMatching:
    def test_bare_intent_without_on_source_suffix_matches(self):
        sel = select_science_spws(n_chan={0: 64}, intents_per_spw={0: ["CALIBRATE_BANDPASS"]})
        assert sel.science == [0]

    def test_matching_is_case_insensitive(self):
        sel = select_science_spws(n_chan={0: 64}, intents_per_spw={0: ["observe_target#on_source"]})
        assert sel.science == [0]


class TestStructuralFallback:
    def test_absent_intents_fall_back_and_warn_loudly(self):
        sel = select_science_spws(
            n_chan={0: 4, 1: 128, 2: 1},
            intents_per_spw={},
            spw_names={0: "WVR#NOMINAL", 1: "ALMA_RB_06#BB_1#SW-01#FULL_RES", 2: "x#CH_AVG"},
        )
        assert sel.method == "structural"
        assert sel.science == [1]
        assert any("fell back" in w for w in sel.warnings)
        # The fallback must admit what it cannot do.
        assert any("CANNOT distinguish pointing" in w for w in sel.warnings)

    def test_all_empty_intent_lists_count_as_absent(self):
        sel = select_science_spws(
            n_chan={0: 128},
            intents_per_spw={0: []},
            spw_names={0: "ALMA_RB_06#BB_1#SW-01#FULL_RES"},
        )
        assert sel.method == "structural"

    def test_wvr_match_is_anchored_at_the_start_of_the_name(self):
        """A science window whose name merely CONTAINS 'WVR' must survive."""
        sel = select_science_spws(
            n_chan={0: 128, 1: 128},
            intents_per_spw={},
            spw_names={0: "MY_WVR_TEST#FULL_RES", 1: "WVR#Antenna_0"},
        )
        assert sel.science == [0]
        assert sel.excluded_no_intent == [1]


class TestCrossCheckCanFail:
    def test_disagreement_between_name_and_intent_warns(self):
        """A water-vapour-named window that carries science intents means one of
        the two signals has changed meaning."""
        sel = select_science_spws(
            n_chan={0: 64, 1: 64},
            intents_per_spw={0: ["OBSERVE_TARGET"], 1: ["OBSERVE_TARGET"]},
            spw_names={0: "WVR#NOMINAL", 1: "ALMA_RB_06#BB_1#SW-01#FULL_RES"},
        )
        assert sel.cross_check["wvr_name_agrees"] is False
        assert any("Cross-check disagreement" in w for w in sel.warnings)

    def test_window_with_intents_but_no_correlation_data_warns(self):
        sel = select_science_spws(
            n_chan={0: 64, 1: 64},
            intents_per_spw={0: ["OBSERVE_TARGET"], 1: ["OBSERVE_TARGET"]},
            spw_names={0: "a#FULL_RES", 1: "b#FULL_RES"},
            spws_with_data={0},
        )
        assert sel.cross_check["data_description_is_subset_of_no_intent"] is False
        assert any("no DATA_DESCRIPTION row yet do carry intents" in w for w in sel.warnings)

    def test_intent_vs_channel_count_disagreement_is_not_warned_about(self):
        """They disagree by design on every ALMA dataset. Warning here would
        warn every time and train the reader to ignore warnings."""
        sel = select_science_spws(
            n_chan={17: 64, 18: 1},
            intents_per_spw={17: _SCIENCE, 18: _SCIENCE},
            spw_names={17: "a#FULL_RES", 18: "a#CH_AVG"},
        )
        assert sel.excluded_single_channel == [18]
        assert sel.warnings == []


class TestReport:
    def test_as_dict_reports_the_work_done_not_just_the_verdict(self, x10a):
        n_chan, intents, names, with_data = x10a
        d = select_science_spws(
            n_chan=n_chan, intents_per_spw=intents, spw_names=names, spws_with_data=with_data
        ).as_dict()
        assert d["n_science"] == 4
        assert d["method"] == "intent"
        assert len(d["excluded"]["no_intent"]) == 32
        assert len(d["excluded"]["non_science_intent_only"]) == 16
        assert len(d["excluded"]["single_channel"]) == 4
        # 4 + 32 + 16 + 4 == 56: every window is accounted for.
        assert (
            d["n_science"]
            + len(d["excluded"]["no_intent"])
            + len(d["excluded"]["non_science_intent_only"])
            + len(d["excluded"]["single_channel"])
            == 56
        )
