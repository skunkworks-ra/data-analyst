"""
Unit tests for tools/workflow_status.py.

What these cover: the branch between "this stage has not happened yet" and
"the probe for this stage failed", and the split between the stage log (what
COMPLETED, as history) and the MS probes (what is TRUE NOW). Fake MS directory
trees and a patched open_table. No CASA is involved.

The old version of this docstring said the tests did not cover "whether the
filesystem heuristics for priorcals, caltables and images match what the
ms_modify tools really write". They did not match — the tool held a hardcoded
list of caltable NAMES while those paths are arguments the caller chooses —
and that was the defect. The heuristics are gone; state comes from
stage_log.jsonl, which the tools write themselves.

What these still do NOT cover: whether open_table raises on a real locked or
corrupt CASA table. That needs a real MS.
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
    # Advance past every earlier stage via the stage log, so the derivation
    # actually reaches the CORRECTED_DATA branch instead of stopping before it.
    _log(workdir, "set_intents", "preflag", "priorcals", "initial_bandpass")
    cal_ms = workdir / "calibrators.ms"
    cal_ms.mkdir()
    (cal_ms / "table.info").write_text("Type = Measurement Set\n")

    def _open(path, *_args, **_kwargs):
        if path.endswith("/STATE"):
            return _table(nrows=3, colnames=[])
        raise RuntimeError("permission denied")

    monkeypatch.setattr(workflow_status, "open_table", _open)

    result = _run(ms, workdir)
    corrected = result["data"]["corrected_populated_calibrators"]

    assert corrected["flag"] == "UNAVAILABLE"
    assert corrected["value"] is None
    assert "permission denied" in corrected["note"]
    assert result["data"]["next_recommended_step"] == "probe_failed_corrected_calibrators"
    assert result["completeness_summary"] == "UNAVAILABLE"


def test_present_corrected_column_reads_true(fake_ms, monkeypatch):
    ms, workdir = fake_ms
    monkeypatch.setattr(
        workflow_status,
        "open_table",
        _fake_main_table(colnames=["DATA", "CORRECTED_DATA"]),
    )

    result = _run(ms, workdir)
    corrected = result["data"]["corrected_populated_target"]

    assert corrected["value"] is True
    assert corrected["flag"] == "COMPLETE"


def _log(workdir, *stages, product="/w/thing"):
    """Append a completed line per stage, the way a generated script does."""
    import json

    from ms_inspect.util.stage_log import STAGE_LOG_NAME

    with open(workdir / STAGE_LOG_NAME, "a") as fh:
        for stage in stages:
            fh.write(
                json.dumps(
                    {"stage": stage, "product": product, "at": "2026-09-02T00:00:00Z",
                     "exists": True}
                )
                + "\n"
            )


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


# ---------------------------------------------------------------------------
# The two defects this tool was rewritten to fix (G55 run, 2026-08-31)
# ---------------------------------------------------------------------------


def test_caller_chosen_caltable_names_are_recognised(fake_ms, monkeypatch):
    """THE original defect.

    The old tool held ["delay.K", "bandpass.B", "gain.G", "gain.fluxscaled"]
    and the run wrote delay.K, bandpass.b, gain.g and flux.fluxscale — one of
    four matched. Those paths are ARGUMENTS with no default, so no fixed list
    can be right. The names below are exactly the ones the G55 run used.
    """
    ms, workdir = fake_ms
    (ms / "STATE").mkdir()
    monkeypatch.setattr(
        workflow_status, "open_table", _fake_main_table(colnames=["DATA", "CORRECTED_DATA"])
    )
    _log(workdir, "set_intents", "preflag", "priorcals", "initial_bandpass", "initial_rflag")
    for stage, product in (
        ("gaincal", "/w/delay.K"),
        ("gaincal", "/w/gain.g"),
        ("bandpass", "/w/bandpass.b"),
        ("fluxscale", "/w/flux.fluxscale"),
    ):
        _log(workdir, stage, product=product)

    result = _run(ms, workdir)
    assert result["data"]["final_solves_completed"] == ["gaincal", "bandpass", "fluxscale"]
    assert result["data"]["next_recommended_step"] != "delay_bandpass_gain"


def test_corrected_is_reported_for_both_measurement_sets(fake_ms, monkeypatch):
    """THE second defect.

    Calibration runs on calibrators.ms; the target applycal writes CORRECTED to
    the MS this tool is given. Probing only the latter is why the run reported
    corrected_populated=false for ten turns after applycal had populated
    CORRECTED on the calibrators.
    """
    ms, workdir = fake_ms
    cal_ms = workdir / "calibrators.ms"
    cal_ms.mkdir()
    (cal_ms / "table.info").write_text("Type = Measurement Set\n")

    def _open(path, *_args, **_kwargs):
        if path.endswith("/STATE"):
            return _table(nrows=3, colnames=[])
        if "calibrators.ms" in path:
            return _table(colnames=["DATA", "CORRECTED_DATA"])
        return _table(colnames=["DATA"])

    (ms / "STATE").mkdir()
    monkeypatch.setattr(workflow_status, "open_table", _open)
    _log(workdir, "set_intents", "preflag", "priorcals", "initial_bandpass", "initial_rflag")

    result = _run(ms, workdir)
    assert result["data"]["corrected_populated_calibrators"]["value"] is True
    assert result["data"]["corrected_populated_target"]["value"] is False


def test_the_stage_that_froze_the_g55_run_now_advances(fake_ms, monkeypatch):
    """Regression for the observed symptom, not just its cause.

    Turns 7 through 16 all reported apply_initial_rflag_then_applycal. With
    CORRECTED on the calibrators and the initial rflag recorded, the tool must
    move on.
    """
    ms, workdir = fake_ms
    cal_ms = workdir / "calibrators.ms"
    cal_ms.mkdir()
    (cal_ms / "table.info").write_text("Type = Measurement Set\n")
    (ms / "STATE").mkdir()

    def _open(path, *_args, **_kwargs):
        if path.endswith("/STATE"):
            return _table(nrows=3, colnames=[])
        if "calibrators.ms" in path:
            return _table(colnames=["DATA", "CORRECTED_DATA"])
        return _table(colnames=["DATA"])

    monkeypatch.setattr(workflow_status, "open_table", _open)
    _log(workdir, "set_intents", "preflag", "priorcals", "initial_bandpass", "initial_rflag")

    assert _run(ms, workdir)["data"]["next_recommended_step"] == "delay_bandpass_gain"


# ---------------------------------------------------------------------------
# Log vs. live state
# ---------------------------------------------------------------------------


def test_no_stage_log_reads_as_nothing_done_and_says_so(fake_ms, monkeypatch):
    """Deliberate, and not backward compatible: a workdir written before the
    stage log existed cannot be resumed. The warning is what makes that
    visible rather than silently wrong."""
    ms, workdir = fake_ms
    (ms / "STATE").mkdir()
    monkeypatch.setattr(workflow_status, "open_table", _fake_main_table(colnames=["DATA"]))

    result = _run(ms, workdir)
    assert result["data"]["stage_log_present"]["value"] is False
    assert result["data"]["stages_completed"] == []
    assert any("stage_log.jsonl" in w for w in result["warnings"])


def test_a_stage_recorded_only_as_failed_does_not_count(fake_ms, monkeypatch):
    """The failure line is the record OF the failure, not of the stage."""
    import json

    from ms_inspect.util.stage_log import STAGE_LOG_NAME

    ms, workdir = fake_ms
    (ms / "STATE").mkdir()
    monkeypatch.setattr(workflow_status, "open_table", _fake_main_table(colnames=["DATA"]))
    _log(workdir, "set_intents")
    (workdir / STAGE_LOG_NAME).open("a").write(
        json.dumps({"stage": "preflag", "product": "/w/calibrators.ms", "exists": False,
                    "error": "product not found"}) + "\n"
    )

    result = _run(ms, workdir)
    assert "preflag" not in result["data"]["stages_completed"]
    assert result["data"]["next_recommended_step"] == "apply_preflag"


def test_disagreement_between_log_and_ms_is_reported_not_resolved(fake_ms, monkeypatch):
    """A stage recorded complete whose product is gone from the MS is a real
    event. The tool says so instead of picking a winner."""
    ms, workdir = fake_ms
    (ms / "STATE").mkdir()
    monkeypatch.setattr(workflow_status, "open_table", _fake_main_table(colnames=["DATA"]))
    _log(workdir, "set_intents", "preflag", "priorcals", "initial_bandpass",
         "initial_rflag", "gaincal", "bandpass", "fluxscale", "applycal")

    result = _run(ms, workdir)
    assert any(
        "recorded complete in the stage log, but CORRECTED_DATA is not" in w
        for w in result["warnings"]
    )


def test_products_recorded_reports_the_paths_the_run_actually_used(fake_ms, monkeypatch):
    """The names are data now, so the tool can report them instead of assuming."""
    ms, workdir = fake_ms
    (ms / "STATE").mkdir()
    monkeypatch.setattr(workflow_status, "open_table", _fake_main_table(colnames=["DATA"]))
    _log(workdir, "gaincal", product="/w/delay.K")
    _log(workdir, "gaincal", product="/w/gain.g")

    assert _run(ms, workdir)["data"]["products_recorded"]["gaincal"] == [
        "/w/delay.K",
        "/w/gain.g",
    ]
