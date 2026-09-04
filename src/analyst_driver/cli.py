"""analyst-driver CLI (PLAN.md step 8): init, step, run, status, rebuild.

``config.toml`` lives in the run root and is data, not code — operational
settings only, never science. See PLAN.md "Files".

The verbs (user decision, 2026-08-31):

- ``init``  scaffolds ``config.toml`` and nothing else. It registers no run.
            Nothing else needs scaffolding: ``DriverDB.__init__`` creates the
            run root and the database on first open.
- ``run``   registers a run for an ASDM or an MS when none is open on it, then
            drives it. Bare ``run`` drives every active run, which is the
            fan-out mode. The dataset stays on the command line, not in
            ``config.toml``, so one config can serve many runs.
- ``step``  one turn of one run, for debugging.

``run`` and ``step`` both take the run's ownership record before they work and
release it afterwards. See ``owner.py`` for what "alive", "dead" and "unknown"
mean there, and for why a lock file is not used.
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path
from typing import Any

from analyst_driver.backends import StubBackend, make_backend
from analyst_driver.db import DriverDB, make_run_key
from analyst_driver.executors import make_executor
from analyst_driver.loop import Loop
from analyst_driver.owner import clear_owner, probe_owner, read_owner, write_owner

#: Written verbatim by ``init``. Every live value equals the code default, so
#: an unedited template behaves exactly as no file did. Commented keys are
#: examples: uncommenting one the chosen backend or executor does not accept
#: is a hard error, because the table is expanded into a constructor
#: (``make_backend(kind, **cfg)``, ``SlurmConfig(**cfg)``).
DEFAULT_CONFIG = """# analyst-driver configuration.
#
# Operational settings only — which queue, as which user, driven by which
# binary. Never science: solint, thresholds and reference antennas come from
# the radio-interferometry skill, and a second copy here would drift.

[driver]
# Where runs, their journals and driver.sqlite3 live. A relative path resolves
# against this file's directory.
run_root = "runs"
# Turns before the run stops and asks for a human.
max_turns = 100
# Seconds between polls while waiting for a job.
poll_interval = 60
# Declares this run's goal in plain language — appended to every turn's
# brief. The model reads it like any other instruction; the loop never
# parses or checks it. Only the model decides {"done": true}, still.
# Examples:
#   scope = "calibration only"
#   scope = "calibration + imaging"
#   scope = "full-Stokes calibration + imaging"
#   scope = "calibration + imaging, prefer MT-MFS nterms=2, use awproject"
scope = ""

[backend]
# claude | opencode | codex | stub
kind = "claude"
# `claude -p` is non-interactive: nobody can answer a permission prompt, so a
# tool that is not listed here is DENIED and the turn fails. The three MCP
# servers are the driver's whole purpose. Read/Glob/Grep let it consult the
# skill and read logs; Skill loads the skill itself.
# The model writes a script and the LOOP executes it. A turn that ran CASA
# itself would leave no job id, no exit code and no artifact checksum in the
# journal — the run becomes unauditable, which is the point of the loop.
#
# allowed_tools PRE-APPROVES. It does not remove anything: the 2026-08-31 G55
# run made 101 Bash calls across 16 turns with Bash absent from this list.
# disallowed_tools is what actually removes a tool, and ClaudeBackend checks
# the harness's own system/init event against it on every turn, because a flag
# that is silently ignored looks exactly like a flag that works.
allowed_tools = [
  "mcp__ms-inspect",
  "mcp__ms-modify",
  "mcp__ms-create",
  "Read",
  "Glob",
  "Grep",
  "Skill",
]
# Listed here for visibility only — this IS the code default
# (backends.DEFAULT_DISALLOWED_TOOLS), so deleting these lines changes nothing
# and a config written before the ban existed is still protected. Set it to []
# to turn the ban off deliberately.
disallowed_tools = [
  "Bash",
  "Write",
  "Edit",
  "NotebookEdit",
  "Task",
  "WebFetch",
  "WebSearch",
]
# cmd = "claude"
# model = "claude-opus-5"
# mcp_config = "/path/to/.mcp.json"
# timeout = 1800

[executor]
# local | slurm | htcondor
kind = "local"
# runner = "python3"

