"""Tests for analyst_driver.db — the run index.

The theme of these tests is that the database must stay *derivable*. The
rebuild round trip is the one that matters: if it fails, the index has quietly
become the source of truth.
"""

from __future__ import annotations

import json

import pytest

from analyst_driver import db


@pytest.fixture
def conn(tmp_path):
    c = db.connect(db.db_path(tmp_path))
    yield c
    c.close()


# ---------------------------------------------------------------- identity


def test_run_key_is_sortable_readable_and_unique(tmp_path):
    key = db.make_run_key(tmp_path / "3C286_Band6.ms", "2026-08-27T14:25:30Z")
    stamp, slug, digest = key.split("-")
    assert stamp == "20260827T142530Z"
    assert slug == "3c286_band6"  # extension stripped, lowercased
    assert len(digest) == 4

    # Same name in a different directory must not collide.
    other = db.make_run_key(tmp_path / "sub" / "3C286_Band6.ms", "2026-08-27T14:25:30Z")
    assert other != key

    # Later timestamp sorts later as a plain string.
    assert key < db.make_run_key(tmp_path / "3C286_Band6.ms", "2026-08-27T14:25:31Z")


def test_run_key_survives_a_name_with_no_alphanumerics(tmp_path):
    assert db.make_run_key(tmp_path / "....ms", "2026-08-27T00:00:00Z").split("-")[1] == "ms"


# ---------------------------------------------------------------- checksums


def test_checksum_covers_a_directory_tree_like_a_caltable(tmp_path):
    """CASA products are directories, so a file-only hash would be useless."""
    table = tmp_path / "gain.G"
    (table / "sub").mkdir(parents=True)
    (table / "table.dat").write_bytes(b"solutions")
    (table / "sub" / "table.f0").write_bytes(b"more")

    first = db.checksum_path(table)
    assert first and db.checksum_path(table) == first  # stable

    (table / "sub" / "table.f0").write_bytes(b"different")
    assert db.checksum_path(table) != first  # sensitive to nested content


def test_checksum_distinguishes_content_from_layout(tmp_path):
    """Same bytes under different names must not hash alike."""
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    (a / "one").write_bytes(b"x")
    (b / "two").write_bytes(b"x")
    assert db.checksum_path(a) != db.checksum_path(b)


def test_absent_artifact_measures_as_absent_rather_than_raising(tmp_path):
    """A failed job legitimately produces nothing."""
    missing = tmp_path / "never_written.G"
    assert db.checksum_path(missing) == ""
    assert db.path_size(missing) == 0
    assert db.path_mtime(missing) == ""


# ---------------------------------------------------------------- ordinals


def _run(conn, **kw):
    return db.insert_run(
        conn,
        run_key=kw.get("run_key", "20260827T000000Z-x-0000"),
        ms_path="/data/x.ms",
        workdir="/work/x",
        backend="claude",
        executor="local",
    )


def test_ordinal_is_monotonic_and_attempt_counts_per_stage(conn):
    run_id = _run(conn)
    stages = ["apply_preflag", "delay_bandpass_gain", "delay_bandpass_gain"]
    for stage in stages:
        db.insert_turn(
            conn,
            run_id=run_id,
            ordinal=db.next_ordinal(conn, run_id),
            stage=stage,
            attempt=db.next_attempt(conn, run_id, stage),
        )

    rows = db.turns_of(conn, run_id)
    assert [r["ordinal"] for r in rows] == [1, 2, 3]
    assert [r["attempt"] for r in rows] == [1, 1, 2]


def test_ordinal_is_not_reused_after_a_failed_turn(conn):
    """A wasted turn still consumes its number, so history stays unambiguous."""
    run_id = _run(conn)
    t1 = db.insert_turn(conn, run_id=run_id, ordinal=1, stage="s", attempt=1)
    db.set_turn_outcome(conn, t1, "failed")
    assert db.next_ordinal(conn, run_id) == 2


# ---------------------------------------------------------------- outcomes


def test_accepting_a_retry_demotes_the_earlier_accepted_turn(conn):
    """The question "did the gaincal work" must have exactly one answer."""
    run_id = _run(conn)
    stage = "delay_bandpass_gain"
    first = db.insert_turn(conn, run_id=run_id, ordinal=1, stage=stage, attempt=1)
    db.set_turn_outcome(conn, first, "accepted")

    second = db.insert_turn(conn, run_id=run_id, ordinal=2, stage=stage, attempt=2)
    db.set_turn_outcome(conn, second, "accepted")

    winner = db.accepted_turn(conn, run_id, stage)
    assert winner["id"] == second
    assert winner["attempt"] == 2
    assert (
        conn.execute("SELECT outcome FROM turns WHERE id = ?", (first,)).fetchone()["outcome"]
        == "retried"
    )


