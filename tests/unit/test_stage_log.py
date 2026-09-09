"""
Unit tests for util/stage_log.py.

The snippet is tested by EXECUTING it, not by inspecting its text: it is
embedded verbatim into generated scripts, so what matters is that the emitted
source runs, appends the line, and raises when the product is absent. A test
that only asserted on the string would pass while the emitted code was broken.
"""

from __future__ import annotations

import json

import pytest

from ms_inspect.util import stage_log


def _load_recorder():
    """exec the emitted snippet and hand back the function it defines."""
    ns: dict = {}
    exec(stage_log.RECORD_STAGE_SNIPPET, ns)
    return ns["_record_stage"]


# ---------------------------------------------------------------- the snippet


def test_snippet_appends_a_line_for_a_product_that_exists(tmp_path):
    product = tmp_path / "gain.g"
    product.mkdir()
    _load_recorder()(str(tmp_path), "gaincal", str(product))

    entries = stage_log.read_stage_log(tmp_path)
    assert len(entries) == 1
    assert entries[0]["stage"] == "gaincal"
    assert entries[0]["product"] == str(product)
    assert entries[0]["exists"] is True
    assert "error" not in entries[0]
    assert entries[0]["at"].endswith("Z")


def test_snippet_raises_and_still_records_when_the_product_is_absent(tmp_path):
    """The failure line must survive the raise — it is the record of the failure."""
    missing = tmp_path / "never_written.g"
    with pytest.raises(RuntimeError, match="does not exist"):
        _load_recorder()(str(tmp_path), "gaincal", str(missing))

    entries = stage_log.read_stage_log(tmp_path)
    assert len(entries) == 1
    assert entries[0]["exists"] is False
    assert entries[0]["error"]


def test_snippet_appends_rather_than_overwrites(tmp_path):
    """A retry must not destroy the previous attempt's record."""
    record = _load_recorder()
    for name in ("init_gain.g", "BP0.b", "init_gain.g"):
        (tmp_path / name).mkdir(exist_ok=True)
        record(str(tmp_path), "initial_bandpass", str(tmp_path / name))

    entries = stage_log.read_stage_log(tmp_path)
    assert [e["product"] for e in entries] == [
        str(tmp_path / "init_gain.g"),
        str(tmp_path / "BP0.b"),
        str(tmp_path / "init_gain.g"),
    ]


def test_snippet_is_self_contained(tmp_path):
    """It is exec'd inside a generated script whose imports we do not control."""
    product = tmp_path / "delay.K"
    product.mkdir()
    ns: dict = {}
    exec(stage_log.RECORD_STAGE_SNIPPET, ns)
    ns["_record_stage"](str(tmp_path), "gaincal", str(product))
    assert stage_log.read_stage_log(tmp_path)[0]["exists"] is True


# ---------------------------------------------------------------- the reader


def test_read_stage_log_absent_is_empty(tmp_path):
    assert stage_log.read_stage_log(tmp_path) == []


def test_read_stage_log_skips_a_truncated_final_line(tmp_path):
    """A job killed mid-write leaves a partial line; that is expected, not corrupt."""
    path = tmp_path / stage_log.STAGE_LOG_NAME
    path.write_text(
        json.dumps({"stage": "priorcals", "product": "/w/opacities.opac", "exists": True})
        + "\n"
        + '{"stage": "gainca'
    )
    entries = stage_log.read_stage_log(tmp_path)
    assert len(entries) == 1
    assert entries[0]["stage"] == "priorcals"


def test_read_stage_log_skips_a_non_object_line(tmp_path):
    path = tmp_path / stage_log.STAGE_LOG_NAME
    path.write_text('["not", "an", "object"]\n' + json.dumps({"stage": "preflag", "exists": True}))
    assert [e["stage"] for e in stage_log.read_stage_log(tmp_path)] == ["preflag"]


# ---------------------------------------------------------------- derivations


def test_completed_stages_excludes_a_stage_that_only_failed(tmp_path):
    entries = [
        {"stage": "priorcals", "product": "/w/opacities.opac", "exists": True},
        {"stage": "gaincal", "product": "/w/gain.g", "exists": False, "error": "x"},
    ]
    assert stage_log.completed_stages(entries) == {"priorcals"}


def test_completed_stages_counts_a_stage_that_failed_then_succeeded():
    entries = [
        {"stage": "gaincal", "product": "/w/gain.g", "exists": False, "error": "x"},
        {"stage": "gaincal", "product": "/w/gain.g", "exists": True},
    ]
    assert stage_log.completed_stages(entries) == {"gaincal"}


