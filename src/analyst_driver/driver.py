"""
analyst-driver — the external loop.

The model decides. The driver runs. Those two never wait on each other.

A tick is one pass: poll the running job, harvest it if it finished, render the
brief, call the model once, validate what it wrote, submit the next job, exit.
When a job is still running the tick ends in milliseconds and no model is in
memory. That is the entire point — an eight-hour tclean costs one model call at
the start and one at the end, not eight hours of held context.

    analyst-driver init   --run-id NAME --input PATH --goal TEXT [--recipe KEY]
    analyst-driver tick   --run DIR     one pass, then exit
    analyst-driver run    --run DIR     tick, sleep, repeat until DONE or parked
    analyst-driver status --run DIR

Run it from wherever the data lives, not from the repository.

Exit codes from `tick`: 0 keep going · 10 DONE · 20 parked, a human is needed.
"""

from __future__ import annotations

import argparse
import fcntl
import importlib
import inspect
import json
import pkgutil
import sys
import time
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from analyst_driver import backends, executors
from analyst_driver import brief as brief_mod
from analyst_driver import commit as commit_mod
from analyst_driver import state as state_mod
from analyst_driver import validate as validate_mod
from analyst_driver import verifier as verifier_mod

# The packaged defaults. config.toml, whitelist.yaml, recipe.yaml,
# verifier.yaml and PROMPT.md all ship inside the package, and init copies
# them into the run directory so a later edit here cannot change a live run.
HERE = Path(__file__).resolve().parent

EXIT_CONTINUE = 0
EXIT_DONE = 10
EXIT_PARKED = 20


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# -- configuration -------------------------------------------------------


def load_config(run_dir: Path) -> dict[str, Any]:
    """Prefer the copy inside the run. A run keeps the config it started with."""
    path = run_dir / "config.toml"
    if not path.exists():
        path = HERE / "config.toml"
    return tomllib.loads(path.read_text())


def load_yaml(run_dir: Path, name: str) -> dict[str, Any]:
    path = run_dir / name
    if not path.exists():
        path = HERE / name
    return yaml.safe_load(path.read_text())


# -- tool resolution -----------------------------------------------------

_TOOL_MODULES: dict[str, str] | None = None


def _tool_index() -> dict[str, str]:
    """Map every TOOL_NAME in the ms_* packages to its module path.

    Built by scanning rather than by a hand-written table, so a new tool is
    callable as a probe the moment it exists.
    """
    global _TOOL_MODULES
    if _TOOL_MODULES is not None:
        return _TOOL_MODULES
    index: dict[str, str] = {}
    for pkg_name in ("ms_inspect.tools", "ms_modify", "ms_create"):
        try:
            pkg = importlib.import_module(pkg_name)
        except ImportError:
            continue
        for mod_info in pkgutil.iter_modules(pkg.__path__):
            full = f"{pkg_name}.{mod_info.name}"
            try:
                mod = importlib.import_module(full)
            except Exception:
                continue
            name = getattr(mod, "TOOL_NAME", None)
            if name and hasattr(mod, "run"):
                index[name] = full
    _TOOL_MODULES = index
    return index


def _unwrap(v: Any) -> Any:
    return v["value"] if isinstance(v, dict) and "value" in v else v


# -- the input -----------------------------------------------------------


def detect_input_kind(path: Path) -> str:
    """Decide whether we were handed a Measurement Set or an ASDM.

    Detected from what is on disk, never from the flag the user typed, so
    `--ms` pointing at an ASDM still does the right thing.
    """
    info = path / "table.info"
    if info.is_file():
        try:
            if "Measurement Set" in info.read_text(errors="replace").splitlines()[0]:
                return state_mod.KIND_MS
        except (OSError, IndexError):
            pass
    if (path / "ASDM.xml").is_file():
        return state_mod.KIND_ASDM
    return ""


def resolve_ms(st: state_mod.RunState, whitelist: dict, tool: str) -> Path | None:
    """Which MS this tool operates on, from its declared ms_role."""
    role = whitelist["tools"][tool].get("ms_role", state_mod.ROLE_RAW)
    if role == "none":
        return None
    resolved = st.ms_for(role)
    return Path(resolved) if resolved else None


