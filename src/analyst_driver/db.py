"""Run index for the analyst driver (PLAN.md step 3).

The filesystem is the truth; this database is an index. Every row is
reconstructable from the journal files under the run root:

    <run_root>/<run_key>/run.json          -- the run and its run-level metrics
    <run_root>/<run_key>/turns/NNNN.json   -- one record per turn

A turn record is written at job submission (state "submitted") and rewritten
at completion (state "complete"), so a job submitted just before a crash is
still visible to ``rebuild``. Every journal write is write-to-temp-then-rename,
so a file on disk is always either the old version or the new one.

Exactly one code path turns a journal record into rows: ``_sync_run`` and
``_sync_turn``. The live write path and ``rebuild`` both call them, so the two
cannot drift apart. ``rebuild`` replays the journal; it never re-measures the
products, because a retried stage overwrites the previous attempt's output.

Artifact identity: every kind gets size and mtime. Small kinds (caltable,
script, plot) get a content hash, ``sha256:`` over the bytes — for a CASA
product directory the walk includes each file's relative path, so content
and layout are distinguishable. Large kinds (``ms``, ``image``) get a
metadata digest instead, ``meta:`` over name + size + mtime: it identifies
the producing attempt without reading gigabytes, but cannot prove content.

SQLite via stdlib sqlite3, WAL mode, 30 s busy timeout. DDL is ANSI-portable
except where marked PORTABILITY.

The run root may sit on local disk or on NFS, so nothing here may assume the
database is reliable. WAL mode relies on shared memory and on POSIX locks that
NFS does not provide dependably, which makes a stale or damaged index a
foreseeable state rather than a bug. It is survivable only because the journal
files are the truth: ``rebuild`` reconstructs every row from them, and the live
write path and ``rebuild`` share ``_sync_run``/``_sync_turn``, so the two
cannot drift. The queries the CLI runs over the index — which runs are active,
which run holds a given MS — are therefore repairable by ``rebuild`` and never
by hand. Run ownership avoids a file lock for the same NFS reason; see
``owner.py``.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

#: Kinds too large to read for a content hash. They get a metadata digest
#: ("meta:" over name, size, mtime) — enough to say which attempt produced
#: the file, since a rewrite changes mtime; it cannot prove bytes unchanged.
META_HASH_KINDS = frozenset({"ms", "image"})

#: Valid turn outcomes. ``None`` means the turn is submitted, not finished.
OUTCOMES = frozenset({"accepted", "retried", "failed"})

#: Valid run statuses. ``active`` is the only one that means work remains;
#: every other value is terminal and the driver skips it.
#:
#: ``stopped`` exists because a reduction the model ended must be
#: distinguishable from one still in progress. Without it both read
#: ``active``, a bare ``run`` re-drives it, and a resume gate has nothing to
#: read. It is deliberately not called ``completed``: the driver cannot
#: confirm the reduction actually succeeded, only that the model set
#: done=true and stopped submitting turns — read decision.notes on the last
#: turn for what the model actually meant.
RUN_STATUSES = frozenset({"active", "stopped", "needs_human", "failed"})
TERMINAL_STATUSES = frozenset({"stopped", "needs_human", "failed"})

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,      -- PORTABILITY: SQLite rowid alias
    run_key TEXT NOT NULL UNIQUE,
    started_at TEXT NOT NULL,
    -- What the user handed us: an ASDM before import, an MS after it, and the
    -- stable identity of the run either way. ms_path is empty until an MS
    -- exists, so it cannot serve as that identity.
    input_path TEXT NOT NULL DEFAULT '',
    ms_path TEXT NOT NULL,
    workdir TEXT NOT NULL,
    telescope TEXT,
    backend TEXT,
    executor TEXT,
    status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY,      -- PORTABILITY: SQLite rowid alias
    run_id INTEGER NOT NULL REFERENCES runs(id),
    ordinal INTEGER NOT NULL,
    stage TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    state TEXT NOT NULL,
    outcome TEXT,
    brief TEXT,
    decision TEXT,
    model TEXT,
    -- Four separate counts, not one. They bill at different rates, so a single
    -- "input tokens" number cannot support a cost figure. tokens_in used to
    -- hold usage.input_tokens alone; on a cached conversation that is only the
    -- uncached tail (12-26 tokens a turn on the G55 run) while the cache
    -- counts carry everything real, so any cost taken from it undercounted
    -- input by 17,899x.
    tokens_in INTEGER,              -- uncached input
    tokens_cache_read INTEGER,      -- read from the prompt cache
    tokens_cache_creation INTEGER,  -- written to the prompt cache
    tokens_out INTEGER,
    wall_time_s REAL,
    UNIQUE (run_id, ordinal)
);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,      -- PORTABILITY: SQLite rowid alias
    turn_id INTEGER NOT NULL REFERENCES turns(id),
    executor TEXT NOT NULL,
    handle TEXT,
    submitted_at TEXT,
    finished_at TEXT,
    exit_code INTEGER,
    log_paths TEXT
);
CREATE TABLE IF NOT EXISTS artifacts (
    id INTEGER PRIMARY KEY,      -- PORTABILITY: SQLite rowid alias
    turn_id INTEGER NOT NULL REFERENCES turns(id),
    path TEXT NOT NULL,
    kind TEXT NOT NULL,
    size INTEGER,
    checksum TEXT,
    mtime REAL
);
CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY,      -- PORTABILITY: SQLite rowid alias
    run_id INTEGER NOT NULL REFERENCES runs(id),
    turn_id INTEGER REFERENCES turns(id),
    name TEXT NOT NULL,
    value REAL,
    unit TEXT,
    flag TEXT
);
"""


