"""Unit tests for ms_postcal_flag command-list construction (no CASA required)."""

from __future__ import annotations

from ms_modify.postcal_flag import _build_cmds_content


def test_cmds_order_and_membership():
    txt = _build_cmds_content(
        field="J1454,SN1006",
        keep_spw="0,1,2",
        drop_spw="3,8,9",
        datacolumn="corrected",
        clipmax=100.0,
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


def test_no_clip_no_drop_omits_those_lines():
    txt = _build_cmds_content(
        field="SN1006",
        keep_spw="",
        drop_spw="",
        datacolumn="corrected",
        clipmax=None,
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
