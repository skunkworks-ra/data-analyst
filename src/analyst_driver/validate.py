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

import dataclasses
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
#
# These used to open the MS with casatools to answer "is CORRECTED_DATA
# there?". ms_workflow_status already computes that, along with everything
# else that exists in the processed directory, so the driver asks it once per
# tick instead of re-deriving the same facts here.


@dataclasses.dataclass
class Context:
    """Everything a precondition may consult, gathered once per tick.

    `workflow` is the data block from ms_workflow_status. That tool already
    computes what exists in the processed directory, so the driver reads its
    booleans rather than re-deriving them by globbing. Its
    next_recommended_step is deliberately not used — see whitelist.yaml.
    """

    run_dir: Path
    tools_done: list[str]
    input_kind: str = "ms"
    workflow: dict[str, Any] = dataclasses.field(default_factory=dict)
    # Resolved per tool from its ms_role, so a precondition never has to know
    # which MS a step is about to operate on.
    ms_path: Path | None = None


def _workflow_flag(workflow: dict[str, Any], key: str) -> bool | None:
    """Read one ms_workflow_status boolean. None means it could not be read."""
    if key not in workflow:
        return None
    v = workflow[key]
    if isinstance(v, dict):
        if v.get("flag") == "UNAVAILABLE":
            return None
        v = v.get("value")
    if isinstance(v, list):
        return len(v) > 0
    return None if v is None else bool(v)


def precondition_status(req: Any, ctx: Context) -> tuple[bool, str]:
    """Return (met, label) for one precondition entry from whitelist.yaml.

    An unknown answer counts as MET, and says so in its label. Preconditions
    exist to save compute, not to enforce science: refusing on something we
    could not measure would park a run over a failed probe, and the tool
    itself fails loudly and visibly if the input really is missing.
    """
    if req == "input_is_asdm":
        return ctx.input_kind == "asdm", "the run to have started from an ASDM"

    if req == "ms_exists":
        if ctx.ms_path is None or not str(ctx.ms_path):
            return False, "an MS for this tool's role to exist yet"
        return ctx.ms_path.exists(), f"{ctx.ms_path.name} to exist"

    if isinstance(req, dict) and "file_glob" in req:
        pat = req["file_glob"]
        return bool(list(ctx.run_dir.glob(pat))), f"a file matching {pat}"

    if isinstance(req, dict) and "step_done" in req:
        t = req["step_done"]
        return t in ctx.tools_done, f"{t} to have completed OK"

    for kind, want in (("workflow", True), ("not_workflow", False)):
        if isinstance(req, dict) and kind in req:
            key = req[kind]
            got = _workflow_flag(ctx.workflow, key)
            phrase = key.replace("_", " ")
            if got is None:
                return True, f"{phrase} (not measurable — ms_workflow_status did not report it)"
            return got is want, phrase if want else f"{phrase} to be false"

    return True, f"unknown precondition {req!r}"


def check_preconditions(d: dict[str, Any], whitelist: dict[str, Any], ctx: Context) -> None:
    if d["action"] not in NEEDS_TOOL:
        return
    entry = whitelist["tools"][d["tool"]]
    problems = [
        f"{d['tool']} needs {label}, and that is not satisfied"
        for req in entry.get("requires", [])
        for met, label in [precondition_status(req, ctx)]
        if not met
    ]
    _fail(problems)


# -- 4. evidence --------------------------------------------------------


def check_evidence(d: dict[str, Any], run_dir: Path) -> None:
    from analyst_driver.verifier import _find  # the same nested lookup

    problems: list[str] = []
    for item in d.get("evidence", []) or []:
        if not isinstance(item, dict) or "source" not in item or "name" not in item:
            problems.append(f"evidence item is malformed: {item!r}")
            continue
        if not str(item["source"]).strip():
            problems.append(f"evidence for {item['name']!r} names no source file")
            continue
        # is_file, not exists: an empty or directory-shaped source resolves to
        # the run directory itself, and reading that raises IsADirectoryError
        # out of the validator instead of refusing the decision.
        src = run_dir / str(item["source"])
        if not src.is_file():
            problems.append(f"evidence source is not a file: {item['source']}")
            continue
        if "value" not in item:
            continue
        try:
            actual = _find(json.loads(src.read_text()), str(item["name"]))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            problems.append(f"evidence source could not be read as JSON: {item['source']}")
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


def validate(path: Path, whitelist: dict[str, Any], ctx: Context) -> dict[str, Any]:
    """Run every check in order. Raises Refusal with a message for the model.

    The caller resolves ctx.ms_path from the chosen tool's ms_role before
    calling, because which MS a step is about to touch is a property of the
    tool, not of the run.
    """
    d = load_decision(path)
    check_shape(d)
    check_tool(d, whitelist)
    check_preconditions(d, whitelist, ctx)
    check_evidence(d, ctx.run_dir)
    return d
