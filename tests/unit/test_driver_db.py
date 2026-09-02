"""Unit tests for analyst_driver.db — the journal and the index over it.

The load-bearing test is the round-trip: a live history, the database file
deleted, ``rebuild``, and a full dump compared for equality. Testing the write
path or ``rebuild`` alone would prove nothing — the failure mode is the two
drifting apart.
"""

from datetime import UTC, datetime

import pytest

from analyst_driver.db import DriverDB, make_run_key, measure_artifact


@pytest.fixture
def db(tmp_path):
    d = DriverDB(tmp_path / "runs")
    yield d
    d.close()


def _make_run(db, key="20260831T120000Z-testms-abcd"):
    db.create_run(
        key,
        ms_path="/data/test.ms",
        workdir="/work/test",
        telescope="VLA",
        backend="claude",
        executor="local",
        started_at="2026-08-31T12:00:00Z",
    )
    return key


def _job(handle="pid:123"):
    return {
        "executor": "local",
        "handle": handle,
        "submitted_at": "2026-08-31T12:01:00Z",
        "finished_at": None,
        "exit_code": None,
        "log_paths": ["/work/test/logs/j1.log"],
    }


# ----------------------------------------------------------------- identity


def test_run_key_format():
    now = datetime(2026, 8, 27, 14, 25, 30, tzinfo=UTC)
    key = make_run_key("/data/3C286_B6.ms", now=now)
    stamp, name, digest = key.split("-")
    assert stamp == "20260827T142530Z"
    assert name == "3c286_b6"
    assert len(digest) == 4


def test_run_key_distinguishes_directories():
    now = datetime(2026, 8, 27, tzinfo=UTC)
    a = make_run_key("/data/a/test.ms", now=now)
    b = make_run_key("/data/b/test.ms", now=now)
    assert a != b


# ---------------------------------------------------------------- live path


#: dump()["runs"] column order, so the positional asserts below survive a
#: column being added in the middle of the SELECT.
RUN_COLS = (
    "run_key",
    "started_at",
    "input_path",
    "ms_path",
    "workdir",
    "telescope",
    "backend",
    "executor",
    "status",
)


def test_create_run_writes_journal_and_row(db):
    key = _make_run(db)
    assert (db.run_root / key / "run.json").exists()
    rows = db.dump()["runs"]
    assert len(rows) == 1
    row = dict(zip(RUN_COLS, rows[0], strict=True))
    assert row["run_key"] == key
    assert row["status"] == "active"


def test_create_run_defaults_input_path_to_the_ms(db):
    """A run started from an MS has no separate input; the two agree."""
    key = _make_run(db)
    row = dict(zip(RUN_COLS, db.dump()["runs"][0], strict=True))
    assert row["input_path"] == row["ms_path"] == "/data/test.ms"
    assert key


def test_run_can_start_from_an_asdm_with_no_ms_yet(db):
    db.create_run(
        "k1",
        input_path="/data/uid___A002_X1",
        ms_path="",
        workdir="/w",
        started_at="2026-08-31T10:00:00Z",
    )
    row = dict(zip(RUN_COLS, db.dump()["runs"][0], strict=True))
    assert row["input_path"] == "/data/uid___A002_X1"
    assert row["ms_path"] == ""


def test_set_run_ms_path_records_the_imported_ms(db):
    db.create_run(
        "k1",
        input_path="/data/uid___A002_X1",
        ms_path="",
        workdir="/w",
        started_at="2026-08-31T10:00:00Z",
    )
    db.set_run_ms_path("k1", "/w/uid___A002_X1.ms")
    assert db._read_json(db._run_json("k1"))["ms_path"] == "/w/uid___A002_X1.ms"
    row = dict(zip(RUN_COLS, db.dump()["runs"][0], strict=True))
    assert row["ms_path"] == "/w/uid___A002_X1.ms"
    assert row["input_path"] == "/data/uid___A002_X1"


def test_find_runs_by_ms_matches_the_asdm_before_import(db, tmp_path):
    asdm = tmp_path / "uid___A002_X1"
    db.create_run(
        "k1",
        input_path=str(asdm.absolute()),
        ms_path="",
        workdir="/w",
        started_at="2026-08-31T10:00:00Z",
    )
    assert [r["run_key"] for r in db.find_runs_by_ms(asdm)] == ["k1"]


