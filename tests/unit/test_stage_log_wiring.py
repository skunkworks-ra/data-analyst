"""
Unit tests that the writing tools actually emit the stage-log recorder into the
scripts they generate.

The point is not that the string appears — it is that the emitted script is
valid Python, defines the recorder, and calls it with the product the tool was
asked to write. ms_workflow_status derives the whole reduction state from those
lines, so a generator that silently stops emitting one makes the run look like
it never happened.

No CASA required: every tool here is exercised on its execute=False path.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest


def _make_ms(tmp_path) -> Path:
    ms = tmp_path / "test.ms"
    ms.mkdir()
    (ms / "table.info").write_text("Type = Measurement Set\n")
    return ms


def _make_workdir(tmp_path) -> Path:
    wd = tmp_path / "work"
    wd.mkdir()
    return wd


def _script_from(result) -> str:
    path = result["data"]["script_path"]
    path = path["value"] if isinstance(path, dict) else path
    return Path(path).read_text()


def _string_bindings(tree) -> dict:
    """Module-level `name = "literal"` assignments, so a call that passes a
    variable can still be resolved to the path the tool put there."""
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            for target in node.targets:
                if isinstance(target, ast.Name) and isinstance(node.value.value, str):
                    out[target.id] = node.value.value
    return out


def _record_calls(tree) -> list[ast.Call]:
    return [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_record_stage"
    ]


def _recorded(script: str) -> list[tuple[str, str]]:
    """(stage, product) for every _record_stage call, names resolved."""
    tree = ast.parse(script)
    names = _string_bindings(tree)
    out = []
    for call in _record_calls(tree):
        stage = ast.literal_eval(call.args[1])
        arg = call.args[2]
        if isinstance(arg, ast.Constant):
            product = arg.value
        elif isinstance(arg, ast.Name):
            product = names.get(arg.id, f"<unresolved {arg.id}>")
        else:
            product = "<unresolved>"
        out.append((stage, product))
    return out


def _module_level_index(script: str, func_name: str, product: str | None = None) -> int:
    """Position of a top-level call, ignoring anything inside a function body."""
    tree = ast.parse(script)
    names = _string_bindings(tree)
    for i, node in enumerate(tree.body):
        if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)):
            continue
        call = node.value
        if getattr(call.func, "id", "") != func_name:
            continue
        if product is None:
            return i
        arg = call.args[2]
        got = arg.value if isinstance(arg, ast.Constant) else names.get(arg.id)
        if got == product:
            return i
    raise AssertionError(f"no top-level {func_name} call for {product!r}")


@pytest.fixture
def ms_and_workdir(tmp_path):
    return _make_ms(tmp_path), _make_workdir(tmp_path)


def _patch_model_data_present(monkeypatch) -> None:
    """initial_rflag.run() now checks MODEL_DATA before writing a script (T8),
    which needs casatools.table.open() to succeed. _make_ms's fake MS has no
    real table, so this stands in for a real open, MODEL_DATA present."""
    from contextlib import contextmanager

    from ms_modify import initial_rflag

    @contextmanager
    def _fake_open_table(path, **kw):
        class _T:
            def colnames(self):
                return ["DATA", "CORRECTED_DATA", "MODEL_DATA"]

        yield _T()

    monkeypatch.setattr(initial_rflag, "open_table", _fake_open_table)


# ---------------------------------------------------------------------------


def test_gaincal_script_records_its_caltable(ms_and_workdir):
    from ms_modify.gaincal import run

    ms, wd = ms_and_workdir
    caltable = str(wd / "delay.K")
    script = _script_from(
        run(
            ms_path=str(ms),
            field="0",
            spw="",
            caltable=caltable,
            workdir=str(wd),
            gaintype="K",
            refant="ea01",
        )
    )
    assert _recorded(script) == [("gaincal", caltable)]


def test_bandpass_script_records_its_caltable(ms_and_workdir):
    from ms_modify.bandpass import run

    ms, wd = ms_and_workdir
    caltable = str(wd / "bandpass.b")
    script = _script_from(
        run(
            ms_path=str(ms),
            field="0",
            spw="",
            caltable=caltable,
            workdir=str(wd),
            refant="ea01",
        )
    )
    assert _recorded(script) == [("bandpass", caltable)]


def test_the_recorder_is_defined_in_the_script_it_is_called_from(ms_and_workdir):
    """A call to a function the script never defines is a NameError at run time."""
    from ms_modify.gaincal import run

    ms, wd = ms_and_workdir
    script = _script_from(
        run(
            ms_path=str(ms),
            field="0",
            spw="",
            caltable=str(wd / "gain.g"),
            workdir=str(wd),
            refant="ea01",
        )
    )
    tree = ast.parse(script)
    defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert "_record_stage" in defined


def test_the_record_call_follows_the_casa_task(ms_and_workdir):
    """Recording before the task would log a product that was never written."""
    from ms_modify.gaincal import run

    ms, wd = ms_and_workdir
    script = _script_from(
        run(
            ms_path=str(ms),
            field="0",
            spw="",
            caltable=str(wd / "gain.g"),
            workdir=str(wd),
            refant="ea01",
        )
    )
    # Ignore the definition inside the snippet; find the call at module level.
    body = ast.parse(script).body
    task_at = next(
        i
        for i, n in enumerate(body)
        if isinstance(n, ast.Expr)
        and isinstance(n.value, ast.Call)
        and getattr(n.value.func, "id", "") == "gaincal"
    )
    record_at = next(
        i
        for i, n in enumerate(body)
        if isinstance(n, ast.Expr)
        and isinstance(n.value, ast.Call)
        and getattr(n.value.func, "id", "") == "_record_stage"
    )
    assert record_at > task_at


def test_generated_script_records_into_the_workdir_it_was_given(ms_and_workdir):
    """The path in the call must be the workdir, not the caltable's parent."""
    from ms_modify.gaincal import run

    ms, wd = ms_and_workdir
    nested = wd / "tables"
    nested.mkdir()
    script = _script_from(
        run(
            ms_path=str(ms),
            field="0",
            spw="",
            caltable=str(nested / "gain.g"),
            workdir=str(wd),
            refant="ea01",
        )
    )
    call = re.search(r"_record_stage\((.+?), \"gaincal\"", script)
    assert ast.literal_eval(call.group(1)) == str(wd)