def test_products_for_deduplicates_a_retry():
    entries = [
        {"stage": "initial_bandpass", "product": "/w/init_gain.g", "exists": True},
        {"stage": "initial_bandpass", "product": "/w/BP0.b", "exists": True},
        {"stage": "initial_bandpass", "product": "/w/init_gain.g", "exists": True},
        {"stage": "gaincal", "product": "/w/gain.g", "exists": True},
    ]
    assert stage_log.products_for(entries, "initial_bandpass") == [
        "/w/init_gain.g",
        "/w/BP0.b",
    ]


def test_products_for_omits_a_product_recorded_absent():
    entries = [{"stage": "gaincal", "product": "/w/gain.g", "exists": False, "error": "x"}]
    assert stage_log.products_for(entries, "gaincal") == []


# ---------------------------------------------------------------- measurements
#
# The measurement is the only real content of a line for a tool that changes an
# MS in place: the existence check passes before that tool ever runs. These
# assert the two halves stay independent — a recorded measurement of "the stage
# changed nothing" must not be confused with "the product is missing", and vice
# versa.


def test_measurement_is_recorded_alongside_the_existence_check(tmp_path):
    product = tmp_path / "t.ms"
    product.mkdir()
    _load_recorder()(str(tmp_path), "applycal", str(product), {"corrected_data": True})

    (entry,) = stage_log.read_stage_log(tmp_path)
    assert entry["exists"] is True
    assert entry["measurement"] == {"corrected_data": True}


def test_a_failing_measurement_does_not_make_the_recorder_raise(tmp_path):
    """Whether a measurement means failure is the caller's decision, not the
    recorder's: flagging nothing is legitimate, an absent CORRECTED_DATA is not.
    The scripts that must stop raise on their own after recording."""
    product = tmp_path / "t.ms"
    product.mkdir()
    _load_recorder()(str(tmp_path), "applycal", str(product), {"corrected_data": False})

    (entry,) = stage_log.read_stage_log(tmp_path)
    assert entry["exists"] is True
    assert entry["measurement"] == {"corrected_data": False}


def test_a_missing_product_still_raises_when_a_measurement_is_passed(tmp_path):
    with pytest.raises(RuntimeError, match="does not exist"):
        _load_recorder()(
            str(tmp_path), "preflag", str(tmp_path / "gone.ms"), {"flagged_fraction": 0.1}
        )
    (entry,) = stage_log.read_stage_log(tmp_path)
    assert entry["exists"] is False
    assert entry["measurement"] == {"flagged_fraction": 0.1}


def test_no_measurement_key_when_none_is_passed(tmp_path):
    """Absent, not null: a null would read as 'measured, and it was nothing'."""
    product = tmp_path / "gain.g"
    product.mkdir()
    _load_recorder()(str(tmp_path), "gaincal", str(product))
    assert "measurement" not in stage_log.read_stage_log(tmp_path)[0]


def test_a_none_valued_measurement_survives_the_round_trip(tmp_path):
    """flagged_fraction is None when the summary reports zero total rows. That
    is 'could not measure', and it must not silently become 0.0."""
    product = tmp_path / "t.ms"
    product.mkdir()
    _load_recorder()(str(tmp_path), "rflag", str(product), {"flagged_fraction": None})
    assert stage_log.read_stage_log(tmp_path)[0]["measurement"] == {"flagged_fraction": None}


def test_completed_stages_ignores_the_measurement(tmp_path):
    """A stage that ran and changed nothing still ran."""
    entries = [
        {
            "stage": "rflag",
            "product": "/w/t.ms",
            "exists": True,
            "measurement": {"flagged_fraction": 0.0},
        }
    ]
    assert stage_log.completed_stages(entries) == {"rflag"}


def test_the_in_process_recorder_is_the_same_function_as_the_pasted_one(tmp_path):
    """stage_log.record_stage is defined by executing RECORD_STAGE_SNIPPET, so
    the two callers cannot drift. If someone re-implements it by hand, this
    fails."""
    product = tmp_path / "gain.g"
    product.mkdir()
    stage_log.record_stage(str(tmp_path), "gaincal", str(product), {"n": 1})

    other = tmp_path / "other"
    other.mkdir()
    (other / "gain.g").mkdir()
    _load_recorder()(str(other), "gaincal", str(other / "gain.g"), {"n": 1})

    mine = stage_log.read_stage_log(tmp_path)[0]
    theirs = stage_log.read_stage_log(other)[0]
    assert set(mine) == set(theirs)
    assert mine["measurement"] == theirs["measurement"]