def test_accepting_does_not_touch_another_stage_or_another_run(conn):
    run_a = _run(conn, run_key="20260827T000000Z-a-0000")
    run_b = _run(conn, run_key="20260827T000000Z-b-0000")
    keep_stage = db.insert_turn(conn, run_id=run_a, ordinal=1, stage="other", attempt=1)
    keep_run = db.insert_turn(conn, run_id=run_b, ordinal=1, stage="gain", attempt=1)
    db.set_turn_outcome(conn, keep_stage, "accepted")
    db.set_turn_outcome(conn, keep_run, "accepted")

    t = db.insert_turn(conn, run_id=run_a, ordinal=2, stage="gain", attempt=1)
    db.set_turn_outcome(conn, t, "accepted")

    assert db.accepted_turn(conn, run_a, "other")["id"] == keep_stage
    assert db.accepted_turn(conn, run_b, "gain")["id"] == keep_run


def test_unknown_vocabulary_is_refused(conn):
    run_id = _run(conn)
    with pytest.raises(ValueError, match="unknown turn outcome"):
        db.insert_turn(conn, run_id=run_id, ordinal=1, stage="s", attempt=1, outcome="maybe")
    t = db.insert_turn(conn, run_id=run_id, ordinal=1, stage="s", attempt=1)
    with pytest.raises(ValueError, match="unknown job state"):
        db.insert_job(conn, turn_id=t, executor="local", handle="1", state="wedged")
    with pytest.raises(ValueError, match="unknown run status"):
        db.set_run_status(conn, run_id, "whoops")


# ---------------------------------------------------------------- resume


def test_unfinished_jobs_are_the_ones_to_adopt_on_restart(conn):
    """A restarted driver must recognise a live job, not resubmit it."""
    run_id = _run(conn)
    live, dead = [], []
    for i, state in enumerate(["pending", "running", "done", "failed"], start=1):
        t = db.insert_turn(conn, run_id=run_id, ordinal=i, stage=f"s{i}", attempt=1)
        db.insert_job(conn, turn_id=t, executor="slurm", handle=f"job{i}", state=state)
        (live if state in ("pending", "running") else dead).append(f"job{i}")

    assert {r["handle"] for r in db.unfinished_jobs(conn)} == set(live)
    assert dead  # guard: the negative case was actually exercised


def test_job_finish_time_is_only_set_at_a_terminal_state(conn):
    run_id = _run(conn)
    t = db.insert_turn(conn, run_id=run_id, ordinal=1, stage="s", attempt=1)
    j = db.insert_job(conn, turn_id=t, executor="local", handle="7")

    db.set_job_state(conn, j, "running")
    assert db.job_of_turn(conn, t)["finished_at"] is None

    db.set_job_state(conn, j, "done", exit_code=0)
    row = db.job_of_turn(conn, t)
    assert row["finished_at"] is not None and row["exit_code"] == 0


def test_active_runs_drives_the_step_all_sweep(conn):
    a = _run(conn, run_key="20260827T000001Z-a-0000")
    b = _run(conn, run_key="20260827T000002Z-b-0000")
    db.set_run_status(conn, b, "done", finished_at=db.utc_now())
    assert [r["id"] for r in db.active_runs(conn)] == [a]


# ---------------------------------------------------------------- rebuild
#
# These exercise write_turn_record and rebuild against each other. Testing
# either alone would prove nothing: the failure mode is the two drifting apart,
# after which the index silently becomes the source of truth.


def _dump(conn):
    """Every row of every table, minus the surrogate keys, for comparison."""
    out = {}
    for table, drop in (
        ("runs", {"id"}),
        ("turns", {"id", "run_id"}),
        ("jobs", {"id", "turn_id"}),
        ("artifacts", {"id", "turn_id"}),
        ("metrics", {"id", "run_id", "turn_id"}),
    ):
        rows = conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()  # noqa: S608
        # .keys() is not optional here: iterating a sqlite3.Row yields values.
        out[table] = [{k: r[k] for k in r.keys() if k not in drop} for r in rows]  # noqa: SIM118
    return out


