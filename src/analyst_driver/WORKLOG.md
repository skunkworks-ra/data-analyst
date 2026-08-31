# WORKLOG — analyst_driver

## STATUS (2026-08-27)

**Goal**: an external loop that runs a CASA reduction as a sequence of long
jobs, calling a model only at the decision points between them. The model
decides and exits; the loop submits the job and waits. Backends
(claude/opencode/codex) and executors (local/slurm/htcondor) are pluggable. No
reduction logic lives here — the driver consumes the radio-interferometry skill
and the three MCP servers.

Design: `PLAN.md` at the repo root. It is scaffolding for branch `driver-v2` and
**must be deleted before any merge to main**.

**Next step**: step 4 of the plan — `executors.py` with the `local` executor
only, plus brief rendering in `loop.py`. Prove one turn end to end against a
stub backend that returns a fixed decision. **Read `PLAN.md` "What the driver
does" first**; it was rewritten on 2026-08-31 and it decides what the loop may
and may not do.

**Blocked on**: nothing.

---

## 2026-08-31 — the driver's role, settled

`PLAN.md` rewritten. No code changed. The session removed things rather than
adding them; the reasons are in the plan and are not repeated here.

- The driver renders a brief, runs the harness, submits the job, waits, and
  records. **It may report a number. It may never name a verdict.**
- Dropped: the threshold file (`verifier.yaml`), product checks that duplicate
  `ms_workflow_status`, and the stage-to-tool table. The only refusal left is
  "the decision names no script, so there is nothing to submit".
- The driver holds no tool list and no parameter schema. The MCP servers
  validate parameters at call time, and they die with the harness anyway.
- Metric harvest names no keys: walk the response envelope, one row per numeric
  leaf, name from the tool and key path, completeness flag stored with it.
- `max_turns = 100`, in `config.toml`. Refine after real runs.
- Recorded in the plan, not solved here: the stage vocabulary in
  `00-playbook.md` and the labels `ms_workflow_status` emits do not correspond.
  Reconcile it with the ALMA ladder work, not before.
- `db.py` is expected to be rewritten. Five findings are listed under Status in
  `PLAN.md` so they are not re-derived.

---

## 2026-08-27 — step 3, the run index (`db.py`)

Schema, all SQL, the on-disk record format, and `rebuild`. 21 unit tests in
`tests/unit/test_driver_db.py`; full suite 755 pass, ruff clean.

- **The index is derived, never authoritative.** Each turn writes
  `<run_root>/<run_key>/turns/NNNN.json` *before* its rows are inserted, so a
  crash between the two loses nothing. `rebuild` empties the tables and replays
  the files. The round-trip test compares a full table dump before and after —
  testing `write_turn_record` or `rebuild` alone would prove nothing, because
  the failure mode is the two drifting apart.
- **Identity**: `run_key` = `20260827T142530Z-3c286_b6-a91f`. The 4-char digest
  is over the *absolute* MS path, so two runs on same-named MSs in different
  directories cannot collide. `started_at` is a separate column so date queries
  need no string parsing.
- **Repeated stages**: `ordinal` is monotonic and never reused, even after a
  failed turn. `attempt` counts prior turns at the same stage.
  `set_turn_outcome(..., 'accepted')` demotes any previously accepted turn for
  that (run, stage) to `retried`, so "did the gaincal work" has exactly one
  answer and the caller cannot forget to enforce it.
- **Artifact checksums exist because of a defect elsewhere.** `ms_modify`
  scripts `rmtree` an existing output before writing, so a retried stage
  destroys the previous attempt's caltable and the file cannot say which turn
  produced it. `rebuild` therefore *replays* recorded size/checksum/mtime rather
  than re-measuring disk. Recorded in the notes-repo CLAUDE.md; the real fix
  belongs in the tools.
- `checksum_path` walks directories, since CASA products are directories, and
  hashes each file's relative path as well as its bytes so content and layout
  are distinguishable. An absent path measures as absent rather than raising —
  a failed job legitimately produces nothing.
- SQLite via stdlib `sqlite3`, WAL + 30 s busy timeout. DDL is ANSI-portable
  except two points marked `PORTABILITY` (rowid alias, `?` placeholders). Keep
  the run root on local disk: SQLite locking over NFS is unreliable.

### Traps found

- `pixi run pytest` does not exist as a task, and `.pixi/envs/default/bin/pytest`
  has a stale shebang pointing at the pre-rename `data-analyst` path. Use
  `.pixi/envs/default/bin/python -m pytest`.
- `pyproject.toml` now lists `src/analyst_driver` in the hatch packages and
  declares the `analyst-driver` console script. **The script target
  `analyst_driver.cli:main` does not exist until step 8** — do not run it yet.
