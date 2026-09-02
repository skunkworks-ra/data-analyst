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


def _recorded(script: str) -> list[tuple[str, str]]:
    """(stage, product) for every _record_stage call in a generated script."""
    tree = ast.parse(script)
    out = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_record_stage"
        ):
            stage, product = node.args[1], node.args[2]
            out.append((ast.literal_eval(stage), ast.literal_eval(product)))
    return out


@pytest.fixture
def ms_and_workdir(tmp_path):
    return _make_ms(tmp_path), _make_workdir(tmp_path)


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
