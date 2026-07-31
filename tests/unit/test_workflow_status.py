"""
Unit tests for tools/workflow_status.py.

What these cover: the branch between "this stage has not happened yet" and
"the probe for this stage failed", using a fake MS directory tree and a
patched open_table. No CASA is involved.

What they do NOT cover: whether open_table actually raises on a real locked or
corrupt CASA table, and whether the filesystem heuristics for priorcals,
caltables and images match what the ms_modify tools really write. Both need a
real MS.
"""

from __future__ import annotations

import pytest

from ms_inspect.tools import workflow_status


@pytest.fixture
def fake_ms(tmp_path):
    """A directory that passes validate_ms_path, with no STATE subtable."""
    ms = tmp_path / "fake.ms"
    ms.mkdir()
    (ms / "table.info").write_text("Type = Measurement Set\n")
    workdir = tmp_path / "work"
    workdir.mkdir()
    return ms, workdir


def _run(ms, workdir):
    return workflow_status.run(str(ms), str(workdir))


class _Raiser:
    """Stand-in for open_table that fails the way a locked table would."""

    def __init__(self, exc):
        self._exc = exc

    def __call__(self, *_args, **_kwargs):
        raise self._exc


def test_absent_state_subtable_is_not_populated_not_unavailable(fake_ms, monkeypatch):
    ms, workdir = fake_ms
    # MAIN opens fine and has no CORRECTED_DATA.
    monkeypatch.setattr(workflow_status, "open_table", _fake_main_table(colnames=["DATA"]))

    result = _run(ms, workdir)
    intents = result["data"]["intents_populated"]

    assert intents["value"] is False
    assert intents["flag"] == "COMPLETE"
    assert result["data"]["next_recommended_step"] == "set_intents"


def test_unreadable_state_subtable_yields_unavailable_not_incomplete(fake_ms, monkeypatch):
    ms, workdir = fake_ms
    (ms / "STATE").mkdir()  # exists, so absence is not the explanation
    monkeypatch.setattr(
        workflow_status, "open_table", _Raiser(RuntimeError("table is locked by another process"))
    )

    result = _run(ms, workdir)
    intents = result["data"]["intents_populated"]

    assert intents["flag"] == "UNAVAILABLE"
    assert intents["value"] is None
    assert "locked" in intents["note"]
    # The critical assertion: a failed probe must not be reported as
    # "intents not set", which would recommend re-running set_intents.
    assert result["data"]["next_recommended_step"] == "probe_failed_intents"
    assert any("STATE subtable exists but could not be read" in w for w in result["warnings"])


def test_unreadable_main_table_yields_unavailable_corrected(fake_ms, monkeypatch):
    ms, workdir = fake_ms
    (ms / "STATE").mkdir()
    # Advance the workdir past every earlier stage, so the derivation actually
    # reaches the CORRECTED_DATA branch instead of stopping before it.
    cal_ms = workdir / "calibrators.ms"
    cal_ms.mkdir()
    (cal_ms / "table.info").write_text("Type = Measurement Set\n")
    for name in ("gain_curves.gc", "opacities.opac", "init_gain.g", "BP0.b"):
        (workdir / name).mkdir()

    def _open(path, *_args, **_kwargs):
        if path.endswith("/STATE"):
            return _table(nrows=3, colnames=[])
        raise RuntimeError("permission denied")

    monkeypatch.setattr(workflow_status, "open_table", _open)

    result = _run(ms, workdir)
    corrected = result["data"]["corrected_populated"]

    assert corrected["flag"] == "UNAVAILABLE"
    assert corrected["value"] is None
    assert "permission denied" in corrected["note"]
    assert result["data"]["next_recommended_step"] == "probe_failed_corrected"
    assert result["completeness_summary"] == "UNAVAILABLE"


def test_present_corrected_column_reads_true(fake_ms, monkeypatch):
    ms, workdir = fake_ms
    monkeypatch.setattr(
        workflow_status,
        "open_table",
        _fake_main_table(colnames=["DATA", "CORRECTED_DATA"]),
    )

    result = _run(ms, workdir)
    corrected = result["data"]["corrected_populated"]

    assert corrected["value"] is True
    assert corrected["flag"] == "COMPLETE"


# --- helpers -----------------------------------------------------------------


class _FakeTable:
    def __init__(self, nrows, colnames):
        self._nrows = nrows
        self._colnames = colnames

    def nrows(self):
        return self._nrows

    def colnames(self):
        return self._colnames

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _table(nrows=0, colnames=()):
    return _FakeTable(nrows, list(colnames))


def _fake_main_table(colnames):
    def _open(_path, *_args, **_kwargs):
        return _table(nrows=0, colnames=colnames)

    return _open
