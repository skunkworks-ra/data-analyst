"""
reduction_log.py — ms_reduction_log

A place to shuttle KNOWN-GOOD calls as a reduction proceeds. After a tool call
succeeds, append it here with the exact parameters that worked, the salient
output, and why it was done. The accumulated log is the replayable "working
path" through the data — the canonical recipe for this dataset, and the
artifact a cheaper model (or a future run) can replay step by step.

Only validated calls should be shuttled in: failed attempts and dead ends stay
out, so the ledger is the clean path, not the search for it.

Storage is a JSON-lines file `reduction_log.jsonl` in the workdir — one record
per line, append-only, trivially diffable and greppable.

Actions:
  append  — record one working call (tool, params, outputs, rationale, rule)
  render  — emit the ordered recipe and a replay Python script
  list    — compact summary (step, tool, rationale) of the recorded path
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from ms_inspect.util.formatting import field as fmt_field
from ms_inspect.util.formatting import response_envelope

TOOL_NAME = "ms_reduction_log"

_LOG_NAME = "reduction_log.jsonl"


def _log_path(workdir: str) -> Path:
    return Path(workdir) / _LOG_NAME


def _read_records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records: list[dict] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


# Maps the recorded tool name to the package module whose run() implements it.
# Steps whose tool name is not in this registry are emitted as MANUAL markers
# (e.g. ad-hoc casatasks bypasses), so the script is honest about what it can
# and cannot replay automatically.
_RUN_REGISTRY: dict[str, str] = {
    "ms_sdm_summary": "ms_create.sdm_summary",
    "ms_import_asdm": "ms_create.import_asdm",
    # NOTE: ms_set_intents is intentionally NOT registered — its entrypoint is
    # set_intents(), not run(), so it cannot be replayed via the run() convention
    # and is emitted as a MANUAL step. It is also a rare one-off (only for MSs
    # that lack scan intents).
    "ms_apply_preflag": "ms_modify.preflag",
    "ms_generate_priorcals": "ms_modify.priorcals",
    "ms_initial_bandpass": "ms_modify.initial_bandpass",
    "ms_apply_initial_rflag": "ms_modify.initial_rflag",
    "ms_setjy": "ms_modify.setjy",
    "ms_setjy_polcal": "ms_modify.setjy_polcal",
    "ms_gaincal": "ms_modify.gaincal",
    "ms_bandpass": "ms_modify.bandpass",
    "ms_fluxscale": "ms_modify.fluxscale",
    "ms_polcal": "ms_modify.polcal",
    "ms_applycal": "ms_modify.applycal",
    "ms_apply_rflag": "ms_modify.rflag",
    "ms_flag_caltable": "ms_modify.flag_caltable",
    "ms_tclean": "ms_modify.tclean",
}


# Tools that legitimately act on an EARLIER MS after the working MS has moved
# on. Flagging and inspection of the pre-split MS stay valid — only calibration
# and imaging steps must follow the working MS forward.
_SUPERSEDED_MS_ALLOWED: frozenset[str] = frozenset(
    {
        "ms_apply_preflag",
        "ms_apply_rflag",
        "ms_apply_initial_rflag",
        "ms_postcal_flag",
        "ms_set_intents",
    }
)


def _record_ms(record: dict) -> str | None:
    """The MS a record acted on, or None if the record names no MS."""
    params = record.get("params") or {}
    ms = params.get("ms_path") or params.get("vis")
    return str(ms) if ms else None


def _var_name(ms_path: str, taken: set[str]) -> str:
    """A readable, unique Python identifier for an MS path."""
    stem = Path(ms_path).name or "ms"
    base = "".join(c if c.isalnum() else "_" for c in stem).strip("_").lower()
    if not base or base[0].isdigit():
        base = f"ms_{base}"
    name, n = base, 2
    while name in taken:
        name, n = f"{base}_{n}", n + 1
    taken.add(name)
    return name


def _ms_chain(records: list[dict]) -> tuple[list[dict], list[dict]]:
    """
    Walk the records and return (chain, violations).

    A step may declare ``supersedes`` — the MS path it REPLACES. That is the
    ALMA prior-cal split: after it, the old MS holds data without the priors
    applied, so continuing to use it is a science error that produces no error
    message.

    A split that does NOT declare `supersedes` is a side branch, not a
    replacement. The VLA calibrators.ms split is exactly that: later steps
    correctly return to the full MS to applycal and image. So supersession is
    declared, never inferred from the path sequence — inferring it would flag
    every normal VLA reduction.
    """
    chain: list[dict] = []
    violations: list[dict] = []
    superseded: dict[str, int] = {}  # ms path -> step that replaced it

    for r in records:
        step = r.get("step")
        tool = r.get("tool", "?")
        ms = _record_ms(r)

        if ms is not None and ms in superseded and tool not in _SUPERSEDED_MS_ALLOWED:
            violations.append(
                {
                    "step": step,
                    "tool": tool,
                    "ms_path": ms,
                    "superseded_at_step": superseded[ms],
                    "problem": (
                        f"step {step} ({tool}) uses {ms}, which step "
                        f"{superseded[ms]} replaced. The replaced MS does not "
                        f"carry the calibration applied at that step, so a "
                        f"replay would silently produce a wrong result."
                    ),
                }
            )

        sup = r.get("supersedes")
        if sup:
            superseded[str(sup)] = step
            chain.append({"step": step, "tool": tool, "replaced": str(sup), "with": ms})

    return chain, violations


def _replay_script(records: list[dict]) -> str:
    """
    Render an EXECUTABLE replay of the recorded working calls.

    Each recognised step becomes ``importlib.import_module(mod).run(**params)``.
    Faithful replay requires the appended params to be the literal working
    kwargs (absolute paths, full gaintable lists) — abbreviated/placeholder
    params will not run as-is.
    """
    lines = [
        "#!/usr/bin/env python",
        '"""',
        "Auto-generated by ms_reduction_log render — executable replay of the",
        "working path. Run inside the project environment (pixi run python ...).",
        "Review before running: paths and selections are dataset-specific.",
        '"""',
        "import importlib",
        "",
    ]

    # Declare each MS once, then reference the variable. Repeating literal MS
    # paths down the file is how a replay ends up half on one MS and half on
    # another without it being visible on any single line.
    ms_vars: dict[str, str] = {}
    taken: set[str] = set()
    for r in records:
        ms = _record_ms(r)
        if ms and ms not in ms_vars:
            ms_vars[ms] = _var_name(ms, taken)
    if ms_vars:
        lines.append("# --- Measurement Sets used by this reduction ---")
        for ms, var in ms_vars.items():
            lines.append(f"{var} = {ms!r}")
        lines.append("")

    chain, _ = _ms_chain(records)
    for link in chain:
        lines.append(
            f"# step {link['step']} ({link['tool']}) REPLACED "
            f"{link['replaced']!r} with {link['with']!r} — every later "
            f"calibration or imaging step must use the latter."
        )
    if chain:
        lines.append("")

    for r in records:
        tool = r.get("tool", "?")
        params = r.get("params", {})
        rationale = r.get("rationale")
        if rationale:
            lines.append(f"# step {r.get('step')}: {rationale}")
        mod = _RUN_REGISTRY.get(tool)
        if mod is None:
            lines.append(f"# MANUAL STEP — no run() mapping for {tool!r}:")
            lines.append(f"#   params = {params!r}")
        else:
            lines.append(f"importlib.import_module({mod!r}).run(")
            for k, v in params.items():
                # Emit the variable for MS-valued params so the declared name is
                # the single source of truth.
                if k in ("ms_path", "vis") and isinstance(v, str) and v in ms_vars:
                    lines.append(f"    {k}={ms_vars[v]},")
                else:
                    lines.append(f"    {k}={v!r},")
            lines.append(")")
        lines.append("")
    return "\n".join(lines)