# SLURM: replace the [executor] block above with this one.
# [executor]
# kind = "slurm"
# account = ""
# partition = ""
# cpus_per_task = 8
# mem = "60G"
# time = "08:00:00"
# modules = []
"""


def load_config(path: str | Path) -> dict[str, Any]:
    with open(path, "rb") as fh:
        return tomllib.load(fh)


def build_loop(cfg: dict[str, Any], db: DriverDB) -> Loop:
    backend_cfg = dict(cfg.get("backend") or {"kind": "claude"})
    backend_kind = backend_cfg.pop("kind")
    if backend_kind == "stub":
        backend = StubBackend(backend_cfg.get("responses") or [])
    else:
        backend = make_backend(backend_kind, **backend_cfg)

    executor_cfg = dict(cfg.get("executor") or {"kind": "local"})
    executor_kind = executor_cfg.pop("kind")
    if executor_kind == "slurm":
        from ms_modify.slurm import SlurmConfig

        executor = make_executor("slurm", config=SlurmConfig(**executor_cfg))
    else:
        executor = make_executor(executor_kind, **executor_cfg)

    driver_cfg = cfg.get("driver") or {}
    return Loop(
        db,
        backend,
        executor,
        max_turns=int(driver_cfg.get("max_turns", 100)),
        poll_interval=float(driver_cfg.get("poll_interval", 60)),
        scope=driver_cfg.get("scope", ""),
    )


def _open_db(cfg: dict[str, Any], config_path: Path) -> DriverDB:
    run_root = Path((cfg.get("driver") or {}).get("run_root", "runs"))
    if not run_root.is_absolute():
        run_root = config_path.parent / run_root
    return DriverDB(run_root)


def _active_run_keys(db: DriverDB) -> list[str]:
    return [
        row[0]
        for row in db.conn.execute(
            "SELECT run_key FROM runs WHERE status = 'active' ORDER BY run_key"
        ).fetchall()
    ]


# ------------------------------------------------------------------ ownership


def _job_state(loop: Loop, owner: dict) -> str | None:
    """The executor's answer for the recorded job, or None if we cannot ask.

    Only SLURM is asked. A local job cannot outlive its driver, so under
    ``executor = "local"`` the driver's own liveness is the whole answer.
    """
    job_id = owner.get("job_id")
    if not job_id or owner.get("executor") != "slurm":
        return None
    try:
        return loop.executor.poll({"job_id": job_id, "executor": "slurm"})
    except Exception:  # sacct absent or cluster unreachable — unknown, not dead
        return None


def _claim(loop: Loop, db: DriverDB, run_key: str, *, resume: bool) -> tuple[int, str]:
    """Take ownership of a run, or say why not. ``(0, "")`` means taken."""
    run_dir = db._run_dir(run_key)
    owner = read_owner(run_dir)
    probe = probe_owner(owner, job_state=_job_state(loop, owner or {}))

    if probe["driver"] == "alive":
        # Proof that another driver holds it. Two drivers on one MS corrupt
        # the data, so this refusal is not overridable.
        return 2, (
            f"{run_key}: a driver is already running this run — {probe['detail']}."
            " Stop it first. --resume does not override this."
        )

    if probe["driver"] in ("dead", "unknown") and not resume:
        hint = ""
        if probe["job"] == "alive":
            hint = (
                f" Its {owner.get('executor')} job {owner.get('job_id')} is still"
                f" {probe['job_state']}; --resume adopts it instead of resubmitting."
            )
        return 3, (
            f"{run_key}: interrupted run — {probe['detail']}.{hint} Pass --resume to take it over."
        )

    row = db.conn.execute("SELECT executor FROM runs WHERE run_key = ?", (run_key,)).fetchone()
    write_owner(run_dir, executor=(row[0] if row else None) or "local")
    return 0, ""


# -------------------------------------------------------------------- commands


def cmd_init(args: argparse.Namespace, cfg: dict[str, Any], config_path: Path) -> int:
    if config_path.exists():
        print(f"{config_path} already exists; nothing to do", file=sys.stderr)
        return 0
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(DEFAULT_CONFIG)
    print(f"wrote {config_path}")
    print("Edit it, then start a run with:", file=sys.stderr)
    print("  analyst-driver run --ms <path.ms> --workdir <path>", file=sys.stderr)
    return 0


def _resolve_run(
    args: argparse.Namespace, cfg: dict[str, Any], db: DriverDB
) -> tuple[list[str], int, str]:
    """Which runs to drive: an explicit key, an MS, or every active run."""
    if args.run:
        return [args.run], 0, ""

    if args.input or args.workdir:
        if not (args.input and args.workdir):
            return [], 1, "--input and --workdir must be given together"
        input_path = str(Path(args.input).absolute())
        open_runs = db.find_runs_by_ms(input_path)
        if len(open_runs) > 1:
            keys = ", ".join(r["run_key"] for r in open_runs)
            return (
                [],
                1,
                (
                    f"{len(open_runs)} active runs already exist on this MS: {keys}."
                    " Name one with --run, or close the others."
                ),
            )
        if open_runs:
            return [open_runs[0]["run_key"]], 0, ""
        # An ASDM is a legitimate starting point: ms_path stays empty until an
        # import turn writes an MS, and the loop learns it from that turn's
        # "ms" output. Registering against a path that is not yet an MS is the
        # whole point of keeping input_path separate.
        is_ms = (Path(input_path) / "table.info").exists()
        run_key = make_run_key(input_path)
        db.create_run(
            run_key,
            input_path=input_path,
            ms_path=input_path if is_ms else "",
            workdir=str(Path(args.workdir).absolute()),
            telescope=args.telescope,
            backend=(cfg.get("backend") or {}).get("kind", "claude"),
            executor=(cfg.get("executor") or {}).get("kind", "local"),
        )
        print(run_key)
        return [run_key], 0, ""

    keys = _active_run_keys(db)
    if not keys:
        return [], 1, "no active runs"
    return keys, 0, ""


def cmd_step(args: argparse.Namespace, cfg: dict[str, Any], db: DriverDB) -> int:
    loop = build_loop(cfg, db)
    code, msg = _claim(loop, db, args.run, resume=args.resume)
    if code:
        print(msg, file=sys.stderr)
        return code
    try:
        result = loop.step(args.run)
    finally:
        clear_owner(db._run_dir(args.run))
    print(json.dumps(result, sort_keys=True))
    terminal_ok = ("completed", "waiting", "skipped", "run_completed")
    return 0 if result["action"] in terminal_ok else 1


def cmd_run(args: argparse.Namespace, cfg: dict[str, Any], db: DriverDB) -> int:
    if args.scope is not None:
        cfg = {**cfg, "driver": {**(cfg.get("driver") or {}), "scope": args.scope}}
    loop = build_loop(cfg, db)
    keys, code, msg = _resolve_run(args, cfg, db)
    if code:
        print(msg, file=sys.stderr)
        return code

    claimed: list[str] = []
    for key in keys:
        code, msg = _claim(loop, db, key, resume=args.resume)
        if code:
            print(msg, file=sys.stderr)
            for done in claimed:
                clear_owner(db._run_dir(done))
            return code
        claimed.append(key)

    try:
        results = loop.run_all(keys)
    finally:
        for key in claimed:
            clear_owner(db._run_dir(key))
    print(json.dumps(results, sort_keys=True))
    return 0


def cmd_status(args: argparse.Namespace, cfg: dict[str, Any], db: DriverDB) -> int:
    rows = db.conn.execute(
        "SELECT r.run_key, r.status, COUNT(t.id),"
        " MAX(t.ordinal)"
        " FROM runs r LEFT JOIN turns t ON t.run_id = r.id"
        " GROUP BY r.id ORDER BY r.run_key"
    ).fetchall()
    for run_key, status, n_turns, last_ordinal in rows:
        line = f"{run_key}  status={status}  turns={n_turns}"
        if last_ordinal:
            stage, outcome, state = db.conn.execute(
                "SELECT t.stage, t.outcome, t.state FROM turns t"
                " JOIN runs r ON t.run_id = r.id"
                " WHERE r.run_key = ? AND t.ordinal = ?",
                (run_key, last_ordinal),
            ).fetchone()
            line += f"  last: {stage} ({state}, outcome={outcome})"
        owner = read_owner(db._run_dir(run_key))
        if owner is not None:
            probe = probe_owner(owner)
            line += f"  owner: {probe['driver']} ({probe['detail']})"
        print(line)
    if not rows:
        print("no runs")
    return 0


def cmd_rebuild(args: argparse.Namespace, cfg: dict[str, Any], db: DriverDB) -> int:
    counts = db.rebuild()
    print(json.dumps(counts, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="analyst-driver")
    parser.add_argument("--config", default="config.toml", help="path to config.toml")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="write a default config.toml to edit")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("step", help="advance one run by one turn (waits for the job)")
    p.add_argument("--run", required=True, help="run_key")
    p.add_argument("--resume", action="store_true", help="take over an interrupted run")
    p.set_defaults(func=cmd_step)

    p = sub.add_parser("run", help="register a run if needed, then drive it")
    p.add_argument(
        "--input",
        "--ms",
        dest="input",
        default=None,
        help="ASDM or MS to drive; registers a run if none is open on it",
    )
    p.add_argument("--workdir", default=None, help="work directory; required with --input")
    p.add_argument("--telescope", default=None)
    p.add_argument("--run", default=None, help="one run_key; default all active runs")
    p.add_argument("--resume", action="store_true", help="take over an interrupted run")
    p.add_argument(
        "--scope", default=None, help="override config.toml's [driver] scope for this invocation"
    )
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("status", help="list runs, their latest turn and their owner")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("rebuild", help="reconstruct the database from the journal")
    p.set_defaults(func=cmd_rebuild)

    args = parser.parse_args(argv)
    config_path = Path(args.config)

    if args.func is cmd_init:
        return cmd_init(args, {}, config_path)

    if not config_path.exists():
        # A missing config used to fall through to code defaults in silence,
        # so a mistyped --config ran a full reduction under settings nobody
        # chose. It is now a stop.
        print(
            f"no config at {config_path}. Run 'analyst-driver init' to write one.",
            file=sys.stderr,
        )
        return 1

    cfg = load_config(config_path)
    db = _open_db(cfg, config_path.absolute())
    try:
        return args.func(args, cfg, db)
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
