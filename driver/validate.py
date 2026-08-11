"""
validate.py — refuse a bad decision before it costs eight hours of compute.

Four things get checked, cheapest first:

  1. shape       — the action is one of four words, the fields are present
  2. tool        — the tool is whitelisted, and the parameters match the REAL
                   run() signature, read with inspect.signature
  3. precondition— the inputs the tool needs are on disk
  4. evidence    — every number the model cited exists in the file it cited

Check 2 reads the signature from the code rather than from whitelist.yaml, so
the whitelist can never drift out of step with the tools.

Check 4 is the cheap lie detector. A rationale in prose cannot be verified; a
number with a source can. That is the reason evidence is a list of numbers and
not a sentence.
"""

from __future__ import annotations

import contextlib
import importlib
import inspect
import json
from pathlib import Path
from typing import Any

ACTIONS = {"run", "redo", "done", "ask"}
NEEDS_TOOL = {"run", "redo"}

# A cited number may be rounded. It may not be invented.
EVIDENCE_REL_TOL = 0.02


class Refusal(Exception):
    """The decision cannot be acted on. The reason goes back to the model."""


def _fail(problems: list[str]) -> None:
    if problems:
        raise Refusal("\n".join(f"- {p}" for p in problems))


# -- 1. shape -----------------------------------------------------------


def load_decision(path: Path) -> dict[str, Any]:
    try:
        obj = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise Refusal(f"- {path.name} is not valid JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise Refusal(f"- {path.name} must contain a JSON object, not a {type(obj).__name__}")
    return obj


def check_shape(d: dict[str, Any]) -> None:
    problems: list[str] = []
    action = d.get("action")
    if action not in ACTIONS:
        problems.append(f"action {action!r} is not one of {sorted(ACTIONS)}")
    if not str(d.get("rationale", "")).strip():
        problems.append("rationale is empty — say why in at most three sentences")
    if action in NEEDS_TOOL:
        if not d.get("tool"):
            problems.append(f"action {action!r} needs a tool")
        if not isinstance(d.get("params", {}), dict):
            problems.append("params must be an object")
    _fail(problems)


# -- 2. tool and parameters --------------------------------------------


def check_tool(d: dict[str, Any], whitelist: dict[str, Any]) -> None:
    if d["action"] not in NEEDS_TOOL:
        return
    tool = d["tool"]
    tools = whitelist["tools"]
    if tool not in tools:
        raise Refusal(
            f"- {tool!r} is not on the whitelist. Choose one of: {', '.join(sorted(tools))}"
        )

    params = d.get("params", {}) or {}
    problems: list[str] = []

    owned = set(whitelist.get("driver_owned", []))
    for k in sorted(owned & set(params)):
        problems.append(f"params.{k} is set by the driver — remove it")

    module_name = tools[tool]["module"]
    try:
        run_fn = importlib.import_module(module_name).run
    except (ImportError, AttributeError) as exc:
        # Do not refuse on an environment problem. That would blame the model
        # for a missing package, and it would make the run unrecoverable.
        problems.append(f"NOTE: cannot import {module_name} to check parameters ({exc})")
        _fail(problems)
        return

    sig = inspect.signature(run_fn)
    accepted = set(sig.parameters)
    # Advertise only what the model may actually send, or it will copy the
    # driver-owned names straight back out of the error message.
    offerable = ", ".join(sorted(accepted - owned))
    for k in sorted(set(params) - accepted - owned):
        problems.append(f"{tool} has no parameter {k!r}. It accepts: {offerable}")

    for name, p in sig.parameters.items():
        if name in owned or p.default is not inspect.Parameter.empty:
            continue
        if name not in params:
            problems.append(f"{tool} requires {name!r} and it is missing")

    _fail(problems)


# -- 3. preconditions ---------------------------------------------------


def _has_corrected(ms_path: Path) -> bool | None:
    """True, False, or None when we cannot tell without CASA."""
    try:
        from casatools import table  # type: ignore[import]
    except ImportError:
        return None
    tb = table()
    try:
        tb.open(str(ms_path))
        return "CORRECTED_DATA" in tb.colnames()
    except Exception:
        return None
    finally:
        with contextlib.suppress(Exception):
            tb.close()


def precondition_status(
    req: Any, run_dir: Path, active_ms: Path, tools_done: list[str]
) -> tuple[bool, str]:
    """Return (met, label) for one precondition entry from whitelist.yaml."""
    if req == "ms_exists":
        return active_ms.exists(), "MS exists"
    if req == "has_corrected":
        got = _has_corrected(active_ms)
        if got is None:
            return True, "CORRECTED_DATA (not checkable without casatools)"
        return got, "CORRECTED_DATA"
    if isinstance(req, dict) and "file_glob" in req:
        pat = req["file_glob"]
        return bool(list(run_dir.glob(pat))), f"a file matching {pat}"
    if isinstance(req, dict) and "step_done" in req:
        t = req["step_done"]
        return t in tools_done, f"{t} completed OK"
    return True, f"unknown precondition {req!r}"


def check_preconditions(
    d: dict[str, Any],
    whitelist: dict[str, Any],
    run_dir: Path,
    active_ms: Path,
    tools_done: list[str],
) -> None:
    if d["action"] not in NEEDS_TOOL:
        return
    entry = whitelist["tools"][d["tool"]]
    problems = [
        f"{d['tool']} needs {label}, and that is not satisfied"
        for req in entry.get("requires", [])
        for met, label in [precondition_status(req, run_dir, active_ms, tools_done)]
        if not met
    ]
    _fail(problems)


# -- 4. evidence --------------------------------------------------------


def check_evidence(d: dict[str, Any], run_dir: Path) -> None:
    from verifier import _find  # same nested lookup the verifier uses

    problems: list[str] = []
    for item in d.get("evidence", []) or []:
        if not isinstance(item, dict) or "source" not in item or "name" not in item:
            problems.append(f"evidence item is malformed: {item!r}")
            continue
        src = run_dir / str(item["source"])
        if not src.exists():
            problems.append(f"evidence source does not exist: {item['source']}")
            continue
        if "value" not in item:
            continue
        try:
            actual = _find(json.loads(src.read_text()), str(item["name"]))
        except json.JSONDecodeError:
            problems.append(f"evidence source is not JSON: {item['source']}")
            continue
        if actual is None:
            problems.append(f"{item['name']!r} does not appear in {item['source']}")
            continue
        claimed = float(item["value"])
        tol = max(abs(actual) * EVIDENCE_REL_TOL, 1e-12)
        if abs(claimed - actual) > tol:
            problems.append(
                f"you cited {item['name']}={claimed:g} but {item['source']} says {actual:g}"
            )
    _fail(problems)


def validate(
    path: Path,
    whitelist: dict[str, Any],
    run_dir: Path,
    active_ms: Path,
    tools_done: list[str],
) -> dict[str, Any]:
    """Run every check in order. Raises Refusal with a message for the model."""
    d = load_decision(path)
    check_shape(d)
    check_tool(d, whitelist)
    check_preconditions(d, whitelist, run_dir, active_ms, tools_done)
    check_evidence(d, run_dir)
    return d