def workflow_status(run_dir: Path, st: state_mod.RunState) -> dict[str, Any]:
    """One ms_workflow_status call per tick, feeding preconditions and the brief.

    Only its booleans are used. `next_recommended_step` is ignored on purpose:
    that ladder will not advance past generate_priorcals until gain_curves.gc
    and opacities.opac exist, which are VLA tables, so on ALMA data it answers
    the same step forever and does not warn. In a loop that is a cycle, not a
    hint.
    """
    mod_name = _tool_index().get("ms_workflow_status")
    ms = st.ms_for(state_mod.ROLE_RAW)
    if not mod_name or not ms or not Path(ms).exists():
        return {}
    try:
        data = importlib.import_module(mod_name).run(
            ms_path=ms, workdir=str(state_mod.processed_dir(run_dir))
        )["data"]
    except Exception as exc:
        return {"probe_error": f"ms_workflow_status raised {type(exc).__name__}: {exc}"}
    data.pop("next_recommended_step", None)
    return data


# -- run directory -------------------------------------------------------


def cmd_init(args: argparse.Namespace) -> int:
    # --config selects an alternative profile. Without it the packaged
    # defaults are used, and changing where runs live would mean editing a
    # file inside the installed package.
    config_src = Path(args.config).expanduser().resolve() if args.config else HERE / "config.toml"
    if not config_src.is_file():
        print(f"no such config file: {config_src}", file=sys.stderr)
        return 1

    cfg = tomllib.loads(config_src.read_text())
    root = Path(args.root or cfg["run"]["root"]).expanduser().resolve()
    run_dir = root / args.run_id
    if run_dir.exists() and not args.force:
        print(f"{run_dir} already exists. Use --force to reuse it.", file=sys.stderr)
        return 1

    raw_input = Path(args.input or args.ms).expanduser().resolve()
    if not raw_input.is_dir():
        print(f"no such input: {raw_input}", file=sys.stderr)
        return 1
    kind = detect_input_kind(raw_input)
    if not kind:
        print(
            f"{raw_input} is neither a Measurement Set nor an ASDM. "
            "An MS has a table.info naming one; an ASDM has an ASDM.xml.",
            file=sys.stderr,
        )
        return 1

    for sub in ("steps", "decisions", "cache", "processed"):
        (run_dir / sub).mkdir(parents=True, exist_ok=True)

    # Freeze the config and the contract into the run. A later edit to the
    # package must not change how a run already in flight behaves.
    (run_dir / "config.toml").write_text(config_src.read_text())
    for name in ("whitelist.yaml", "recipe.yaml", "verifier.yaml", "PROMPT.md"):
        (run_dir / name).write_text((HERE / name).read_text())

    st = state_mod.RunState(
        run_id=args.run_id,
        goal=args.goal,
        recipe=args.recipe,
        input_path=str(raw_input),
        input_kind=kind,
        started_utc=_now(),
        status=state_mod.STATUS_IDLE,
    )
    # An MS input is the raw MS from the start. An ASDM has produced no MS
    # yet, so the registry stays empty and every MS tool reads NOT MET until
    # ms_import_asdm has run.
    if kind == state_mod.KIND_MS:
        st.record_ms(state_mod.ROLE_RAW, str(raw_input))
    state_mod.save(run_dir, st)

    _refresh_ms_cache(run_dir, st, probe_fields=True)
    _write_instrument_summary(run_dir, raw_input, kind)
    print(f"initialised {run_dir} from {kind.upper()} {raw_input.name}")
    if kind == state_mod.KIND_ASDM:
        print("input is an ASDM — the first step must be ms_import_asdm")
    print(f"next: analyst-driver run --run {run_dir}")
    return 0