# ---------------------------------------------------------------------------
# Multi-product scripts — where the raise actually changes an outcome
# ---------------------------------------------------------------------------


def test_initial_bandpass_records_all_three_of_its_steps(ms_and_workdir):
    from ms_modify.initial_bandpass import run

    ms, wd = ms_and_workdir
    script = _script_from(
        run(
            ms_path=str(ms),
            bp_field="0",
            applycal_field="0",
            ref_ant="ea01",
            bp_scan="3",
            workdir=str(wd),
        )
    )
    assert _recorded(script) == [
        ("initial_bandpass", str(wd / "init_gain.g")),
        ("initial_bandpass", str(wd / "BP0.b")),
        ("initial_bandpass", str(ms)),
    ]


def test_initial_bandpass_records_the_gain_table_before_the_bandpass_solve(
    ms_and_workdir,
):
    """This ordering is the whole point: the bandpass solve uses init_gain.g.

    Recording it AFTER the bandpass call would let the script solve against a
    table the previous step failed to write, which is the failure the raise
    exists to prevent.
    """
    from ms_modify.initial_bandpass import run

    ms, wd = ms_and_workdir
    script = _script_from(
        run(
            ms_path=str(ms),
            bp_field="0",
            applycal_field="0",
            ref_ant="ea01",
            bp_scan="3",
            workdir=str(wd),
        )
    )
    assert _module_level_index(
        script, "_record_stage", str(wd / "init_gain.g")
    ) < _module_level_index(script, "bandpass")


