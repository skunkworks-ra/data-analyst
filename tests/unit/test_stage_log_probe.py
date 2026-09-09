"""
Unit tests for TABLE_PROBE_SNIPPET — the column/row probe pasted into the
scripts that must measure what they changed.

It had no coverage at all when it landed. That matters more here than for most
code: it is pasted into three generated scripts, its failure mode is returning
an empty list rather than raising, and an empty list is exactly what a
"the stage did nothing" measurement looks like. A silently broken probe would
make every applycal and setjy run look like a failure, or — if the callers ever
inverted a check — like a success.

These build a REAL CASA table rather than a fake directory, because the probe's
whole job is to open one.
"""

from __future__ import annotations

import pytest

from ms_inspect.util import stage_log


@pytest.fixture
def probe():
    """exec the pasted source and hand back the two functions it defines."""
    ns: dict = {}
    exec(stage_log.TABLE_PROBE_SNIPPET, ns)
    return ns["_table_colnames"], ns["_table_rows"]


@pytest.fixture
def casa_table(tmp_path):
    """A real CASA table with two columns and three rows."""
    from casatools import table

    path = tmp_path / "probe.tab"
    tb = table()
    desc = {
        name: {
            "valueType": "double",
            "dataManagerType": "StandardStMan",
            "comment": "",
        }
        for name in ("DATA", "CORRECTED_DATA")
    }
    assert tb.create(str(path), desc)
    tb.addrows(3)
    tb.close()
    return path


def test_colnames_reads_a_real_table(probe, casa_table):
    colnames, _ = probe
    assert sorted(colnames(str(casa_table))) == ["CORRECTED_DATA", "DATA"]


def test_rows_reads_a_real_table(probe, casa_table):
    _, rows = probe
    assert rows(str(casa_table)) == 3


def test_colnames_of_a_missing_table_is_empty_not_an_exception(probe, tmp_path):
    """The probe runs after the CASA task, inside the script. If it raised, the
    stage-log line would never be written and the failure would lose its
    record — the opposite of what the log exists for."""
    colnames, _ = probe
    assert colnames(str(tmp_path / "does_not_exist")) == []


def test_rows_of_a_missing_table_is_zero(probe, tmp_path):
    _, rows = probe
    assert rows(str(tmp_path / "does_not_exist")) == 0


def test_colnames_of_a_directory_that_is_not_a_table_is_empty(probe, tmp_path):
    """A half-written product from a killed job is a directory, not a table."""
    junk = tmp_path / "partial.ms"
    junk.mkdir()
    (junk / "table.info").write_text("Type = Measurement Set\n")
    colnames, _ = probe
    assert colnames(str(junk)) == []


def test_the_probe_is_self_contained(casa_table):
    """It is pasted into scripts whose imports we do not control."""
    ns: dict = {}
    exec(stage_log.TABLE_PROBE_SNIPPET, ns)
    assert ns["_table_rows"](str(casa_table)) == 3


def test_repeated_probes_do_not_leave_the_table_locked(probe, casa_table):
    """The probe closes in a finally block. A leaked lock would block the next
    CASA task in the same script."""
    colnames, rows = probe
    for _ in range(5):
        assert colnames(str(casa_table))
        assert rows(str(casa_table)) == 3

    from casatools import table

    tb = table()
    assert tb.open(str(casa_table), nomodify=False) is not False
    tb.close()