def _record_turn(conn, run_root, run_key, run_id, *, stage, outcome, **extra):
    """Do what the loop does: write the file first, then insert the rows."""
    ordinal = db.next_ordinal(conn, run_id)
    rec = {
        "ordinal": ordinal,
        "stage": stage,
        "attempt": db.next_attempt(conn, run_id, stage),
        "outcome": outcome,
        "started_at": "2026-08-27T10:00:00Z",
        "finished_at": "2026-08-27T12:00:00Z",
        "brief": f"brief for {stage}",
        "decision": json.dumps({"tool": f"ms_{stage}", "params": {"solint": "int"}}),
        "tool": f"ms_{stage}",
        "backend": "claude",
        "model": "claude-opus-5",
        "tokens_in": 1200,
        "tokens_out": 300,
        "decide_wall_s": 4.5,
        **extra,
    }
    db.write_turn_record(run_root, run_key, rec)

    turn_id = db.insert_turn(
        conn,
        run_id=run_id,
        ordinal=ordinal,
        stage=stage,
        attempt=rec["attempt"],
        brief=rec["brief"],
        decision=rec["decision"],
        tool=rec["tool"],
        backend=rec["backend"],
        model=rec["model"],
        tokens_in=rec["tokens_in"],
        tokens_out=rec["tokens_out"],
        decide_wall_s=rec["decide_wall_s"],
        started_at=rec["started_at"],
    )
    db.set_turn_outcome(conn, turn_id, outcome, finished_at=rec["finished_at"])

    if job := rec.get("job"):
        job_id = db.insert_job(
            conn,
            turn_id=turn_id,
            executor=job["executor"],
            handle=job["handle"],
            script_path=job.get("script_path"),
            submitted_at=job.get("submitted_at"),
            stdout_path=job.get("stdout_path"),
            stderr_path=job.get("stderr_path"),
        )
        if job["state"] in ("done", "failed"):
            db.set_job_state(
                conn,
                job_id,
                job["state"],
                exit_code=job.get("exit_code"),
                finished_at=job.get("finished_at"),
            )
    for art in rec.get("artifacts", []):
        db.insert_artifact(conn, turn_id=turn_id, **art)
    for met in rec.get("metrics", []):
        db.insert_metric(conn, run_id=run_id, turn_id=turn_id, **met)
    return turn_id


@pytest.fixture
def populated(tmp_path):
    """Two runs, a retried stage, jobs, artifacts and metrics, on disk and indexed."""
    root = tmp_path / "runs"
    conn = db.connect(db.db_path(root))
    keys = []

    for n, (ms, status) in enumerate([("3c286.ms", "active"), ("g55.ms", "done")]):
        run_key = db.make_run_key(tmp_path / ms, f"2026-08-2{n + 1}T09:00:00Z")
        keys.append(run_key)
        run_rec = {
            "run_key": run_key,
            "started_at": f"2026-08-2{n + 1}T09:00:00Z",
            "finished_at": "2026-08-26T09:00:00Z" if status == "done" else None,
            "ms_path": str(tmp_path / ms),
            "workdir": str(tmp_path / "work" / ms),
            "telescope": "VLA",
            "backend": "claude",
            "executor": "slurm",
            "status": status,
        }
        db.write_run_record(root, run_rec)
        run_id = db.insert_run(
            conn,
            run_key=run_key,
            ms_path=run_rec["ms_path"],
            workdir=run_rec["workdir"],
            backend="claude",
            executor="slurm",
            telescope="VLA",
            started_at=run_rec["started_at"],
            status="active",
        )
        if status == "done":
            db.set_run_status(conn, run_id, "done", finished_at=run_rec["finished_at"])

        _record_turn(
            conn,
            root,
            run_key,
            run_id,
            stage="apply_preflag",
            outcome="accepted",
            job={
                "executor": "slurm",
                "handle": f"1000{n}",
                "state": "done",
                "exit_code": 0,
                "script_path": "s/preflag.py",
                "submitted_at": "2026-08-27T10:00:01Z",
                "finished_at": "2026-08-27T11:59:00Z",
                "stdout_path": "l/a.out",
                "stderr_path": "l/a.err",
            },
            artifacts=[
                {
                    "path": "/work/calibrators.ms",
                    "kind": "ms",
                    "size": 42,
                    "checksum": "abc",
                    "mtime": "2026-08-27T11:00:00Z",
                }
            ],
            metrics=[{"name": "flagged_fraction", "value": 0.092, "unit": "fraction"}],
        )
        # A retried stage: attempt 1 loses, attempt 2 counts.
        _record_turn(
            conn,
            root,
            run_key,
            run_id,
            stage="delay_bandpass_gain",
            outcome="retried",
            job={
                "executor": "slurm",
                "handle": f"2000{n}",
                "state": "failed",
                "exit_code": 1,
                "script_path": "s/gain1.py",
            },
        )
        _record_turn(
            conn,
            root,
            run_key,
            run_id,
            stage="delay_bandpass_gain",
            outcome="accepted",
            job={
                "executor": "slurm",
                "handle": f"3000{n}",
                "state": "done",
                "exit_code": 0,
                "script_path": "s/gain2.py",
            },
            artifacts=[
                {
                    "path": "/work/gain.G",
                    "kind": "caltable",
                    "size": 99,
                    "checksum": "def",
                    "mtime": "2026-08-27T12:00:00Z",
                }
            ],
            metrics=[{"name": "solutions_flagged", "value": 3.0, "unit": "percent"}],
        )
    yield root, conn, keys
    conn.close()