def _refresh_ms_cache(run_dir: Path, st: state_mod.RunState, probe_fields: bool = False) -> None:
    """Keep cache/ms_summary.json in step with the registry.

    Driven by the registry, not by globbing: a run produces several split MSs
    and a glob cannot say which is which. Anything found in `processed/` that
    the registry does not know about is still listed, with no role, so a
    product nobody declared is visible rather than silently absent.
    """
    cache = run_dir / "cache" / "ms_summary.json"
    known: dict[str, Any] = json.loads(cache.read_text()) if cache.exists() else {}

    roles: dict[str, str] = {}
    for role, path in st.ms_registry.items():
        roles.setdefault(path, role)
    for stray in state_mod.processed_dir(run_dir).glob("*.ms"):
        roles.setdefault(str(stray), "")

    for path, role in sorted(roles.items()):
        entry = known.setdefault(
            path, {"name": Path(path).name, "fields": "?", "flag_fraction": None}
        )
        entry["role"] = role
        if probe_fields and entry["fields"] == "?":
            entry["fields"] = _probe_fields(path)
    cache.write_text(json.dumps(known, indent=2) + "\n")


def _probe_fields(ms_path: str) -> str:
    mod_name = _tool_index().get("ms_field_list")
    if not mod_name or not Path(ms_path).exists():
        return "?"
    try:
        data = importlib.import_module(mod_name).run(ms_path=ms_path).get("data", {})
        fields = _unwrap(data.get("fields", []))
        parts = [
            f"{_unwrap(f.get('field_id'))} {_unwrap(f.get('name'))}"
            for f in fields
            if isinstance(f, dict)
        ]
        return " · ".join(parts)[:34] or "?"
    except Exception:
        return "?"


def _asdm_summary_line(sdm_path: Path) -> str:
    """The ASDM equivalent of the MS instrument line.

    ms_sdm_summary takes only sdm_path — no workdir, no execute — so it is a
    probe the driver runs, never a step the model runs.
    """
    mod_name = _tool_index().get("ms_sdm_summary")
    if not mod_name:
        return f"ASDM {sdm_path.name} (ms_sdm_summary unavailable)"
    try:
        d = importlib.import_module(mod_name).run(sdm_path=str(sdm_path))["data"]
    except Exception as exc:
        return f"ASDM {sdm_path.name} (summary failed: {type(exc).__name__})"
    bits = [
        str(_unwrap(d[k]))
        for k in ("telescope_name", "n_antennas", "n_scans", "band", "total_duration_human")
        if k in d and _unwrap(d[k]) not in (None, "")
    ]
    return "ASDM · " + " · ".join(bits) if bits else f"ASDM {sdm_path.name}"


def _write_instrument_summary(run_dir: Path, path: Path, kind: str) -> None:
    """Cache the one-line instrument description shown at the top of section 2.

    Read straight from the MS subtables rather than through a tool, because
    this must not fail when a particular ms_inspect module is unavailable. An
    ASDM has no such subtables, so it goes to ms_sdm_summary instead; where
    neither works the line says so rather than guessing.
    """
    out = run_dir / "cache" / "instrument.txt"
    if kind == state_mod.KIND_ASDM:
        out.write_text(_asdm_summary_line(path) + "\n")
        return

    bits: list[str] = []
    idx = _tool_index()
    if "ms_observation_info" in idx:
        try:
            d = importlib.import_module(idx["ms_observation_info"]).run(ms_path=str(path))["data"]
            bits.append(str(_unwrap(d.get("telescope_name", "?"))))
            bits.append(f"{_unwrap(d.get('total_duration_human', '?'))} on sky")
        except Exception:
            pass
    try:
        from casatools import table

        tb = table()
        tb.open(str(path / "ANTENNA"))
        bits.insert(1, f"{tb.nrows()} antennas")
        tb.close()
        tb.open(str(path / "SPECTRAL_WINDOW"))
        nchan = tb.getcol("NUM_CHAN")
        freqs = [tb.getcell("REF_FREQUENCY", i) / 1e9 for i in range(tb.nrows())]
        tb.close()
        span = f"{min(freqs):.3f}–{max(freqs):.3f} GHz" if freqs else "?"
        bits.append(f"{len(nchan)} spw × {int(nchan[0])} ch · {span}")
    except Exception:
        pass
    out.write_text(" · ".join(b for b in bits if b) or "(instrument summary unavailable)")


