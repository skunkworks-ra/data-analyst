"""Unit tests for ms_postcal_flag flag-call construction (no CASA required)."""

from __future__ import annotations

from ms_modify.postcal_flag import _build_flag_calls, _parse_spw_ids


def test_call_order_and_membership():
    calls = _build_flag_calls(
        field="J1454,SN1006",
        keep_spw="0,1,2",
        drop_spw="3,8,9",
        datacolumn="corrected",
        clipmax=100.0,
        clip_thresholds=None,
        uvrange="",
        timedevscale=5.0,
        freqdevscale=5.0,
        timecutoff=4.0,
        freqcutoff=4.0,
    )
    # clip must come first (before the autoflaggers see the data)
    assert calls[0]["mode"] == "clip"
    assert calls[0]["clipminmax"] == [0.0, 100.0]
    assert calls[1]["mode"] == "tfcrop"
    assert calls[2]["mode"] == "rflag"
    # the drop-tier manual flag comes last
    assert calls[-1]["mode"] == "manual"
    assert calls[-1]["spw"] == "3,8,9"
    # keep_spw scopes the autoflaggers, not the manual drop
    assert calls[1]["spw"] == "0,1,2" and calls[2]["spw"] == "0,1,2"


def test_per_spw_robust_clip_calls():
    calls = _build_flag_calls(
        field="SN1006",
        keep_spw="0,4",
        drop_spw="",
        datacolumn="corrected",
        clipmax=None,
        clip_thresholds={4: 2.5, 0: 1.2},
        uvrange=">2klambda",
        timedevscale=5.0,
        freqdevscale=5.0,
        timecutoff=4.0,
        freqcutoff=4.0,
    )
    # one clip call per SpW, sorted, with that SpW's own ceiling and the uvrange
    assert calls[0]["mode"] == "clip" and calls[0]["spw"] == "0" and calls[0]["clipminmax"] == [0.0, 1.2]
    assert calls[1]["mode"] == "clip" and calls[1]["spw"] == "4" and calls[1]["clipminmax"] == [0.0, 2.5]
    assert calls[0]["uvrange"] == ">2klambda"


def test_parse_spw_ids():
    assert _parse_spw_ids("0,1,2,4,10") == [0, 1, 2, 4, 10]
    assert _parse_spw_ids("0:5~10,1") == [1]  # channel syntax skipped
    # inclusive ranges are expanded, mixable with plain ids, sorted + de-duped
    assert _parse_spw_ids("0~7") == [0, 1, 2, 3, 4, 5, 6, 7]
    assert _parse_spw_ids("0~7,9~15") == [0, 1, 2, 3, 4, 5, 6, 7, 9, 10, 11, 12, 13, 14, 15]
    assert _parse_spw_ids("16,20~28") == [16, 20, 21, 22, 23, 24, 25, 26, 27, 28]
    assert _parse_spw_ids("9~15,8") == [8, 9, 10, 11, 12, 13, 14, 15]
    assert _parse_spw_ids("") == []


def test_no_clip_no_drop_omits_those_calls():
    calls = _build_flag_calls(
        field="SN1006",
        keep_spw="",
        drop_spw="",
        datacolumn="corrected",
        clipmax=None,
        clip_thresholds=None,
        uvrange="",
        timedevscale=5.0,
        freqdevscale=5.0,
        timecutoff=4.0,
        freqcutoff=4.0,
    )
    modes = [c["mode"] for c in calls]
    assert "clip" not in modes
    assert "manual" not in modes
    # no spw clause when keep_spw is empty
    assert all("spw" not in c for c in calls)
    assert modes == ["tfcrop", "rflag"]


def test_generated_script_never_uses_list_mode():
    from ms_modify.postcal_flag import _build_script

    calls = _build_flag_calls(
        field="SN1006",
        keep_spw="0,1",
        drop_spw="3",
        datacolumn="corrected",
        clipmax=100.0,
        clip_thresholds=None,
        uvrange="",
        timedevscale=5.0,
        freqdevscale=5.0,
        timecutoff=4.0,
        freqcutoff=4.0,
    )
    script = _build_script("/data/x.ms", calls)
    # inpfile is the only list-mode marker; its absence proves the switch.
    assert "inpfile" not in script
    assert "flagmanager" in script and "before_postcal_flag" in script
    assert "action='apply'" in script
    assert "flagbackup=False" in script and "flagbackup=True" not in script
