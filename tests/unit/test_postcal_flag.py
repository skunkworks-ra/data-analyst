"""Unit tests for ms_postcal_flag command-list construction (no CASA required)."""

from __future__ import annotations

from ms_modify.postcal_flag import _build_cmds_content, _parse_spw_ids


def test_cmds_order_and_membership():
    txt = _build_cmds_content(
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
    lines = txt.strip().splitlines()
    # clip must come first (before the autoflaggers see the data)
    assert lines[0].startswith("mode='clip'")
    assert "clipminmax=[0.0,100.0]" in lines[0]
    assert lines[1].startswith("mode='tfcrop'")
    assert lines[2].startswith("mode='rflag'")
    # the drop-tier manual flag comes last
    assert lines[-1].startswith("mode='manual'")
    assert "spw='3,8,9'" in lines[-1]
    # keep_spw scopes the autoflaggers, not the manual drop
    assert "spw='0,1,2'" in lines[1] and "spw='0,1,2'" in lines[2]


def test_per_spw_robust_clip_lines():
    txt = _build_cmds_content(
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
    lines = txt.strip().splitlines()
    # one clip line per SpW, sorted, with that SpW's own ceiling and the uvrange
    assert lines[0].startswith("mode='clip'") and "spw='0'" in lines[0] and "[0.0,1.2]" in lines[0]
    assert lines[1].startswith("mode='clip'") and "spw='4'" in lines[1] and "[0.0,2.5]" in lines[1]
    assert "uvrange='>2klambda'" in lines[0]


def test_parse_spw_ids():
    assert _parse_spw_ids("0,1,2,4,10") == [0, 1, 2, 4, 10]
    assert _parse_spw_ids("0:5~10,1") == [1]  # channel syntax skipped


def test_no_clip_no_drop_omits_those_lines():
    txt = _build_cmds_content(
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
    assert "mode='clip'" not in txt
    assert "mode='manual'" not in txt
    # no spw clause when keep_spw is empty
    assert "spw=" not in txt
    assert "mode='tfcrop'" in txt and "mode='rflag'" in txt
