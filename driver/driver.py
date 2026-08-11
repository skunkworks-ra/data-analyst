#!/usr/bin/env python
"""
driver.py — the external loop.

The model decides. The driver runs. Those two never wait on each other.

A tick is one pass: poll the running job, harvest it if it finished, render the
brief, call the model once, validate what it wrote, submit the next job, exit.
When a job is still running the tick ends in milliseconds and no model is in
memory. That is the entire point — an eight-hour tclean costs one model call at
the start and one at the end, not eight hours of held context.

    driver.py init  --run-id NAME --ms PATH --goal TEXT [--recipe KEY]
    driver.py tick  --run DIR      one pass, then exit
    driver.py run   --run DIR      tick, sleep, repeat until DONE or parked
    driver.py status --run DIR

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

sys.path.insert(0, str(Path(__file__).resolve().parent))

import backends  # noqa: E402
import brief as brief_mod  # noqa: E402
import commit as commit_mod  # noqa: E402
import executors  # noqa: E402
import state as state_mod  # noqa: E402
import validate as validate_mod  # noqa: E402
import verifier as verifier_mod  # noqa: E402

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


# -- run directory -------------------------------------------------------


def cmd_init(args: argparse.Namespace) -> int:
    cfg = tomllib.loads((HERE / "config.toml").read_text())
    root = Path(cfg["run"]["root"]).expanduser()
    run_dir = root / args.run_id
    if run_dir.exists() and not args.force:
        print(f"{run_dir} already exists. Use --force to reuse it.", file=sys.stderr)
        return 1

    for sub in ("steps", "decisions", "cache"):
        (run_dir / sub).mkdir(parents=True, exist_ok=True)

    # Freeze the config and the contract into the run. A later edit to the
    # repo must not change how a run already in flight behaves.
    for name in ("config.toml", "whitelist.yaml", "recipe.yaml", "verifier.yaml", "PROMPT.md"):
        (run_dir / name).write_text((HERE / name).read_text())

    ms = Path(args.ms).expanduser().resolve()
    st = state_mod.RunState(
        run_id=args.run_id,
        goal=args.goal,
        recipe=args.recipe,
        active_ms=str(ms),
        started_utc=_now(),
        status=state_mod.STATUS_IDLE,
    )
    state_mod.save(run_dir, st)

    _refresh_ms_cache(run_dir, ms, probe_fields=True)
    _write_instrument_summary(run_dir, ms)
    print(f"initialised {run_dir}")
    print(f"next: driver.py run --run {run_dir}")
    return 0


def _refresh_ms_cache(run_dir: Path, active_ms: Path, probe_fields: bool = False) -> None:
    """Keep cache/ms_summary.json honest as splits create new MSs.

    Rescans for .ms directories every tick, so a split shows up in the brief
    without the model having to be told about it. Values we do not have are
    written as unknown, never guessed.
    """
    cache = run_dir / "cache" / "ms_summary.json"
    known: dict[str, Any] = json.loads(cache.read_text()) if cache.exists() else {}

    candidates = {str(active_ms)}
    for base in (run_dir, active_ms.parent):
        candidates.update(str(p) for p in base.glob("*.ms") if p.is_dir())

    for path in sorted(candidates):
        entry = known.setdefault(
            path, {"name": Path(path).name, "fields": "?", "flag_fraction": None}
        )
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


def _write_instrument_summary(run_dir: Path, ms_path: Path) -> None:
    """Cache the one-line instrument description shown at the top of section 2.

    Read straight from the MS subtables rather than through a tool, because
    this must not fail when a particular ms_inspect module is unavailable.
    """
    bits: list[str] = []
    idx = _tool_index()
    if "ms_observation_info" in idx:
        try:
            d = importlib.import_module(idx["ms_observation_info"]).run(ms_path=str(ms_path))[
                "data"
            ]
            bits.append(str(_unwrap(d.get("telescope_name", "?"))))
            bits.append(f"{_unwrap(d.get('total_duration_human', '?'))} on sky")
        except Exception:
            pass
    try:
        from casatools import table

        tb = table()
        tb.open(str(ms_path / "ANTENNA"))
        bits.insert(1, f"{tb.nrows()} antennas")
        tb.close()
        tb.open(str(ms_path / "SPECTRAL_WINDOW"))
        nchan = tb.getcol("NUM_CHAN")
        freqs = [tb.getcell("REF_FREQUENCY", i) / 1e9 for i in range(tb.nrows())]
        tb.close()
        span = f"{min(freqs):.3f}–{max(freqs):.3f} GHz" if freqs else "?"
        bits.append(f"{len(nchan)} spw × {int(nchan[0])} ch · {span}")
    except Exception:
        pass
    (run_dir / "cache" / "instrument.txt").write_text(" · ".join(b for b in bits if b) + "\n")


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


def harvest(run_dir: Path, st: state_mod.RunState, whitelist: dict, ex) -> dict[str, Any]:
    """Turn a finished job into a step record plus measurements.json."""
    pending = st.pending
    step_dir = Path(pending.step_dir)
    rc = ex.exit_code(step_dir)
    result = "OK" if rc == 0 else "FAILED"

    entry = whitelist["tools"][pending.tool]
    measurements = _run_probe(entry, Path(st.active_ms), step_dir) if result == "OK" else {}
    (step_dir / "measurements.json").write_text(json.dumps(measurements, indent=2) + "\n")
    _record_flag_fraction(run_dir, st.active_ms, measurements)

    decision = json.loads(state_mod.decision_path(run_dir, pending.step).read_text())
    inner = decision.get("decision", decision)

    record = {
        "step": pending.step,
        "tool": pending.tool,
        "params": inner.get("params", {}),
        "result": result,
        "exit_code": rc,
        "headline": _headline(measurements),
        "duration": _duration(pending.submitted_utc),
        "rationale": inner.get("rationale", ""),
        "step_dir": str(step_dir),
    }
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
) -> tuple[Path, Path]:
    """Ask the tool to write its script. Returns (step_dir, script_path).

    ms_path, workdir and execute are the driver's to set. The model supplies
    the science parameters and nothing else, so a generated script is always
    rooted in this run's step directory.
    """
    tool = inner["tool"]
    step_dir = state_mod.step_dir(run_dir, st.step, tool)
    step_dir.mkdir(parents=True, exist_ok=True)
    module = importlib.import_module(whitelist["tools"][tool]["module"])

    # Not every tool takes every driver-owned parameter: ms_flag_caltable acts
    # on a caltable and has no ms_path at all. Supply the intersection, so a
    # tool that does not want one is not handed it.
    accepted = set(inspect.signature(module.run).parameters)
    owned = {"ms_path": st.active_ms, "workdir": str(step_dir), "execute": False}
    kwargs = {k: v for k, v in owned.items() if k in accepted}

    resp = module.run(**kwargs, **inner.get("params", {}))
    return step_dir, Path(_unwrap(resp["data"]["script_path"]))


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

    _refresh_ms_cache(run_dir, Path(st.active_ms))

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
            active_ms=Path(st.active_ms),
            whitelist=whitelist,
            recipe=recipes[st.recipe],
            steps=_step_records(run_dir),
            tools_done=st.tools_done,
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
            inner = validate_mod.validate(
                decision_file, whitelist, run_dir, Path(st.active_ms), st.tools_done
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
                step_dir, script = generate_script(run_dir, st, whitelist, inner)
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
        "active_ms": st.active_ms,
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
    job_id = ex.submit(script, step_dir, f"{st.run_id}-{st.step:03d}")
    st.pending = state_mod.Pending(
        job_id=job_id,
        step=st.step,
        tool=tool,
        submitted_utc=_now(),
        step_dir=str(step_dir),
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
            f"Create one with: analyst-driver init --run-id NAME --ms PATH --goal TEXT"
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
    print(f"  active MS : {st.active_ms}")
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
    i.add_argument("--ms", required=True)
    i.add_argument("--goal", required=True)
    i.add_argument("--recipe", default="vla_continuum")
    i.add_argument("--force", action="store_true")
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
