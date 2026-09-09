"""
science_spw.py — which spectral windows hold science data.

An ALMA MS carries far more spectral windows than the ones you calibrate and
image. A real Band 6 dataset (uid___A002_X85c183_X10a, 56 windows) breaks down:

    17-24   OBSERVE_TARGET + CALIBRATE_BANDPASS/PHASE/AMPLI/FLUX/POLARIZATION
    1-8     CALIBRATE_POINTING only
    9-16    CALIBRATE_ATMOSPHERE, CALIBRATE_SIDEBAND_RATIO
    0,25-55 no intents at all — the water-vapour radiometer

Only 17-24 hold science, and only the four with more than one channel
(17, 19, 21, 23) are usable. Without this filter, tools offered the pointing and
atmospheric windows for bandpass solving and inferred the observing band from a
183 GHz water-vapour window (reporting Band 5 for a Band 6 dataset).

Two facts that shape the rule, both measured rather than assumed:

- **CALIBRATE_WVR is attached to every window, science ones included.** It cannot
  identify water-vapour windows; only the *absence* of intents does. A rule that
  excluded "windows whose intent says WVR" would exclude everything.
- **A channel-averaged window carries intents identical to its full-resolution
  partner.** Intent describes purpose, never resolution. So intent alone can
  never separate them, and the channel count is a genuine second filter rather
  than a cross-check on the first.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dc_field

# Intents that mean "this window holds data you calibrate or image".
#
# Matched against the intent prefix before '#', so both
# CALIBRATE_BANDPASS#ON_SOURCE and a bare CALIBRATE_BANDPASS match.
#
# CALIBRATE_WVR is deliberately absent: it is attached to every window on real
# ALMA data, so including it would make every window science.
SCIENCE_INTENTS: frozenset[str] = frozenset(
    {
        "OBSERVE_TARGET",
        "CALIBRATE_BANDPASS",
        "CALIBRATE_PHASE",
        "CALIBRATE_AMPLI",
        "CALIBRATE_AMPLITUDE",
        "CALIBRATE_FLUX",
        "CALIBRATE_POLARIZATION",
        "CALIBRATE_POL_ANGLE",
        "CALIBRATE_POL_LEAKAGE",
        "CALIBRATE_DELAY",
    }
)

# Intents that alone never make a window science.
NON_SCIENCE_INTENTS: frozenset[str] = frozenset(
    {
        "CALIBRATE_POINTING",
        "CALIBRATE_ATMOSPHERE",
        "CALIBRATE_SIDEBAND_RATIO",
        "CALIBRATE_WVR",
        "CALIBRATE_FOCUS",
        "CALIBRATE_SIDEBAND",
    }
)

_WVR_NAME_PREFIX = "WVR"


class NoScienceSpwError(Exception):
    """
    The filter selected no window.

    Raised rather than returning an empty selection: an empty spw string is
    accepted by CASA as "all windows", so returning one would silently undo the
    filter and hand every water-vapour window to the caller.
    """


@dataclass
class ScienceSpwSelection:
    """Which windows are science, and the evidence for the decision."""

    science: list[int]
    method: str  # 'intent' or 'structural'
    excluded_no_intent: list[int] = dc_field(default_factory=list)
    excluded_non_science_intent: list[int] = dc_field(default_factory=list)
    excluded_single_channel: list[int] = dc_field(default_factory=list)
    cross_check: dict = dc_field(default_factory=dict)
    warnings: list[str] = dc_field(default_factory=list)

    @property
    def spw_string(self) -> str:
        """CASA spw selection string for the science windows."""
        return ",".join(str(s) for s in self.science)

    def as_dict(self) -> dict:
        """Report the work done, not just the verdict."""
        return {
            "science_spws": self.science,
            "n_science": len(self.science),
            "spw_string": self.spw_string,
            "method": self.method,
            "excluded": {
                "no_intent": self.excluded_no_intent,
                "non_science_intent_only": self.excluded_non_science_intent,
                "single_channel": self.excluded_single_channel,
            },
            "cross_check": self.cross_check,
        }


def _intent_prefix(intent: str) -> str:
    return intent.split("#", 1)[0].strip().upper()


def _has_science_intent(intents: list[str]) -> bool:
    return any(_intent_prefix(i) in SCIENCE_INTENTS for i in intents)


def select_science_spws(
    *,
    n_chan: dict[int, int],
    intents_per_spw: dict[int, list[str]] | None = None,
    spw_names: dict[int, str] | None = None,
    spws_with_data: set[int] | None = None,
) -> ScienceSpwSelection:
    """
    Select the science windows.

    Args:
        n_chan:          spw id -> channel count. Required; the single-channel
                         filter is not optional.
        intents_per_spw: spw id -> intents. Pass None (or all-empty) only when
                         the MS has no STATE table; the structural fallback is
                         then used and reported.
        spw_names:       spw id -> NAME. Used for the cross-check, and for the
                         structural fallback. Optional.
        spws_with_data:  spw ids having a DATA_DESCRIPTION row. Cross-check only.

    Returns:
        ScienceSpwSelection.

    Raises:
        NoScienceSpwError: nothing survived the filter.
    """
    all_spws = sorted(n_chan)
    names = {k: (v or "") for k, v in (spw_names or {}).items()}

    have_intents = bool(intents_per_spw) and any(v for v in (intents_per_spw or {}).values())

    sel = ScienceSpwSelection(science=[], method="intent" if have_intents else "structural")

    if have_intents:
        intents_per_spw = intents_per_spw or {}
        for spw in all_spws:
            got = list(intents_per_spw.get(spw) or [])
            if not got:
                sel.excluded_no_intent.append(spw)
            elif not _has_science_intent(got):
                sel.excluded_non_science_intent.append(spw)
            elif n_chan.get(spw, 0) <= 1:
                # Expected on every ALMA dataset: a channel-averaged window
                # inherits its partner's intents. Not an anomaly.
                sel.excluded_single_channel.append(spw)
            else:
                sel.science.append(spw)
    else:
        # No intents to reason from. Fall back to structure, and say so — a
        # silent fallback here is the same class of defect the filter fixes.
        sel.warnings.append(
            "No spectral-window intents available, so the science selection fell "
            "back to a structural rule (channel count and window name). This rule "
            "CANNOT distinguish pointing or atmospheric windows from science "
            "windows — on real ALMA data it keeps both. Verify the selection, or "
            "populate intents (ms_set_intents) and re-run."
        )
        for spw in all_spws:
            if names.get(spw, "").upper().startswith(_WVR_NAME_PREFIX):
                sel.excluded_no_intent.append(spw)
            elif n_chan.get(spw, 0) <= 1:
                sel.excluded_single_channel.append(spw)
            else:
                sel.science.append(spw)

    _add_cross_check(sel, names=names, spws_with_data=spws_with_data, all_spws=all_spws)

    if not sel.science:
        raise NoScienceSpwError(
            f"No science spectral window found among {len(all_spws)} windows "
            f"(method={sel.method}). Excluded: "
            f"{len(sel.excluded_no_intent)} with no intent, "
            f"{len(sel.excluded_non_science_intent)} with only non-science intents, "
            f"{len(sel.excluded_single_channel)} single-channel. An empty spw "
            f"selection means 'all windows' to CASA, so it is not returned."
        )
    return sel


def _add_cross_check(
    sel: ScienceSpwSelection,
    *,
    names: dict[int, str],
    spws_with_data: set[int] | None,
    all_spws: list[int],
) -> None:
    """
    Compare the no-intent set against two independent signals.

    This check CAN fail, which is the point. On the reference dataset the
    no-intent set and the WVR-named set agree exactly ({0, 25-55}), while the
    DATA_DESCRIPTION test misses window 0 (WVR#NOMINAL does have a row). A
    future dataset that breaks that agreement produces a warning instead of a
    silently wrong window list.

    Deliberately NOT compared: intent against channel count. Those disagree by
    design on every ALMA dataset (a channel-averaged window has science
    intents), so alarming on it would alarm every time.
    """
    no_intent = set(sel.excluded_no_intent)
    named_wvr = {s for s in all_spws if names.get(s, "").upper().startswith(_WVR_NAME_PREFIX)}

    sel.cross_check["wvr_named"] = sorted(named_wvr)
    sel.cross_check["wvr_name_agrees"] = names != {} and named_wvr == no_intent

    if names and named_wvr != no_intent:
        only_named = sorted(named_wvr - no_intent)
        only_no_intent = sorted(no_intent - named_wvr)
        sel.warnings.append(
            "Cross-check disagreement: the windows with no intents and the windows "
            f"named for the water-vapour radiometer are not the same set. "
            f"Named-only: {only_named}. No-intent-only: {only_no_intent}. "
            "One of the two signals no longer means what it did on the reference "
            "dataset — inspect before trusting the selection."
        )

    if spws_with_data is not None:
        absent = {s for s in all_spws if s not in spws_with_data}
        sel.cross_check["absent_from_data_description"] = sorted(absent)
        # Known and expected: WVR#NOMINAL has a DATA_DESCRIPTION row, so this
        # signal is a subset of the no-intent set rather than equal to it.
        sel.cross_check["data_description_is_subset_of_no_intent"] = absent <= no_intent
        if not absent <= no_intent:
            sel.warnings.append(
                "Cross-check disagreement: "
                f"windows {sorted(absent - no_intent)} have no DATA_DESCRIPTION "
                "row yet do carry intents. A window with intents but no "
                "correlation data is unexpected — inspect before trusting the "
                "selection."
            )