def utcnow_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_run_key(ms_path: str | os.PathLike, now: datetime | None = None) -> str:
    """``20260827T142530Z-3c286_b6-a91f`` — timestamp, MS name, short hash.

    The hash is over the absolute MS path, so two runs on same-named MSs in
    different directories cannot collide.
    """
    if now is None:
        now = datetime.now(UTC)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    p = Path(ms_path)
    name = p.name
    if name.lower().endswith(".ms"):
        name = name[:-3]
    name = name.lower().replace(" ", "_")
    digest = hashlib.sha256(str(p.absolute()).encode()).hexdigest()[:4]
    return f"{stamp}-{name}-{digest}"


def _hash_tree(path: Path) -> str:
    """SHA-256 of a file, or of a directory tree including each relative path."""
    h = hashlib.sha256()
    if path.is_dir():
        files = sorted(f for f in path.rglob("*") if f.is_file())
        for f in files:
            h.update(str(f.relative_to(path)).encode())
            h.update(b"\x00")
            with open(f, "rb") as fh:
                while chunk := fh.read(1 << 20):
                    h.update(chunk)
            h.update(b"\x00")
    else:
        with open(path, "rb") as fh:
            while chunk := fh.read(1 << 20):
                h.update(chunk)
    return h.hexdigest()


def measure_artifact(path: str | os.PathLike, kind: str) -> dict:
    """Measure one artifact for the record.

    An absent path measures as absent rather than raising — a failed job
    legitimately produces nothing.
    """
    p = Path(path)
    rec = {"path": str(p), "kind": kind, "size": None, "mtime": None, "checksum": None}
    if not p.exists():
        return rec
    if p.is_dir():
        rec["size"] = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    else:
        rec["size"] = p.stat().st_size
    rec["mtime"] = p.stat().st_mtime
    if kind in META_HASH_KINDS:
        h = hashlib.sha256(f"{p.name}\x00{rec['size']}\x00{rec['mtime']}".encode())
        rec["checksum"] = "meta:" + h.hexdigest()
    else:
        rec["checksum"] = "sha256:" + _hash_tree(p)
    return rec