def test_migration_adds_input_path_to_an_old_database(tmp_path):
    """A database created before the column must not need deleting."""
    import sqlite3

    root = tmp_path / "runs"
    root.mkdir()
    conn = sqlite3.connect(root / "driver.sqlite3")
    conn.executescript(
        "CREATE TABLE runs (id INTEGER PRIMARY KEY, run_key TEXT NOT NULL UNIQUE,"
        " started_at TEXT NOT NULL, ms_path TEXT NOT NULL, workdir TEXT NOT NULL,"
        " telescope TEXT, backend TEXT, executor TEXT, status TEXT NOT NULL);"
        "INSERT INTO runs (run_key, started_at, ms_path, workdir, status)"
        " VALUES ('old', '2026-01-01T00:00:00Z', '/data/old.ms', '/w', 'active');"
    )
    conn.commit()
    conn.close()

    d = DriverDB(root)
    try:
        cols = {r[1] for r in d.conn.execute("PRAGMA table_info(runs)")}
        assert "input_path" in cols
        # backfilled from ms_path, so an old run keeps a usable identity
        assert (
            d.conn.execute("SELECT input_path FROM runs WHERE run_key = 'old'").fetchone()[0]
            == "/data/old.ms"
        )
    finally:
        d.close()


def test_record_turn_written_at_submission(db):
    """Finding 2: the record exists while the job is still running."""
    key = _make_run(db)
    db.record_turn(
        key, 1, stage="apply_preflag", brief="b", decision={"script": "s.py"}, jobs=[_job()]
    )
    assert (db.run_root / key / "turns" / "0001.json").exists()
    (turn,) = db.dump()["turns"]
    assert turn[4] == "submitted"
    assert turn[5] is None  # no outcome yet


def test_complete_turn_updates_record(db):
    key = _make_run(db)
    db.record_turn(key, 1, stage="apply_preflag", jobs=[_job()])
    job = dict(_job(), finished_at="2026-08-31T13:00:00Z", exit_code=0)
    db.complete_turn(key, 1, outcome="accepted", jobs=[job], wall_time_s=3540.0)
    (turn,) = db.dump()["turns"]
    assert turn[4] == "complete"
    assert turn[5] == "accepted"
    (jrow,) = db.dump()["jobs"]
    assert jrow[6] == 0


def test_invalid_outcome_refused(db):
    key = _make_run(db)
    db.record_turn(key, 1, stage="apply_preflag")
    with pytest.raises(ValueError):
        db.complete_turn(key, 1, outcome="great")


def test_next_ordinal_monotonic(db):
    key = _make_run(db)
    assert db.next_ordinal(key) == 1
    db.record_turn(key, 1, stage="apply_preflag")
    db.complete_turn(key, 1, outcome="failed")
    assert db.next_ordinal(key) == 2


def test_multiple_jobs_per_turn(db):
    """Finding 5: the journal holds as many jobs as the schema allows."""
    key = _make_run(db)
    db.record_turn(key, 1, stage="first_image", jobs=[_job("pid:1"), _job("pid:2")])
    assert len(db.dump()["jobs"]) == 2


# ------------------------------------------------------- repeated stages


def test_attempt_counts_prior_same_stage_turns(db):
    key = _make_run(db)
    db.record_turn(key, 1, stage="delay_bandpass_gain")
    db.complete_turn(key, 1, outcome="failed")
    db.record_turn(key, 2, stage="first_image")
    db.complete_turn(key, 2, outcome="accepted")
    rec = db.record_turn(key, 3, stage="delay_bandpass_gain")
    assert rec["attempt"] == 2


def test_accept_demotes_previous_accepted(db):
    key = _make_run(db)
    db.record_turn(key, 1, stage="delay_bandpass_gain")
    db.complete_turn(key, 1, outcome="accepted")
    db.record_turn(key, 2, stage="delay_bandpass_gain")
    db.complete_turn(key, 2, outcome="accepted")
    outcomes = {t[1]: t[5] for t in db.dump()["turns"]}
    assert outcomes == {1: "retried", 2: "accepted"}
    # the demotion reached the journal file, not just the index
    assert db._read_json(db._turn_json(key, 1))["outcome"] == "retried"


def test_accepted_query_returns_one_row(db):
    key = _make_run(db)
    for i in (1, 2, 3):
        db.record_turn(key, i, stage="delay_bandpass_gain")
        db.complete_turn(key, i, outcome="accepted")
    rows = db.conn.execute(
        "SELECT ordinal FROM turns WHERE stage='delay_bandpass_gain' AND outcome='accepted'"
    ).fetchall()
    assert rows == [(3,)]


# ------------------------------------------------------------------ metrics


def test_metric_flag_stored(db):
    """Finding 3: without the flag an UNAVAILABLE row averages in as data."""
    key = _make_run(db)
    db.record_turn(key, 1, stage="apply_preflag")
    db.complete_turn(
        key,
        1,
        outcome="accepted",
        metrics=[
            {
                "name": "ms_apply_preflag.flag_fraction",
                "value": None,
                "unit": None,
                "flag": "UNAVAILABLE",
            }
        ],
    )
    (m,) = db.dump()["metrics"]
    assert m[3] is None
    assert m[5] == "UNAVAILABLE"