def _ms_rows(run_dir: Path) -> list[dict[str, Any]]:
    cache = run_dir / "cache" / "ms_summary.json"
    if not cache.exists():
        return []
    known = json.loads(cache.read_text())
    return [{"path": p, **v} for p, v in sorted(known.items())]


# -- step records --------------------------------------------------------


def _step_records(run_dir: Path) -> list[dict[str, Any]]:
    out = []
    for d in sorted((run_dir / "steps").glob("*/")):
        f = d / "step.json"
        if f.exists():
            out.append(json.loads(f.read_text()))
    return out


def _prev_rationale(run_dir: Path, step: int) -> str:
    p = state_mod.decision_path(run_dir, step)
    if not p.exists():
        return ""
    d = json.loads(p.read_text())
    return str(d.get("decision", d).get("rationale", ""))


# -- harvest -------------------------------------------------------------


def _run_probe(entry: dict[str, Any], active_ms: Path, step_dir: Path) -> dict[str, Any]:
    """Run the read-only probe declared in whitelist.yaml.

    Probes are ms_inspect calls: fast, read-only, safe to run inside the
    driver. They produce measurements.json, which is what the verifier reads
    and what the model must cite as evidence.
    """
    probe = entry.get("probe")
    if not probe:
        return {}
    mod_name = _tool_index().get(probe["tool"])
    if not mod_name:
        return {"probe_error": f"{probe['tool']} is not importable in this environment"}
    try:
        resp = importlib.import_module(mod_name).run(
            ms_path=str(active_ms), **(probe.get("params") or {})
        )
        return resp.get("data", {})
    except Exception as exc:  # a failed probe must not kill a good step
        return {"probe_error": f"{probe['tool']} raised {type(exc).__name__}: {exc}"}


def _record_flag_fraction(run_dir: Path, ms_path: str, measurements: dict[str, Any]) -> None:
    """Keep section 2 of the brief current after a step that changed the flags."""
    frac = verifier_mod._find(measurements, "total_flag_fraction")
    if frac is None:
        frac = verifier_mod._find(measurements, "flag_fraction")
    if frac is None:
        return
    cache = run_dir / "cache" / "ms_summary.json"
    known = json.loads(cache.read_text()) if cache.exists() else {}
    if ms_path in known:
        known[ms_path]["flag_fraction"] = frac
        cache.write_text(json.dumps(known, indent=2) + "\n")


def _headline(measurements: dict[str, Any]) -> str:
    for key, fmt in (
        ("total_flag_fraction", "flagged {:.1%}"),
        ("flag_fraction", "flagged {:.1%}"),
        ("dynamic_range", "DR {:.0f}"),
        ("antennas_lost", "ants lost {:.0f}"),
    ):
        v = verifier_mod._find(measurements, key)
        if v is not None:
            return fmt.format(v)
    return ""


def adopt_outputs(st: state_mod.RunState, planned: list[dict[str, Any]]) -> tuple[list[str], str]:
    """Check each planned output arrived, and register the MSs among them.

    The tool declared these paths when it generated the script, so nothing is
    guessed and nothing is globbed — a run produces several split MSs and a
    glob cannot say which is which. A planned output that never appeared is a
    failed step, not a silent success, so it is returned as a problem.
    """
    adopted: list[str] = []
    missing: list[str] = []
    for item in planned:
        path = str(item.get("path", ""))
        if not path or not Path(path).exists():
            missing.append(path or "(unnamed output)")
            continue
        if item.get("kind") == "ms" and item.get("role"):
            st.record_ms(str(item["role"]), path)
            adopted.append(f"{item['role']}={Path(path).name}")
    problem = (
        "the step reported success but did not produce: " + ", ".join(missing) if missing else ""
    )
    return adopted, problem