def test_priorcals_records_only_what_it_actually_produced(ms_and_workdir):
    """A skipped prior is legitimate, so priorcals must not use the raising form.

    Pre-WIDAR data has no SYSPOWER subtable and an antpos table with no
    corrections is correctly empty. The script therefore records the tables it
    appended to `priorcals`, and never asserts a fixed set.
    """
    from ms_modify.priorcals import run

    ms, wd = ms_and_workdir
    script = _script_from(run(ms_path=str(ms), workdir=str(wd)))

    # No unconditional call: every recorded product comes from the loop, so
    # nothing is recorded for a prior that gencal skipped.
    tree = ast.parse(script)
    assert not [
        n
        for n in tree.body
        if isinstance(n, ast.Expr)
        and isinstance(n.value, ast.Call)
        and getattr(n.value.func, "id", "") == "_record_stage"
    ]
    loops = [
        n
        for n in ast.walk(ast.parse(script))
        if isinstance(n, ast.For) and getattr(n.iter, "id", "") == "priorcals"
    ]
    assert len(loops) == 1
    call = loops[0].body[0].value
    assert call.func.id == "_record_stage"
    assert ast.literal_eval(call.args[1]) == "priorcals"


def test_priorcals_script_defines_the_recorder(ms_and_workdir):
    from ms_modify.priorcals import run

    ms, wd = ms_and_workdir
    script = _script_from(run(ms_path=str(ms), workdir=str(wd)))
    defined = {n.name for n in ast.walk(ast.parse(script)) if isinstance(n, ast.FunctionDef)}
    assert "_record_stage" in defined


# ---------------------------------------------------------------------------
# Tools that change an MS in place — the line's content is the measurement
# ---------------------------------------------------------------------------
#
# For these the existence check is vacuous: the MS was there before the tool
# ran. So each script measures what the stage was supposed to change and logs
# that number. The tests below assert the measurement is taken AFTER the task
# and that the script stops when the measurement says the stage did nothing.


def _measurements(script: str) -> list[dict]:
    """The measurement dict literal passed to each _record_stage call."""
    out = []
    for call in _record_calls(ast.parse(script)):
        out.append(
            {
                k.value: (
                    v.id
                    if isinstance(v, ast.Name)
                    else v.value
                    if isinstance(v, ast.Constant)
                    else ast.unparse(v)
                )
                for k, v in zip(call.args[3].keys, call.args[3].values, strict=True)
            }
            if len(call.args) > 3
            else {}
        )
    return out


def _caltable(tmp_path) -> str:
    ct = tmp_path / "g.G"
    ct.mkdir()
    (ct / "table.info").write_text("Type = Calibration\n")
    return str(ct)


def test_applycal_measures_the_column_it_exists_to_populate(ms_and_workdir, tmp_path):
    """applycal returns None, so a clean return proves only that it did not raise."""
    from ms_modify.applycal import run

    ms, wd = ms_and_workdir
    script = _script_from(
        run(
            ms_path=str(ms),
            field="0",
            gaintable=[_caltable(wd)],
            gainfield=[""],
            interp=["linear"],
            workdir=str(wd),
        )
    )
    assert _measurements(script) == [{"field": "0", "corrected_data": "_corrected"}]
    assert _module_level_index(script, "applycal") < _module_level_index(script, "_record_stage")


def test_applycal_script_stops_when_corrected_data_is_absent(ms_and_workdir, tmp_path):
    """Recording the failure is not enough — the stage must not report success."""
    from ms_modify.applycal import run

    ms, wd = ms_and_workdir
    script = _script_from(
        run(
            ms_path=str(ms),
            field="0",
            gaintable=[_caltable(wd)],
            gainfield=[""],
            interp=["linear"],
            workdir=str(wd),
        )
    )
    # Module level only: the recorder's own raise lives inside its function.
    top = [
        n
        for n in ast.parse(script).body
        if isinstance(n, ast.If) and any(isinstance(b, ast.Raise) for b in n.body)
    ]
    assert len(top) == 1
    assert getattr(top[0].test, "op", None).__class__ is ast.Not


def test_setjy_measures_model_data(tmp_path):
    """_build_script directly: the run() path needs a real FIELD subtable."""
    from ms_modify.setjy import _build_script, _FieldPlan

    plans = [_FieldPlan(name="3C147", mode="standard", standard="Perley-Butler 2017")]
    script = _build_script("/data/x.ms", str(tmp_path), plans, True, [])
    keys = [set(m) for m in _measurements(script)]
    assert keys == [{"model_data", "usescratch"}]