def test_run_level_metric_has_null_turn(db):
    """Finding 3: a run-level metric needs somewhere to live in the journal."""
    key = _make_run(db)
    db.record_run_metric(key, "total_wall_time", 7200.0, unit="s")
    (m,) = db.dump()["metrics"]
    assert m[1] is None  # no turn ordinal
    assert m[2] == "total_wall_time"


# ---------------------------------------------------------------- artifacts


def test_ms_kind_gets_metadata_digest(tmp_path):
    """Finding 4: a science MS is gigabytes — never read its bytes."""
    ms = tmp_path / "test.ms"
    ms.mkdir()
    (ms / "table.dat").write_bytes(b"x" * 100)
    rec = measure_artifact(ms, "ms")
    assert rec["checksum"].startswith("meta:")
    assert rec["size"] == 100
    assert rec["mtime"] is not None


def test_metadata_digest_changes_when_rewritten(tmp_path):
    import os as _os

    img = tmp_path / "target.image"
    img.mkdir()
    f = img / "table.dat"
    f.write_bytes(b"x" * 100)
    _os.utime(img, (1000.0, 1000.0))
    first = measure_artifact(img, "image")["checksum"]
    _os.utime(img, (2000.0, 2000.0))
    second = measure_artifact(img, "image")["checksum"]
    assert first.startswith("meta:")
    assert first != second


def test_caltable_hashed(tmp_path):
    cal = tmp_path / "gain.G"
    cal.mkdir()
    (cal / "table.dat").write_bytes(b"solutions")
    rec = measure_artifact(cal, "caltable")
    assert rec["checksum"].startswith("sha256:")


def test_checksum_distinguishes_layout(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / "one.dat").write_bytes(b"same")
    (b / "two.dat").write_bytes(b"same")
    assert (
        measure_artifact(a, "caltable")["checksum"] != measure_artifact(b, "caltable")["checksum"]
    )


def test_absent_artifact_measures_absent(tmp_path):
    rec = measure_artifact(tmp_path / "never_written.G", "caltable")
    assert rec == {
        "path": str(tmp_path / "never_written.G"),
        "kind": "caltable",
        "size": None,
        "mtime": None,
        "checksum": None,
    }


# ----------------------------------------------------------------- rebuild


def _full_history(db):
    key = _make_run(db)
    db.record_turn(
        key,
        1,
        stage="apply_preflag",
        brief="brief 1",
        decision={"script": "preflag.py", "tool": "ms_apply_preflag"},
        model="claude",
        tokens_in=1000,
        tokens_out=200,
        jobs=[_job()],
    )
    db.complete_turn(
        key,
        1,
        outcome="accepted",
        jobs=[dict(_job(), finished_at="2026-08-31T13:00:00Z", exit_code=0)],
        artifacts=[
            {
                "path": "/work/test/preflag.py",
                "kind": "script",
                "size": 512,
                "checksum": "ab" * 32,
                "mtime": 1756645200.0,
            }
        ],
        metrics=[
            {"name": "ms_apply_preflag.flag_fraction", "value": 0.12, "unit": None, "flag": "OK"}
        ],
        wall_time_s=120.0,
    )
    db.record_turn(key, 2, stage="apply_preflag")
    db.complete_turn(key, 2, outcome="accepted", wall_time_s=60.0)
    db.record_run_metric(key, "n_stages", 2.0)
    # a submitted-but-unfinished turn, as a crash would leave it
    db.record_turn(key, 3, stage="generate_priorcals", jobs=[_job("slurm:99")])
    return key


def test_rebuild_roundtrip(db, tmp_path):
    """The load-bearing test: journal → rows must equal rows → journal → rows."""
    _full_history(db)
    before = db.dump()
    db.close()
    db.db_path.unlink()
    fresh = DriverDB(db.run_root)
    counts = fresh.rebuild()
    assert counts == {"runs": 1, "turns": 3}
    assert fresh.dump() == before
    fresh.close()


def test_submitted_turn_survives_crash(db):
    """Finding 2: a job submitted before a crash is visible to rebuild."""
    key = _make_run(db)
    db.record_turn(key, 1, stage="first_image", jobs=[_job("slurm:42")])
    db.close()
    db.db_path.unlink()
    fresh = DriverDB(db.run_root)
    fresh.rebuild()
    (turn,) = fresh.dump()["turns"]
    assert turn[4] == "submitted"
    (job,) = fresh.dump()["jobs"]
    assert job[3] == "slurm:42"
    fresh.close()