def harvest(run_dir: Path, st: state_mod.RunState, whitelist: dict, ex) -> dict[str, Any]:
    """Turn a finished job into a step record plus measurements.json."""
    pending = st.pending
    step_dir = Path(pending.step_dir)
    rc = ex.exit_code(step_dir)
    result = "OK" if rc == 0 else "FAILED"

    adopted: list[str] = []
    missing = ""
    if result == "OK":
        adopted, missing = adopt_outputs(st, pending.planned_outputs)
        if missing:
            result = "FAILED"

    entry = whitelist["tools"][pending.tool]
    probe_ms = resolve_ms(st, whitelist, pending.tool)
    measurements = _run_probe(entry, probe_ms, step_dir) if result == "OK" and probe_ms else {}
    (step_dir / "measurements.json").write_text(json.dumps(measurements, indent=2) + "\n")
    if probe_ms:
        _record_flag_fraction(run_dir, str(probe_ms), measurements)

    decision = json.loads(state_mod.decision_path(run_dir, pending.step).read_text())
    inner = decision.get("decision", decision)

    record = {
        "step": pending.step,
        "tool": pending.tool,
        "params": inner.get("params", {}),
        "result": result,
        "exit_code": rc,
        "headline": _headline(measurements) or (", ".join(adopted) if adopted else ""),
        "duration": _duration(pending.submitted_utc),
        "rationale": inner.get("rationale", ""),
        "step_dir": str(step_dir),
        "ms_used": str(probe_ms) if probe_ms else "",
        "produced": adopted,
    }
    if missing:
        record["missing_outputs"] = missing
    (step_dir / "step.json").write_text(json.dumps(record, indent=2) + "\n")

    if result == "OK" and pending.tool not in st.tools_done:
        st.tools_done.append(pending.tool)
    st.pending = None
    return record