def test_rflag_measures_the_flagged_fraction(ms_and_workdir):
    from ms_modify.rflag import run

    ms, wd = ms_and_workdir
    script = _script_from(
        run(ms_path=str(ms), field="0", spw="", workdir=str(wd), datacolumn="corrected")
    )
    assert _measurements(script) == [{"flagged_fraction": "_flagged"}]
    assert _module_level_index(script, "flagdata") < _module_level_index(script, "_record_stage")


def test_preflag_uses_the_raising_form_for_calibrators_ms_only(ms_and_workdir, tmp_path):
    """Two lines with different semantics in one script.

    The flag pass gets a measurement and no raise — flagging nothing is a
    legitimate outcome. calibrators.ms is a real new product that every later
    stage runs against, so its line raises if the split produced nothing.
    """
    from ms_modify.preflag import run

    ms, wd = ms_and_workdir
    online = tmp_path / "x.flagonline.txt"
    online.write_text("")
    script = _script_from(
        run(ms_path=str(ms), workdir=str(wd), cal_fields="0", online_flag_file=str(online))
    )
    calls = _record_calls(ast.parse(script))
    assert len(calls) == 2
    assert len(calls[0].args) == 4  # flag pass — carries a measurement
    assert len(calls[1].args) == 3  # calibrators.ms — existence, and it raises


def test_applycal_emitted_code_runs_records_and_raises(tmp_path):
    """End to end on the emitted source, not on its text.

    Executes the generated script with the casatasks import and the applycal
    call removed, so the recorder, the column probe and the raise all run for
    real against an MS that has no CORRECTED_DATA. This is the assertion that
    would catch a template that produces syntactically valid but broken code.
    """
    from ms_modify.applycal import _build_script

    ms = tmp_path / "t.ms"
    ms.mkdir()
    src = _build_script(
        ms_str=str(ms),
        workdir=str(tmp_path),
        field="0",
        gaintable=["/x"],
        gainfield=[""],
        interp=["linear"],
        calwt=False,
        applymode="calonly",
        parang=True,
        flagbackup=False,
    )
    tree = ast.parse(src)
    keep = [
        n
        for n in tree.body
        if not (isinstance(n, ast.ImportFrom) and n.module == "casatasks")
        and not (
            isinstance(n, ast.Expr)
            and getattr(getattr(n.value, "func", None), "id", "") == "applycal"
        )
    ]
    module = ast.fix_missing_locations(ast.Module(body=keep, type_ignores=[]))
    with pytest.raises(RuntimeError, match="CORRECTED_DATA is not present"):
        exec(compile(module, "<generated>", "exec"), {})

    from ms_inspect.util import stage_log

    (entry,) = stage_log.read_stage_log(tmp_path)
    assert entry["stage"] == "applycal"
    # The distinction the whole design rests on: the path check passes and says
    # nothing, the measurement is what reports the stage did not do its job.
    assert entry["exists"] is True
    assert entry["measurement"] == {"field": "0", "corrected_data": False}


# ---------------------------------------------------------------------------
# The remaining wired tools
# ---------------------------------------------------------------------------
#
# Every writing tool must emit a line: ms_workflow_status derives the whole
# reduction state from them, so a generator that quietly stops emitting one
# makes that stage look like it never ran. These are the five that had no
# wiring test when the feature landed.


def test_fluxscale_records_its_fluxtable(tmp_path):
    """_build_script directly: run() reads the input caltable's FIELD subtable."""
    from ms_modify.fluxscale import _build_script

    script = _build_script(
        ms_str="/data/x.ms",
        caltable="/w/gain.g",
        workdir=str(tmp_path),
        fluxtable="/w/flux.fluxscale",
        reference="3C147",
        transfer=["J0555+3948"],
        incremental=False,
    )
    assert _recorded(script) == [("fluxscale", "/w/flux.fluxscale")]


def test_polcal_records_its_caltable(ms_and_workdir):
    from ms_modify.polcal import run

    ms, wd = ms_and_workdir
    caltable = str(wd / "pol.D")
    script = _script_from(
        run(
            ms_path=str(ms),
            field="0",
            caltable=caltable,
            workdir=str(wd),
            poltype="Df",
            refant="ea01",
        )
    )
    assert _recorded(script) == [("polcal", caltable)]