def run(
    action: str,
    workdir: str,
    tool: str = "",
    params: dict | None = None,
    outputs: dict | None = None,
    rationale: str = "",
    skill_rule: str = "",
    status: str = "ok",
    supersedes: str = "",
) -> dict:
    """
    Append to / render / list the reduction working-calls ledger.

    Args:
        action:     'append', 'render', or 'list'.
        workdir:    Directory holding (or to hold) reduction_log.jsonl.
        tool:       (append) name of the tool/call that worked, e.g. 'ms_gaincal'.
        params:     (append) exact parameters that worked.
        outputs:    (append) salient outputs worth recording (paths, key numbers).
        rationale:  (append) why this step was done.
        skill_rule: (append) skill file / threshold cited, e.g. '07 Step 3'.
        status:     (append) outcome tag; default 'ok'. Only shuttle working calls.
        supersedes: (append) MS path this step REPLACES, if it replaces one.
                    Set it only for a split whose output takes over as the
                    working MS — the ALMA prior-cal split. Do NOT set it for a
                    side-branch split such as the VLA calibrators.ms, where
                    later steps correctly return to the original MS. render
                    refuses to emit a replay script if a later calibration or
                    imaging step still uses a superseded MS.

    Returns:
        Standard envelope. append → n_records; render → recipe + replay_script;
        list → compact step/tool/rationale summary.
    """
    wd = Path(workdir)
    if not wd.is_dir():
        from ms_inspect.exceptions import ComputationError

        raise ComputationError(f"workdir does not exist: {workdir}", ms_path=workdir)

    path = _log_path(workdir)

    if action == "append":
        records = _read_records(path)
        record = {
            "step": len(records) + 1,
            "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "tool": tool,
            "params": params or {},
            "outputs": outputs or {},
            "rationale": rationale,
            "skill_rule": skill_rule,
            "status": status,
        }
        if supersedes:
            record["supersedes"] = supersedes
        with path.open("a") as fh:
            fh.write(json.dumps(record) + "\n")
        return response_envelope(
            tool_name=TOOL_NAME,
            ms_path=workdir,
            data={
                "action": "append",
                "log_path": fmt_field(str(path)),
                "step_recorded": fmt_field(record["step"]),
                "n_records": fmt_field(len(records) + 1),
            },
            casa_calls=[f"append → {path}"],
        )

    records = _read_records(path)

    if action == "list":
        summary = [
            {
                "step": r.get("step"),
                "tool": r.get("tool"),
                "rationale": r.get("rationale"),
                "skill_rule": r.get("skill_rule"),
            }
            for r in records
        ]
        return response_envelope(
            tool_name=TOOL_NAME,
            ms_path=workdir,
            data={"action": "list", "n_records": fmt_field(len(records)), "steps": summary},
            casa_calls=[f"read → {path}"],
        )

    if action == "render":
        chain, violations = _ms_chain(records)
        n_ms_steps = sum(1 for r in records if _record_ms(r) is not None)

        # Report the work the check did, not just its verdict. With no
        # supersession declared the check cannot fail, and a caller must be able
        # to see that rather than read a clean pass as evidence.
        check_report = {
            "n_records_checked": fmt_field(len(records)),
            "n_steps_naming_an_ms": fmt_field(n_ms_steps),
            "n_supersessions_declared": fmt_field(len(chain)),
            "ms_chain": chain,
            "check_effective": fmt_field(
                bool(chain),
                note=(
                    "No step declared `supersedes`, so the superseded-MS check "
                    "could not fail. That is correct for a VLA reduction, where "
                    "calibrators.ms is a side branch. An ALMA reduction whose "
                    "prior-cal split declared nothing would look identical here."
                )
                if not chain
                else None,
            ),
        }

        if violations:
            # Refuse rather than emit. A script that runs post-split steps
            # against the pre-split MS fails silently and produces wrong
            # science; a missing script does not. Raised (not returned as a
            # warning) so the caller cannot proceed past it by ignoring a field.
            from ms_inspect.exceptions import ComputationError

            detail = "\n".join(f"  - {v['problem']}" for v in violations)
            raise ComputationError(
                f"Refused to render a replay script: {len(violations)} step(s) use an MS "
                f"that a later step replaced.\n{detail}\n"
                f"Fix the recorded ms_path on those steps (append corrected records, or "
                f"edit {path}), then render again. "
                f"Steps that only flag or inspect an earlier MS are exempt: "
                f"{', '.join(sorted(_SUPERSEDED_MS_ALLOWED))}.",
                ms_path=workdir,
            )

        script = _replay_script(records)
        script_path = wd / "reduction_replay.py"
        script_path.write_text(script)
        return response_envelope(
            tool_name=TOOL_NAME,
            ms_path=workdir,
            data={
                "action": "render",
                "n_records": fmt_field(len(records)),
                "recipe": fmt_field(records),
                "replay_script": fmt_field(str(script_path)),
                "order_violations": [],
                **check_report,
            },
            casa_calls=[f"read → {path}", f"write → {script_path}"],
        )

    from ms_inspect.exceptions import ComputationError

    raise ComputationError(
        f"Unknown action '{action}'; use 'append', 'render', or 'list'.",
        ms_path=workdir,
    )