class DriverDB:
    """The journal writer and the index over it."""

    def __init__(self, run_root: str | os.PathLike, db_path: str | os.PathLike | None = None):
        self.run_root = Path(run_root)
        self.run_root.mkdir(parents=True, exist_ok=True)
        self.db_path = Path(db_path) if db_path else self.run_root / "driver.sqlite3"
        self.conn = sqlite3.connect(self.db_path, timeout=30.0)
        self.conn.execute("PRAGMA journal_mode=WAL")  # PORTABILITY: SQLite pragma
        self.conn.executescript(_SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """Add columns a database predating them cannot get from CREATE TABLE.

        ``CREATE TABLE IF NOT EXISTS`` is a no-op on an existing table, so a
        new column needs an explicit ALTER. Every column added here must have
        a default, because the rows already there carry no value for it.
        """
        have = {r[1] for r in self.conn.execute("PRAGMA table_info(runs)")}
        if "input_path" not in have:
            self.conn.execute("ALTER TABLE runs ADD COLUMN input_path TEXT NOT NULL DEFAULT ''")
            self.conn.execute("UPDATE runs SET input_path = ms_path WHERE input_path = ''")

        # NULL, not 0, for rows written before these columns existed: 0 would
        # read as "measured, and it was nothing", which is the same mistake the
        # columns exist to correct.
        have = {r[1] for r in self.conn.execute("PRAGMA table_info(turns)")}
        for column in ("tokens_cache_read", "tokens_cache_creation"):
            if column not in have:
                self.conn.execute(f"ALTER TABLE turns ADD COLUMN {column} INTEGER DEFAULT NULL")

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------------ paths

    def _run_dir(self, run_key: str) -> Path:
        return self.run_root / run_key

    def _run_json(self, run_key: str) -> Path:
        return self._run_dir(run_key) / "run.json"

    def _turn_json(self, run_key: str, ordinal: int) -> Path:
        return self._run_dir(run_key) / "turns" / f"{ordinal:04d}.json"

    @staticmethod
    def _write_json(path: Path, record: dict) -> None:
        """Atomic: write to a temp file in the same directory, then rename."""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, "w") as fh:
            json.dump(record, fh, indent=1, sort_keys=True)
        os.replace(tmp, path)

    @staticmethod
    def _read_json(path: Path) -> dict:
        with open(path) as fh:
            return json.load(fh)

    # -------------------------------------------------------------- live path

    def create_run(
        self,
        run_key: str,
        *,
        ms_path: str,
        workdir: str,
        input_path: str | None = None,
        telescope: str | None = None,
        backend: str | None = None,
        executor: str | None = None,
        started_at: str | None = None,
        status: str = "active",
    ) -> dict:
        record = {
            "run_key": run_key,
            "started_at": started_at or utcnow_iso(),
            "input_path": input_path if input_path is not None else ms_path,
            "ms_path": ms_path,
            "workdir": workdir,
            "telescope": telescope,
            "backend": backend,
            "executor": executor,
            "status": status,
            "metrics": [],
        }
        self._write_json(self._run_json(run_key), record)
        self._sync_run(record)
        return record

    def set_run_status(self, run_key: str, status: str) -> None:
        if status not in RUN_STATUSES:
            raise ValueError(f"status must be one of {sorted(RUN_STATUSES)}, got {status!r}")
        record = self._read_json(self._run_json(run_key))
        record["status"] = status
        self._write_json(self._run_json(run_key), record)
        self._sync_run(record)

    def set_run_ms_path(self, run_key: str, ms_path: str) -> None:
        """Record the MS once it exists — after an import turn, typically."""
        record = self._read_json(self._run_json(run_key))
        record["ms_path"] = ms_path
        self._write_json(self._run_json(run_key), record)
        self._sync_run(record)

    def record_run_metric(
        self,
        run_key: str,
        name: str,
        value: float | None,
        unit: str | None = None,
        flag: str | None = None,
    ) -> None:
        """A run-level metric lives in run.json, so ``rebuild`` cannot drop it."""
        record = self._read_json(self._run_json(run_key))
        record["metrics"].append({"name": name, "value": value, "unit": unit, "flag": flag})
        self._write_json(self._run_json(run_key), record)
        self._sync_run(record)

    def find_runs_by_ms(
        self, ms_path: str | os.PathLike, *, statuses: tuple[str, ...] = ("active",)
    ) -> list[dict]:
        """Runs on this MS with one of these statuses, oldest first.

        Matched on the absolute path, because ``make_run_key`` embeds a
        timestamp: two registrations of the same MS get different keys, so the
        path is the only stable identity a caller can present.
        """
        target = str(Path(ms_path).absolute())
        placeholders = ",".join("?" for _ in statuses)
        # Either path matches: before import the caller knows only the ASDM,
        # after it they may well name the MS instead.
        rows = self.conn.execute(
            "SELECT run_key, status, started_at, workdir, executor FROM runs"
            f" WHERE (input_path = ? OR ms_path = ?) AND status IN ({placeholders})"  # noqa: S608
            " ORDER BY started_at, run_key",
            (target, target, *statuses),
        ).fetchall()
        return [
            {"run_key": r[0], "status": r[1], "started_at": r[2], "workdir": r[3], "executor": r[4]}
            for r in rows
        ]

    def next_ordinal(self, run_key: str) -> int:
        row = self.conn.execute(
            "SELECT MAX(t.ordinal) FROM turns t JOIN runs r ON t.run_id = r.id"
            " WHERE r.run_key = ?",  # PORTABILITY: ? placeholders
            (run_key,),
        ).fetchone()
        return (row[0] or 0) + 1

    def record_turn(
        self,
        run_key: str,
        ordinal: int,
        *,
        stage: str,
        brief: str | None = None,
        decision: dict | None = None,
        model: str | None = None,
        tokens_in: int | None = None,
        tokens_cache_read: int | None = None,
        tokens_cache_creation: int | None = None,
        tokens_out: int | None = None,
        jobs: list[dict] | None = None,
        extras: dict | None = None,
    ) -> dict:
        """Write the turn record at job submission (state ``submitted``).

        Written before the rows are inserted, so a crash between the two
        loses nothing. ``extras`` holds journal-only keys (citations, tool
        transcript, stop reason) — kept in the file, never in a column.
        """
        attempt = (
            1
            + self.conn.execute(
                "SELECT COUNT(*) FROM turns t JOIN runs r ON t.run_id = r.id"
                " WHERE r.run_key = ? AND t.stage = ? AND t.ordinal < ?",
                (run_key, stage, ordinal),
            ).fetchone()[0]
        )
        record = {
            "run_key": run_key,
            "ordinal": ordinal,
            "stage": stage,
            "attempt": attempt,
            "state": "submitted",
            "outcome": None,
            "brief": brief,
            "decision": decision,
            "model": model,
            "tokens_in": tokens_in,
            "tokens_cache_read": tokens_cache_read,
            "tokens_cache_creation": tokens_cache_creation,
            "tokens_out": tokens_out,
            "wall_time_s": None,
            "jobs": list(jobs or []),
            "artifacts": [],
            "metrics": [],
        }
        if extras:
            record.update(extras)
        self._write_json(self._turn_json(run_key, ordinal), record)
        self._sync_turn(record)
        return record

    def complete_turn(
        self,
        run_key: str,
        ordinal: int,
        *,
        outcome: str,
        jobs: list[dict] | None = None,
        artifacts: list[dict] | None = None,
        metrics: list[dict] | None = None,
        wall_time_s: float | None = None,
        extras: dict | None = None,
    ) -> dict:
        """Rewrite the turn record at completion (state ``complete``)."""
        if outcome not in OUTCOMES:
            raise ValueError(f"outcome must be one of {sorted(OUTCOMES)}, got {outcome!r}")
        record = self._read_json(self._turn_json(run_key, ordinal))
        record["state"] = "complete"
        record["outcome"] = outcome
        if jobs is not None:
            record["jobs"] = jobs
        if artifacts is not None:
            record["artifacts"] = artifacts
        if metrics is not None:
            record["metrics"] = metrics
        record["wall_time_s"] = wall_time_s
        if extras:
            record.update(extras)
        self._write_json(self._turn_json(run_key, ordinal), record)
        self._sync_turn(record)
        if outcome == "accepted":
            self._demote_previous_accepted(run_key, record["stage"], ordinal)
        return record

    def set_turn_outcome(self, run_key: str, ordinal: int, outcome: str) -> None:
        if outcome not in OUTCOMES:
            raise ValueError(f"outcome must be one of {sorted(OUTCOMES)}, got {outcome!r}")
        record = self._read_json(self._turn_json(run_key, ordinal))
        record["outcome"] = outcome
        self._write_json(self._turn_json(run_key, ordinal), record)
        self._sync_turn(record)
        if outcome == "accepted":
            self._demote_previous_accepted(run_key, record["stage"], ordinal)

    def _demote_previous_accepted(self, run_key: str, stage: str, keep_ordinal: int) -> None:
        """At most one accepted turn per (run, stage).

        The demotion rewrites the demoted turn's journal file too — the
        filesystem is the truth, so an index-only demotion would be lost by
        ``rebuild``.
        """
        rows = self.conn.execute(
            "SELECT t.ordinal FROM turns t JOIN runs r ON t.run_id = r.id"
            " WHERE r.run_key = ? AND t.stage = ? AND t.outcome = 'accepted'"
            " AND t.ordinal <> ?",
            (run_key, stage, keep_ordinal),
        ).fetchall()
        for (ordinal,) in rows:
            record = self._read_json(self._turn_json(run_key, ordinal))
            record["outcome"] = "retried"
            self._write_json(self._turn_json(run_key, ordinal), record)
            self._sync_turn(record)

    # ---------------------------------------------- the one row-insert path

    def _sync_run(self, record: dict) -> None:
        """Make the runs row and run-level metric rows match one run.json."""
        cur = self.conn.cursor()
        row = cur.execute("SELECT id FROM runs WHERE run_key = ?", (record["run_key"],)).fetchone()
        if row:
            run_id = row[0]
            cur.execute(
                "UPDATE runs SET started_at=?, input_path=?, ms_path=?, workdir=?,"
                " telescope=?, backend=?, executor=?, status=? WHERE id=?",
                (
                    record["started_at"],
                    record.get("input_path") or record["ms_path"],
                    record["ms_path"],
                    record["workdir"],
                    record["telescope"],
                    record["backend"],
                    record["executor"],
                    record["status"],
                    run_id,
                ),
            )
        else:
            cur.execute(
                "INSERT INTO runs (run_key, started_at, input_path, ms_path, workdir,"
                " telescope, backend, executor, status) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    record["run_key"],
                    record["started_at"],
                    record.get("input_path") or record["ms_path"],
                    record["ms_path"],
                    record["workdir"],
                    record["telescope"],
                    record["backend"],
                    record["executor"],
                    record["status"],
                ),
            )
            run_id = cur.lastrowid
        cur.execute("DELETE FROM metrics WHERE run_id = ? AND turn_id IS NULL", (run_id,))
        for m in record["metrics"]:
            cur.execute(
                "INSERT INTO metrics (run_id, turn_id, name, value, unit, flag)"
                " VALUES (?,NULL,?,?,?,?)",
                (run_id, m["name"], m["value"], m.get("unit"), m.get("flag")),
            )
        self.conn.commit()

    def _sync_turn(self, record: dict) -> None:
        """Make all rows for one turn match its journal record.

        Delete-then-insert, so the live path and ``rebuild`` converge on
        identical rows from the same file.
        """
        cur = self.conn.cursor()
        row = cur.execute("SELECT id FROM runs WHERE run_key = ?", (record["run_key"],)).fetchone()
        if not row:
            raise ValueError(f"no run.json synced for run_key {record['run_key']!r}")
        run_id = row[0]
        old = cur.execute(
            "SELECT id FROM turns WHERE run_id = ? AND ordinal = ?",
            (run_id, record["ordinal"]),
        ).fetchone()
        if old:
            turn_id = old[0]
            cur.execute("DELETE FROM jobs WHERE turn_id = ?", (turn_id,))
            cur.execute("DELETE FROM artifacts WHERE turn_id = ?", (turn_id,))
            cur.execute("DELETE FROM metrics WHERE turn_id = ?", (turn_id,))
            cur.execute("DELETE FROM turns WHERE id = ?", (turn_id,))
        decision = record.get("decision")
        cur.execute(
            "INSERT INTO turns (run_id, ordinal, stage, attempt, state, outcome, brief,"
            " decision, model, tokens_in, tokens_cache_read, tokens_cache_creation,"
            " tokens_out, wall_time_s)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                run_id,
                record["ordinal"],
                record["stage"],
                record["attempt"],
                record["state"],
                record["outcome"],
                record["brief"],
                json.dumps(decision, sort_keys=True) if decision is not None else None,
                record["model"],
                record["tokens_in"],
                record["tokens_cache_read"],
                record["tokens_cache_creation"],
                record["tokens_out"],
                record["wall_time_s"],
            ),
        )
        turn_id = cur.lastrowid
        for j in record["jobs"]:
            log_paths = j.get("log_paths")
            cur.execute(
                "INSERT INTO jobs (turn_id, executor, handle, submitted_at, finished_at,"
                " exit_code, log_paths) VALUES (?,?,?,?,?,?,?)",
                (
                    turn_id,
                    j["executor"],
                    j.get("handle"),
                    j.get("submitted_at"),
                    j.get("finished_at"),
                    j.get("exit_code"),
                    json.dumps(log_paths, sort_keys=True) if log_paths is not None else None,
                ),
            )
        for a in record["artifacts"]:
            cur.execute(
                "INSERT INTO artifacts (turn_id, path, kind, size, checksum, mtime)"
                " VALUES (?,?,?,?,?,?)",
                (
                    turn_id,
                    a["path"],
                    a["kind"],
                    a.get("size"),
                    a.get("checksum"),
                    a.get("mtime"),
                ),
            )
        for m in record["metrics"]:
            cur.execute(
                "INSERT INTO metrics (run_id, turn_id, name, value, unit, flag)"
                " VALUES (?,?,?,?,?,?)",
                (run_id, turn_id, m["name"], m["value"], m.get("unit"), m.get("flag")),
            )
        self.conn.commit()

    # ---------------------------------------------------------------- rebuild

    def rebuild(self) -> dict:
        """Empty the tables and replay the journal through the same sync path.

        Replays recorded facts only; never re-measures the products (a retried
        stage overwrites the previous attempt's output on disk).
        """
        cur = self.conn.cursor()
        for table in ("metrics", "artifacts", "jobs", "turns", "runs"):
            cur.execute(f"DELETE FROM {table}")  # noqa: S608 - fixed table names
        self.conn.commit()
        n_runs = n_turns = 0
        for run_json in sorted(self.run_root.glob("*/run.json")):
            self._sync_run(self._read_json(run_json))
            n_runs += 1
            for turn_json in sorted(run_json.parent.glob("turns/*.json")):
                self._sync_turn(self._read_json(turn_json))
                n_turns += 1
        return {"runs": n_runs, "turns": n_turns}

    # ------------------------------------------------------------------- dump

    def dump(self) -> dict:
        """Every table keyed by natural identity, surrogate ids excluded.

        Surrogate ids differ between a live history (delete-then-insert
        inflates rowids) and a rebuild, so equality is defined over this.
        """
        q = self.conn.execute
        return {
            "runs": q(
                "SELECT run_key, started_at, input_path, ms_path, workdir, telescope,"
                " backend, executor, status FROM runs ORDER BY run_key"
            ).fetchall(),
            "turns": q(
                "SELECT r.run_key, t.ordinal, t.stage, t.attempt, t.state, t.outcome,"
                " t.brief, t.decision, t.model, t.tokens_in, t.tokens_cache_read,"
                " t.tokens_cache_creation, t.tokens_out, t.wall_time_s"
                " FROM turns t JOIN runs r ON t.run_id = r.id"
                " ORDER BY r.run_key, t.ordinal"
            ).fetchall(),
            "jobs": q(
                "SELECT r.run_key, t.ordinal, j.executor, j.handle, j.submitted_at,"
                " j.finished_at, j.exit_code, j.log_paths"
                " FROM jobs j JOIN turns t ON j.turn_id = t.id"
                " JOIN runs r ON t.run_id = r.id"
                " ORDER BY r.run_key, t.ordinal, j.handle, j.submitted_at"
            ).fetchall(),
            "artifacts": q(
                "SELECT r.run_key, t.ordinal, a.path, a.kind, a.size, a.checksum, a.mtime"
                " FROM artifacts a JOIN turns t ON a.turn_id = t.id"
                " JOIN runs r ON t.run_id = r.id"
                " ORDER BY r.run_key, t.ordinal, a.path"
            ).fetchall(),
            "metrics": q(
                "SELECT r.run_key, t.ordinal, m.name, m.value, m.unit, m.flag"
                " FROM metrics m JOIN runs r ON m.run_id = r.id"
                " LEFT JOIN turns t ON m.turn_id = t.id"
                " ORDER BY r.run_key, m.turn_id IS NULL, t.ordinal, m.name, m.value"
            ).fetchall(),
        }