def test_rebuild_reproduces_the_index_exactly(populated):
    """Delete the database, replay the files, and demand the same rows back."""
    root, conn, _ = populated
    before = _dump(conn)
    assert before["turns"], "guard: the fixture must have written something"

    counts = db.rebuild(conn, root)

    assert _dump(conn) == before
    assert counts == {"runs": 2, "turns": 6, "jobs": 6, "artifacts": 4, "metrics": 4}


def test_rebuild_restores_which_attempt_counted(populated):
    """The retry invariant must be re-derived from disk, not remembered."""
    root, conn, keys = populated
    db.rebuild(conn, root)

    run_id = db.get_run(conn, keys[0])["id"]
    winner = db.accepted_turn(conn, run_id, "delay_bandpass_gain")
    assert winner["attempt"] == 2
    assert winner["ordinal"] == 3
    assert json.loads(winner["decision"])["tool"] == "ms_delay_bandpass_gain"


def test_rebuild_keeps_artifact_measurements_from_the_record(populated, tmp_path):
    """A later attempt overwrites the caltable, so re-measuring disk would lie.

    This is the retry-overwrite defect in the ms_modify tools. Until that is
    fixed, the recorded checksum is the only thing that says what each turn
    actually produced.
    """
    root, conn, keys = populated
    db.rebuild(conn, root)

    run_id = db.get_run(conn, keys[0])["id"]
    turn = db.accepted_turn(conn, run_id, "delay_bandpass_gain")
    art = db.artifacts_of(conn, turn["id"])[0]
    assert art["checksum"] == "def"  # the record, not a fresh hash of a missing file
    assert art["size"] == 99


def test_rebuild_recovers_a_turn_whose_rows_were_never_written(populated):
    """The crash window: the record file lands, then the process dies."""
    root, conn, keys = populated
    run_key = keys[0]
    orphan = {
        "ordinal": 4,
        "stage": "first_image",
        "attempt": 1,
        "outcome": "accepted",
        "started_at": "2026-08-27T13:00:00Z",
        "brief": "image it",
        "tool": "ms_tclean",
    }
    db.write_turn_record(root, run_key, orphan)

    db.rebuild(conn, root)

    run_id = db.get_run(conn, run_key)["id"]
    assert db.accepted_turn(conn, run_id, "first_image")["ordinal"] == 4


def test_rebuild_is_idempotent(populated):
    root, conn, _ = populated
    db.rebuild(conn, root)
    once = _dump(conn)
    db.rebuild(conn, root)
    assert _dump(conn) == once


def test_rebuild_of_an_empty_root_is_not_an_error(tmp_path):
    conn = db.connect(db.db_path(tmp_path / "runs"))
    assert db.rebuild(conn, tmp_path / "runs") == {
        "runs": 0,
        "turns": 0,
        "jobs": 0,
        "artifacts": 0,
        "metrics": 0,
    }
    conn.close()


def test_a_corrupt_record_is_reported_not_skipped(populated):
    """Silently dropping an unreadable turn would lose history without a trace."""
    root, conn, keys = populated
    db.turn_json_path(root, keys[0], 1).write_text("{not json")
    with pytest.raises(ValueError, match="unreadable turn record"):
        db.rebuild(conn, root)


def test_partial_writes_are_never_visible(tmp_path):
    """Records are renamed into place, so a reader sees all of one or none."""
    root = tmp_path / "runs"
    db.ensure_run_dirs(root, "k")
    db.write_turn_record(root, "k", {"ordinal": 1, "stage": "s", "attempt": 1})
    assert not list(db.turns_dir(root, "k").glob("*.tmp"))
