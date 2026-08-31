"""analyst-driver CLI (PLAN.md step 8): init, step, run, status, rebuild.

``config.toml`` lives in the run root and is data, not code — operational
settings only, never science. See PLAN.md "Files".
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
        db, backend, executor,
        max_turns=int(driver_cfg.get("max_turns", 100)),
        poll_interval=float(driver_cfg.get("poll_interval", 60)),
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


def cmd_init(args: argparse.Namespace, cfg: dict[str, Any], db: DriverDB) -> int:
    ms_path = str(Path(args.ms).absolute())
    workdir = str(Path(args.workdir).absolute())
    run_key = make_run_key(ms_path)
    db.create_run(
        run_key,
        ms_path=ms_path,
        workdir=workdir,
        telescope=args.telescope,
        backend=(cfg.get("backend") or {}).get("kind", "claude"),
        executor=(cfg.get("executor") or {}).get("kind", "local"),
    )
    print(run_key)
    return 0


def cmd_step(args: argparse.Namespace, cfg: dict[str, Any], db: DriverDB) -> int:
    loop = build_loop(cfg, db)
    result = loop.step(args.run)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["action"] in ("completed", "waiting", "skipped") else 1


def cmd_run(args: argparse.Namespace, cfg: dict[str, Any], db: DriverDB) -> int:
    loop = build_loop(cfg, db)
    keys = [args.run] if args.run else _active_run_keys(db)
    if not keys:
        print("no active runs", file=sys.stderr)
        return 1
    results = loop.run_all(keys)
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

    p = sub.add_parser("init", help="register a new run")
    p.add_argument("--ms", required=True)
    p.add_argument("--workdir", required=True)
    p.add_argument("--telescope", default=None)
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("step", help="advance one run by one turn (waits for the job)")
    p.add_argument("--run", required=True, help="run_key")
    p.set_defaults(func=cmd_step)

    p = sub.add_parser("run", help="advance runs until done or needs_human")
    p.add_argument("--run", default=None, help="one run_key; default all active runs")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("status", help="list runs and their latest turn")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("rebuild", help="reconstruct the database from the journal")
    p.set_defaults(func=cmd_rebuild)

    args = parser.parse_args(argv)
    config_path = Path(args.config)
    cfg = load_config(config_path) if config_path.exists() else {}
    db = _open_db(cfg, config_path.absolute())
    try:
        return args.func(args, cfg, db)
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