def _duration(started: str) -> str:
    try:
        t0 = datetime.strptime(started, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return "?"
    secs = int((datetime.now(UTC) - t0).total_seconds())
    return f"{secs // 3600}h{(secs % 3600) // 60:02d}m{secs % 60:02d}s"


# -- termination ---------------------------------------------------------


def park(run_dir: Path, st: state_mod.RunState, status: str, reason: str) -> int:
    st.status = status
    st.park_reason = reason
    state_mod.save(run_dir, st)
    print(f"[{status}] {reason}")
    return EXIT_DONE if status == state_mod.STATUS_DONE else EXIT_PARKED


def check_limits(run_dir: Path, st: state_mod.RunState, cfg: dict) -> str:
    """Every limit is enforced here, by the driver. The model is never told."""
    if (run_dir / "STOP").exists():
        return "the STOP file exists"
    if st.step >= int(cfg["run"]["step_cap"]):
        return f"step cap of {cfg['run']['step_cap']} reached"
    try:
        t0 = datetime.strptime(st.started_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        hours = (datetime.now(UTC) - t0).total_seconds() / 3600
        if hours >= float(cfg["run"]["wall_clock_hours"]):
            return f"wall clock limit of {cfg['run']['wall_clock_hours']}h reached"
    except ValueError:
        pass
    return ""


def check_cycle(st: state_mod.RunState, digest: str, window: int) -> str:
    if digest in st.call_digests[-window:]:
        return (
            "that exact call already ran inside the last "
            f"{window} steps. Repeating it cannot change the result."
        )
    return ""


def generate_script(
    run_dir: Path, st: state_mod.RunState, whitelist: dict, inner: dict[str, Any]
) -> tuple[Path, Path, list[dict[str, Any]]]:
    """Ask the tool to write its script.

    Returns (step_dir, script_path, planned_outputs).

    The driver owns four parameters and the model owns the science ones. Which
    MS comes from the tool's declared ms_role, so the model never names an MS
    and can never reach a path outside this run.

    workdir is `processed/`, shared by every step, because that is where the
    products belong and what ms_workflow_status reads. The step directory
    holds only the script and the logs.
    """
    tool = inner["tool"]
    step_dir = state_mod.step_dir(run_dir, st.step, tool)
    step_dir.mkdir(parents=True, exist_ok=True)
    processed = state_mod.processed_dir(run_dir)
    processed.mkdir(parents=True, exist_ok=True)
    module = importlib.import_module(whitelist["tools"][tool]["module"])

    # Not every tool takes every driver-owned parameter: ms_flag_caltable acts
    # on a caltable and has no ms_path, ms_import_asdm takes asdm_path.
    # Supply the intersection, so a tool is never handed one it does not want.
    accepted = set(inspect.signature(module.run).parameters)
    ms = resolve_ms(st, whitelist, tool)
    owned = {
        "ms_path": str(ms) if ms else "",
        "asdm_path": st.input_path,
        "workdir": str(processed),
        "execute": False,
    }
    kwargs = {k: v for k, v in owned.items() if k in accepted}

    data = module.run(**kwargs, **inner.get("params", {}))["data"]
    script = Path(_unwrap(data["script_path"]))

    # Tools write their script into workdir, which is now shared. Move it into
    # the step directory so one step's script cannot overwrite another's on a
    # redo. The generated scripts carry absolute paths, so moving is safe.
    if script.parent != step_dir:
        moved = step_dir / script.name
        moved.write_text(script.read_text())
        script.unlink()
        script = moved

    return step_dir, script, list(data.get("planned_outputs") or [])


def _rendered_recipe(recipe: dict[str, Any], st: state_mod.RunState) -> dict[str, Any]:
    """Drop the optional import step from the map on an MS run.

    Every recipe lists ms_import_asdm first, so one list serves both starting
    points. Showing it to a model that started from an MS would only invite a
    step the input_is_asdm precondition already refuses.
    """
    if st.input_kind == state_mod.KIND_ASDM:
        return recipe
    out = dict(recipe)
    out["order"] = [t for t in recipe.get("order", []) if t != "ms_import_asdm"]
    return out


# -- the tick ------------------------------------------------------------


def tick(run_dir: Path) -> int:  # noqa: C901 - the loop is a flat sequence, kept in one place
    cfg = load_config(run_dir)
    whitelist = load_yaml(run_dir, "whitelist.yaml")
    recipes = load_yaml(run_dir, "recipe.yaml")["recipes"]
    rules = verifier_mod.load_rules(run_dir / "verifier.yaml")
    ex = executors.build(cfg["executor"])

    st = state_mod.load(run_dir)
    if st.status in state_mod.TERMINAL:
        print(f"[{st.status}] {st.park_reason or 'nothing to do'}")
        return EXIT_DONE if st.status == state_mod.STATUS_DONE else EXIT_PARKED

    completed: dict[str, Any] | None = None

    # 1. a job is in flight
    if st.pending is not None:
        step_dir = Path(st.pending.step_dir)
        status = ex.poll(st.pending.job_id, step_dir)
        if status == executors.RUNNING:
            return EXIT_CONTINUE  # the whole point: no model, no context, seconds
        completed = harvest(run_dir, st, whitelist, ex)
        state_mod.save(run_dir, st)

    # 2. hard limits
    reason = check_limits(run_dir, st, cfg)
    if reason:
        return park(run_dir, st, state_mod.STATUS_STOPPED, reason)

    _refresh_ms_cache(run_dir, st)

    # One ms_workflow_status call per tick. Its booleans drive the
    # preconditions and section 2; its next_recommended_step is discarded.
    workflow = workflow_status(run_dir, st)

    # 3. ask the model, with up to max_refusals attempts
    st.step += 1
    st.refusals_this_step = 0
    decision_file = state_mod.decision_path(run_dir, st.step)
    refusals: list[str] = []
    verdict_text = ""
    if completed:
        meas = json.loads((Path(completed["step_dir"]) / "measurements.json").read_text())
        verdict_text = verifier_mod.render(verifier_mod.check(rules, completed["tool"], meas))

    max_refusals = int(cfg["run"]["max_refusals"])
    inner: dict[str, Any] | None = None

    while True:
        brief_path = brief_mod.render(
            run_dir=run_dir,
            run_id=st.run_id,
            step=st.step,
            goal=st.goal,
            instrument=_instrument_line(run_dir),
            ms_rows=_ms_rows(run_dir),
            ctx=validate_mod.Context(
                run_dir=run_dir,
                tools_done=st.tools_done,
                input_kind=st.input_kind,
                workflow=workflow,
            ),
            resolve_ms=lambda tool: resolve_ms(st, whitelist, tool),
            whitelist=whitelist,
            recipe=_rendered_recipe(recipes[st.recipe], st),
            steps=_step_records(run_dir),
            last=completed,
            last_step_dir=Path(completed["step_dir"]) if completed else None,
            verdict_text=verdict_text,
            prev_rationale=_prev_rationale(run_dir, st.step - 1),
            refusals=refusals,
            decision_path=decision_file,
            full_tail=int(cfg["run"]["history_full_steps"]),
        )
        prompt_file = backends.build_prompt(run_dir, run_dir / "PROMPT.md", brief_path)

        try:
            backends.run_model(cfg["backend"], run_dir, prompt_file, decision_file)
            peeked = validate_mod.load_decision(decision_file)
            inner = validate_mod.validate(
                decision_file,
                whitelist,
                validate_mod.Context(
                    run_dir=run_dir,
                    tools_done=st.tools_done,
                    input_kind=st.input_kind,
                    workflow=workflow,
                    ms_path=(
                        resolve_ms(st, whitelist, peeked["tool"])
                        if peeked.get("tool") in whitelist["tools"]
                        else None
                    ),
                ),
            )
        except (backends.BackendError, validate_mod.Refusal) as exc:
            refusals.append(str(exc))
            st.refusals_this_step = len(refusals)
            state_mod.save(run_dir, st)
            if len(refusals) >= max_refusals:
                return park(
                    run_dir,
                    st,
                    state_mod.STATUS_NEEDS_HUMAN,
                    f"{len(refusals)} invalid decisions at step {st.step}:\n{exc}",
                )
            continue

        if inner["action"] in validate_mod.NEEDS_TOOL:
            digest = state_mod.call_digest(inner["tool"], inner.get("params", {}))
            cycle = check_cycle(st, digest, int(cfg["run"]["cycle_window"]))
            if cycle:
                refusals.append(cycle)
                st.refusals_this_step = len(refusals)
                state_mod.save(run_dir, st)
                if len(refusals) >= max_refusals:
                    return park(run_dir, st, state_mod.STATUS_NEEDS_HUMAN, cycle)
                continue

            # Generate the script here, still inside the refusal loop. The tool
            # itself is the strictest check of the model's parameters, so a tool
            # that rejects them is a refusal to hand back — not a crash.
            try:
                step_dir, script, planned = generate_script(run_dir, st, whitelist, inner)
            except Exception as exc:
                problem = (
                    f"- {inner['tool']} rejected these parameters: {type(exc).__name__}: {exc}"
                )
                refusals.append(problem)
                st.refusals_this_step = len(refusals)
                state_mod.save(run_dir, st)
                if len(refusals) >= max_refusals:
                    return park(run_dir, st, state_mod.STATUS_NEEDS_HUMAN, problem)
                continue
        break

    # 4. act on it
    provenance = {
        "action": inner["action"],
        "tool": inner.get("tool", ""),
        "backend": cfg["backend"]["kind"],
        "executor": ex.kind,
        "ms_registry": dict(st.ms_registry),
        "refusals": len(refusals),
    }
    warnings = commit_mod.commit_turn(
        run_dir=run_dir,
        step=st.step,
        decision_file=decision_file,
        provenance=provenance,
        completed=(
            {
                "tool": completed["tool"],
                "params": completed["params"],
                "outputs": {"headline": completed["headline"], "step_dir": completed["step_dir"]},
                "rationale": completed["rationale"],
            }
            if completed and completed["result"] == "OK"
            else None
        ),
        use_git=bool(cfg["provenance"]["git"]),
    )
    for w in warnings:
        print(f"  warning: {w}")

    action = inner["action"]
    if action == "done":
        return park(run_dir, st, state_mod.STATUS_DONE, inner["rationale"])
    if action == "ask":
        return park(run_dir, st, state_mod.STATUS_NEEDS_HUMAN, inner["rationale"])

    # 5. submit the script generated above
    tool = inner["tool"]
    job_id = ex.submit(script, step_dir, f"{st.run_id}-{st.step:03d}", planned)
    st.pending = state_mod.Pending(
        job_id=job_id,
        step=st.step,
        tool=tool,
        submitted_utc=_now(),
        step_dir=str(step_dir),
        planned_outputs=planned,
    )
    st.call_digests.append(state_mod.call_digest(tool, inner.get("params", {})))
    st.status = state_mod.STATUS_RUNNING
    state_mod.save(run_dir, st)
    print(f"step {st.step:03d} · {tool} · submitted as {job_id}")
    return EXIT_CONTINUE


def _instrument_line(run_dir: Path) -> str:
    f = run_dir / "cache" / "instrument.txt"
    return f.read_text().strip() if f.exists() else "(instrument summary not cached)"


# -- entry points --------------------------------------------------------


class BadRunDir(SystemExit):
    """A wrong --run path, reported as one line rather than a stack trace."""


def _resolve_run(args: argparse.Namespace) -> Path:
    """Turn --run into a checked run directory.

    Every one of these commands is typed by hand from a data directory, so a
    typo is the common case. Report it plainly.
    """
    run_dir = Path(args.run).expanduser().resolve()
    if not run_dir.is_dir():
        raise BadRunDir(f"no such run directory: {run_dir}")
    if not state_mod.state_path(run_dir).is_file():
        raise BadRunDir(
            f"{run_dir} holds no run.json, so it is not a run directory. "
            f"Create one with: analyst-driver init --run-id NAME --input PATH --goal TEXT"
        )
    return run_dir


def _locked_tick(run_dir: Path) -> int:
    """One tick, guarded so two copies of the driver cannot both act."""
    lock = run_dir / ".lock"
    with lock.open("w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("another driver holds the lock — nothing done")
            return EXIT_CONTINUE
        return tick(run_dir)


def cmd_tick(args: argparse.Namespace) -> int:
    return _locked_tick(_resolve_run(args))


def cmd_run(args: argparse.Namespace) -> int:
    """Tick, sleep, repeat.

    Only this small process sleeps. The model exits after every decision and
    the science job runs detached, so nothing holds context open across the
    wait.
    """
    run_dir = _resolve_run(args)
    interval = int(load_config(run_dir)["run"]["poll_seconds"])
    while True:
        rc = _locked_tick(run_dir)
        if rc != EXIT_CONTINUE:
            return rc
        time.sleep(interval)


def cmd_status(args: argparse.Namespace) -> int:
    run_dir = _resolve_run(args)
    st = state_mod.load(run_dir)
    print(f"{st.run_id}: {st.status} at step {st.step}")
    print(f"  input     : {st.input_kind.upper()} {st.input_path}")
    for role, path in sorted(st.ms_registry.items()):
        print(f"  {role:<10}: {path}")
    if st.pending:
        print(f"  pending   : {st.pending.tool} as {st.pending.job_id}")
    if st.park_reason:
        print(f"  reason    : {st.park_reason}")
    for rec in _step_records(run_dir)[-10:]:
        print(f"  {rec['step']:>3} {rec['tool']:<26} {rec['result']:<7} {rec['headline']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("init", help="create a run directory")
    i.add_argument("--run-id", required=True)
    i.add_argument(
        "--input", default="", help="the Measurement Set or ASDM to reduce (kind is detected)"
    )
    i.add_argument("--ms", default="", help="alias for --input, kept for older invocations")
    i.add_argument("--goal", required=True, help="one or two sentences; the model reads this")
    i.add_argument("--recipe", default="vla_continuum", help="the usual order to show as a map")
    i.add_argument("--root", default="", help="where to create the run (overrides the config)")
    i.add_argument("--config", default="", help="an alternative config.toml to freeze into the run")
    i.add_argument("--force", action="store_true", help="reuse an existing run directory")
    i.set_defaults(fn=cmd_init)

    for name, fn, helptext in (
        ("tick", cmd_tick, "one pass, then exit"),
        ("run", cmd_run, "loop until DONE or parked"),
        ("status", cmd_status, "show the run state"),
    ):
        s = sub.add_parser(name, help=helptext)
        s.add_argument("--run", required=True)
        s.set_defaults(fn=fn)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