def test_rebuild_preserves_demotion(db):
    key = _make_run(db)
    for i in (1, 2):
        db.record_turn(key, i, stage="delay_bandpass_gain")
        db.complete_turn(key, i, outcome="accepted")
    before = db.dump()
    db.rebuild()
    assert db.dump() == before


def test_no_temp_files_left(db):
    key = _full_history(db)
    leftovers = list((db.run_root / key).rglob("*.tmp"))
    assert leftovers == []


# ------------------------------------------------- run status and MS lookup


def test_set_run_status_accepts_the_vocabulary(db):
    key = _make_run(db)
    for status in ("completed", "needs_human", "failed", "active"):
        db.set_run_status(key, status)
        assert db._read_json(db._run_json(key))["status"] == status


def test_set_run_status_rejects_anything_else(db):
    key = _make_run(db)
    with pytest.raises(ValueError, match="status must be one of"):
        db.set_run_status(key, "done")


def test_find_runs_by_ms_matches_the_absolute_path(db, tmp_path):
    ms = tmp_path / "x.ms"
    db.create_run("k1", ms_path=str(ms.absolute()), workdir="/w", started_at="2026-08-31T10:00:00Z")
    found = db.find_runs_by_ms(ms)
    assert [r["run_key"] for r in found] == ["k1"]


def test_find_runs_by_ms_excludes_terminal_runs(db, tmp_path):
    ms = tmp_path / "x.ms"
    db.create_run("k1", ms_path=str(ms.absolute()), workdir="/w", started_at="2026-08-31T10:00:00Z")
    db.set_run_status("k1", "completed")
    assert db.find_runs_by_ms(ms) == []
    assert len(db.find_runs_by_ms(ms, statuses=("completed",))) == 1


def test_find_runs_by_ms_orders_oldest_first(db, tmp_path):
    ms = tmp_path / "x.ms"
    db.create_run("k2", ms_path=str(ms.absolute()), workdir="/w", started_at="2026-08-31T11:00:00Z")
    db.create_run("k1", ms_path=str(ms.absolute()), workdir="/w", started_at="2026-08-31T10:00:00Z")
    assert [r["run_key"] for r in db.find_runs_by_ms(ms)] == ["k1", "k2"]


def test_find_runs_by_ms_does_not_match_another_ms(db, tmp_path):
    db.create_run(
        "k1",
        ms_path=str((tmp_path / "a.ms").absolute()),
        workdir="/w",
        started_at="2026-08-31T10:00:00Z",
    )
    assert db.find_runs_by_ms(tmp_path / "b.ms") == []


# ---------------------------------------------------------------------------
# Token columns
# ---------------------------------------------------------------------------


def test_turn_records_all_four_token_counts(tmp_path):
    """One column cannot carry input usage: uncached input, cache reads and
    cache writes bill at different rates."""
    db = DriverDB(tmp_path / "runs")
    key = _make_run(db)
    db.record_turn(
        key, 1, stage="gaincal", brief="b", decision={}, model="m",
        tokens_in=26, tokens_cache_read=417313, tokens_cache_creation=31666,
        tokens_out=4138,
    )
    (row,) = db.conn.execute(
        "SELECT tokens_in, tokens_cache_read, tokens_cache_creation, tokens_out FROM turns"
    ).fetchall()
    assert row == (26, 417313, 31666, 4138)
    db.close()


def test_the_cache_columns_are_added_to_a_database_that_predates_them(tmp_path):
    """A database written before 2026-09-02 has neither column. CREATE TABLE
    IF NOT EXISTS is a no-op on an existing table, so this needs the ALTER."""
    import sqlite3

    (tmp_path / "runs").mkdir(parents=True, exist_ok=True)
    path = tmp_path / "runs" / "driver.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE turns (id INTEGER PRIMARY KEY, run_id INTEGER, ordinal INTEGER,"
        " stage TEXT, attempt INTEGER, state TEXT, outcome TEXT, brief TEXT,"
        " decision TEXT, model TEXT, tokens_in INTEGER, tokens_out INTEGER,"
        " wall_time_s REAL, UNIQUE (run_id, ordinal));"
        "INSERT INTO turns (run_id, ordinal, stage, attempt, state, tokens_in)"
        " VALUES (1, 1, 'gaincal', 1, 'done', 26);"
    )
    conn.commit()
    conn.close()

    db = DriverDB(tmp_path / "runs")
    have = {r[1] for r in db.conn.execute("PRAGMA table_info(turns)")}
    assert {"tokens_cache_read", "tokens_cache_creation"} <= have

    # NULL, not 0: the old row was never measured, and 0 would read as
    # "measured, and it was nothing" — the same mistake the columns fix.
    (row,) = db.conn.execute(
        "SELECT tokens_cache_read, tokens_cache_creation FROM turns"
    ).fetchall()
    assert row == (None, None)
    db.close()