def test_initial_rflag_measures_the_flagged_fraction(ms_and_workdir, monkeypatch):
    from ms_modify.initial_rflag import run

    _patch_model_data_present(monkeypatch)
    ms, wd = ms_and_workdir
    script = _script_from(run(ms_path=str(ms), workdir=str(wd), field="0"))
    assert _measurements(script) == [{"flagged_fraction": "_flagged"}]


def test_postcal_flag_measures_the_flagged_fraction(ms_and_workdir):
    from ms_modify.postcal_flag import run

    ms, wd = ms_and_workdir
    script = _script_from(run(ms_path=str(ms), workdir=str(wd), field="0"))
    assert _measurements(script) == [{"flagged_fraction": "_flagged"}]


def test_set_intents_records_the_state_row_count(tmp_path):
    """_build_set_intents_script directly: run() needs real FIELD/STATE tables."""
    from ms_modify.intents import _build_set_intents_script

    script = _build_set_intents_script(
        "/data/x.ms",
        [{"field_id": 0, "name": "3C147", "intents": ["CALIBRATE_BANDPASS#UNSPECIFIED"]}],
        {"CALIBRATE_BANDPASS#UNSPECIFIED": 0},
        str(tmp_path),
    )
    assert [set(m) for m in _measurements(script)] == [{"state_rows", "main_rows_updated"}]


def test_set_intents_stops_when_state_is_still_empty(tmp_path):
    """An empty STATE means the writes did not take, which is the one thing
    this stage exists to prevent downstream."""
    from ms_modify.intents import _build_set_intents_script

    script = _build_set_intents_script(
        "/data/x.ms",
        [{"field_id": 0, "name": "3C147", "intents": ["CALIBRATE_BANDPASS#UNSPECIFIED"]}],
        {"CALIBRATE_BANDPASS#UNSPECIFIED": 0},
        str(tmp_path),
    )
    top = [
        n
        for n in ast.parse(script).body
        if isinstance(n, ast.If) and any(isinstance(b, ast.Raise) for b in n.body)
    ]
    assert len(top) == 1


def test_every_wired_generator_emits_the_recorder(ms_and_workdir, tmp_path, monkeypatch):
    """One assertion over all of them, so a newly added generator that forgets
    the recorder is caught here rather than in a run three weeks later."""
    from ms_modify import (
        applycal,
        bandpass,
        gaincal,
        initial_bandpass,
        initial_rflag,
        polcal,
        postcal_flag,
        preflag,
        priorcals,
        rflag,
    )

    _patch_model_data_present(monkeypatch)
    ms, wd = ms_and_workdir
    ct = _caltable(wd)
    online = tmp_path / "x.flagonline.txt"
    online.write_text("")
    common = dict(ms_path=str(ms), workdir=str(wd))
    results = {
        "gaincal": lambda: gaincal.run(
            field="0", spw="", caltable=str(wd / "g1.G"), refant="ea01", **common
        ),
        "bandpass": lambda: bandpass.run(
            field="0", spw="", caltable=str(wd / "b1.B"), refant="ea01", **common
        ),
        "polcal": lambda: polcal.run(
            field="0", caltable=str(wd / "p1.D"), poltype="Df", refant="ea01", **common
        ),
        "initial_bandpass": lambda: initial_bandpass.run(
            bp_field="0", applycal_field="0", ref_ant="ea01", bp_scan="3", **common
        ),
        "priorcals": lambda: priorcals.run(**common),
        "preflag": lambda: preflag.run(cal_fields="0", online_flag_file=str(online), **common),
        "applycal": lambda: applycal.run(
            field="0", gaintable=[ct], gainfield=[""], interp=["linear"], **common
        ),
        "rflag": lambda: rflag.run(field="0", spw="", datacolumn="corrected", **common),
        "initial_rflag": lambda: initial_rflag.run(field="0", **common),
        "postcal_flag": lambda: postcal_flag.run(field="0", **common),
    }
    # fluxscale is covered by its own test above: run() needs a real caltable.
    missing = []
    for name, call in results.items():
        script = _script_from(call())
        defined = {n.name for n in ast.walk(ast.parse(script)) if isinstance(n, ast.FunctionDef)}
        if "_record_stage" not in defined or not _record_calls(ast.parse(script)):
            missing.append(name)
    assert missing == []
