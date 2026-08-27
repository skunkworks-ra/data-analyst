# External loop for radio-analyst — `analyst_driver` v2

## Context

A CASA reduction is a chain of long jobs. A `tclean` can run eight hours. If an
LLM agent drives the reduction from inside one session, it must sit idle across
that job: on a shared local inference server the KV cache is evicted and the
session times out; on a hosted model it burns context for nothing.

Invert the arrangement. **The model decides. The loop executes. They never run at
the same time.** The model produces a script and a short decision, then exits.
The loop submits the script, waits, records the result, and starts the next turn
from ground truth on disk.

Four requirements from the user:

- **(a)** No model or harness specificity. `claude`, `opencode`, `codex` are
  interchangeable backends.
- **(b)** A queryable database of per-turn metadata, aggregatable across runs.
- **(c)** Reuse the existing skills and MCP servers. Duplicate no calibration
  logic.
- **(d)** No execution specificity. Local, SLURM and HTCondor are interchangeable.

Plus one shape requirement settled during planning: **one driver manages several
MSs at once**, advancing whichever run is ready.

An earlier `external-loop` branch exists. The user has directed that it be
abandoned and the work restarted. This plan does not read from or build on it.

Code repository: `/Users/ssekhar/src/skunkworks-ra/radio-analyst`.
(The path `agents-md/skunkworks-ra/radio-analyst` holds notes only.)

---

## The turn

One turn advances one run by one stage.

1. **Sense** — build a brief from ground truth only. Call `ms_workflow_status`,
   read the previous job's exit code and output paths. No model involved.
2. **Decide** — run the agent harness as a one-shot subprocess with the brief.
   It reads the skills, calls read-only `ms_inspect` tools, calls one
   `ms_modify` tool with `execute=False`, and prints a decision JSON naming the
   generated script. Then it exits.
3. **Validate** — check the decision JSON against the stage schema and the
   allowed-tool table, and confirm the script exists.
4. **Dispatch** — submit the script through the executor. The model is not
   running.
5. **Record** — on job completion, write the turn to the database and exit.

`analyst-driver step` advances one run. `analyst-driver step --all` sweeps every
active run and advances each one whose job has finished. All state lives on disk
and in the database, so the driver can exit between turns, survive a login-node
reboot, and use the same code path for the local executor.

---

## Design decisions

### The filesystem is the truth; the database is an index

CASA products, generated scripts and `ms_reduction_log` are authoritative.
`analyst-driver rebuild` must reconstruct every database row from disk. Nothing
in the loop may read state that exists only in the database or only in RAM. This
mirrors `src/ms_inspect/tools/workflow_status.py`, which derives stage purely
from files.

### SQLite, stdlib `sqlite3`

One file, no server, no dependency. Write volume is a few rows per hour per run,
so WAL mode plus a busy timeout covers even separate driver processes. Postgres
buys network access and real concurrency; neither is needed, and it costs a
long-lived server on a shared cluster.

Two operational notes, both binding:

- Keep the run root on **local disk, not NFS**. SQLite file locking over NFS is
  unreliable.
- Write ANSI-portable DDL and keep all SQL in `db.py`. Moving to Postgres later
  is then a DDL rewrite and a placeholder change — half a day, not a redesign.

Semantic search is deliberately **out of scope**. "Did the gaincal work" is an
exact-match query on one row; cross-run aggregation is `GROUP BY`. Embeddings
only pay off for "find turns like this one", which needs a corpus that does not
yet exist. Leave room for an embedding column; build nothing.

### Schema

| table | contents |
|---|---|
| `runs` | `id`, `run_key`, `started_at`, `ms_path`, `workdir`, `telescope`, `backend`, `executor`, `status` |
| `turns` | `id`, `run_id`, `ordinal`, `stage`, `attempt`, `outcome`, brief text, decision JSON, model, tokens, wall time |
| `jobs` | `turn_id`, executor kind, handle, submitted/finished, exit code, log paths |
| `artifacts` | `turn_id`, path, kind (caltable/image/script/plot), size, `checksum`, `mtime` |
| `metrics` | `run_id`, `turn_id`, `name`, `value`, `unit` |

**Identity.** `run_key` is `20260827T142530Z-3c286_b6-a91f` — timestamp, MS name,
short hash. Sortable, unique, readable. `started_at` is a separate real timestamp
column so date queries need no string parsing. `id` exists only for joins.

**Repeated stages.** `ordinal` is monotonic within a run and never reused.
`attempt` counts prior turns in the run with the same `stage`, so the second
gaincal is `attempt=2`. `outcome` is `accepted` / `retried` / `failed`, so "the
gaincal that counted" is `WHERE stage='delay_bandpass_gain' AND
outcome='accepted'` and returns at most one row.

`metrics` is long-form on purpose. A new measurement never needs a migration, and
"median flagged fraction after rflag, across all runs" is one query.

### Backend contract (requirement a)

One contract: *a command that takes a prompt non-interactively and returns text
on stdout.* Concretely `claude -p`, `opencode run`, `codex exec`. Each backend
declares its MCP registration file, so the harness loads `ms-inspect`,
`ms-modify` and `ms-create` itself.

Skills come free for backends that read `SKILL.md`. `codex` does not, so its
adapter passes the relevant skill file paths in the prompt. A per-backend
capability note, not a fork in the design.

### Executor contract (requirement d)

`submit(script, config) -> handle`, `poll(handle) -> pending|running|done|failed`,
`exit_code(handle)`.

- `local` — subprocess; handle is a pid plus a marker file.
- `slurm` — reuse `src/ms_modify/slurm.py` (`SlurmConfig`, `build_sbatch`,
  `detect_account`). Poll via `sacct`.
- `htcondor` — sibling; nothing exists yet.

Neither contract knows anything about CASA. Requirement (c) is met by omission:
the loop never reasons about radio astronomy.

### The leash (stage → allowed tools)

`ms_workflow_status` emits a fixed set of `next_recommended_step` labels:
`import_asdm`, `set_intents`, `apply_preflag`, `generate_priorcals`,
`initial_bandpass`, `apply_initial_rflag_then_applycal`, `delay_bandpass_gain`,
`first_image`, `selfcal_or_done`, plus two `probe_failed_*` labels.

The leash maps each label to the `ms_modify` tools that may legitimately follow
it — writing down transitions that
`.claude/skills/radio-interferometry/00-playbook.md` already states. A decision
naming an out-of-stage tool is rejected before submission. Skills and
`ms_workflow_status` constrain by persuasion; this table is the only enforcement.
A `probe_failed_*` label always halts and requires a human.

---

## Files

New package `src/analyst_driver/` — five modules. Split only at genuine seams: the
backend, the executor and the store are swappable; everything else is one loop.

| file | responsibility |
|---|---|
| `cli.py` | `analyst-driver init/step/status/rebuild`; parses `config.toml`; console script in `pyproject.toml` |
| `loop.py` | the five stages, brief rendering, decision schema, the leash table |
| `backends.py` | backend protocol + `claude`, `opencode`, `codex` adapters |
| `executors.py` | executor protocol + `local`, `slurm`, `htcondor` adapters |
| `db.py` | schema, all SQL, writes, and `rebuild` reconstruction |
| `WORKLOG.md` | per the repo WORKLOG convention |

**`config.toml`** lives in the run root and is data, not code — no module for it.
It carries only operational settings, never science: ms_path, workdir, backend
command and its MCP config file, executor kind, and when the executor is SLURM
the `SlurmConfig` fields already defined in `src/ms_modify/slurm.py` (account,
partition, mem, time, modules).

This does not overlap the skills. The skills answer "what solint should this
gaincal use"; `config.toml` answers "which queue, as which user, driven by which
binary". It varies per machine and per user, not per dataset, and the skills
correctly know nothing about SLURM or about backends.

Reused, not rewritten: `src/ms_modify/slurm.py`,
`src/ms_inspect/tools/workflow_status.py`, `src/ms_create/reduction_log.py`,
`src/ms_modify/pathguard.py`, and all three MCP servers.

Modified: `pyproject.toml` (console script), `pixi.toml` (a `driver` task),
the repo `CLAUDE.md` (a driver section).

No skill file is modified. The driver is a consumer of the skills and the MCP
servers, never a second place where reduction logic lives.

---

## Order of work

0. **Branch and plan file.** In `/Users/ssekhar/src/skunkworks-ra/radio-analyst`,
   create branch `driver-v2` from `main`. Copy this plan to `PLAN.md` at the repo
   root and commit it as the first commit on the branch. `PLAN.md` is scaffolding:
   **delete it in the final commit before any merge to `main`.** All driver work
   happens on this branch; nothing is pushed unless the user asks.

   This commits to a repository other than `agents-md`, so it needs explicit
   confirmation at execution time. Repo:
   **`/Users/ssekhar/src/skunkworks-ra/radio-analyst`**

1. **Delete the old branch locally only** — `git branch -D external-loop`. Do
   **not** touch `origin/external-loop`; the user handles the remote.
2. **Record the retry-overwrite defect** in
   `/Users/ssekhar/src/agents-md/skunkworks-ra/radio-analyst/CLAUDE.md`: CASA
   products are overwritten in place on a retry, so a second gaincal destroys the
   first `gain.G`. The correct fix is in the `ms_modify` tools and the skills, not
   the driver. Until then the driver records checksum and mtime per artifact so
   the database still knows which attempt produced the file on disk.
3. `db.py` — schema, writes, tests. No model, no executor.
4. `executors.py` with `local` only, plus brief rendering in `loop.py`. Prove one
   turn end to end with a stub backend returning a fixed decision.
5. Decision schema and the leash table in `loop.py`.
6. `backends.py` — `claude` first, then `opencode`, then `codex`.
7. `executors.py` — `slurm` over the existing `slurm.py`, then `htcondor`.
8. `cli.py`, including `step --all` and `rebuild`.

---

## Verification

- **Unit** — `pixi run test-unit`. Cover: schema round-trip; `attempt` and
  `outcome` selecting the right repeated stage; leash rejection of an
  out-of-stage tool; brief rendering from a synthetic `ms_workflow_status`
  payload; executor state transitions against a fake scheduler.
- **`rebuild` is the load-bearing test.** Run a full reduction, delete the
  database file, run `analyst-driver rebuild`, assert the reconstructed rows
  equal the originals. If this fails, the database has become the truth and the
  design is broken.
- **Stub-backend dry run** — canned decision per stage, `local` executor,
  scripts replaced by `sleep 1`. Proves the loop without CASA or a model.
- **Local live run** — small simulated MS via the `ms-simulator` skill,
  `backend=claude`, `executor=local`, import to first image. Compare the recorded
  stage sequence against `ms_reduction_log(action='list')`.
- **Multi-run fan-out** — two simulated MSs, one driver, `step --all`. Confirm
  turns interleave and no row lands under the wrong `run_id`.
- **Resumability** — kill the driver mid-job, re-invoke, confirm it adopts the
  running job rather than resubmitting it.
- **Cluster run** — corrino with `executor=slurm`, one stage only, checking
  `sacct` polling and the exit-code path before a full chain.
- `pixi run lint` clean.

## Where the trouble is, ranked

1. **Stage 2 output discipline.** Getting a harness to emit one clean JSON object
   and nothing else is the hardest part, and it differs per backend. Mitigation:
   parse the last well-formed JSON object in stdout; treat a parse failure as a
   retryable turn, not a run failure.
2. **Adopting an already-running job on resume.** If the driver restarts while a
   job is queued, it must recognise the handle and not resubmit. A naive
   implementation wastes cluster time here.
3. **Retries overwriting CASA products.** Until item 2 of the work order is
   fixed in the tools, reproducibility of a retried stage rests on artifact
   checksums rather than on the files themselves.
4. **`rebuild` drifting from the write path.** Two code paths must agree on how
   disk maps to rows. Test them against each other, not separately.
5. **HTCondor** is unwritten and untested here. Lowest risk — isolated behind the
   executor contract — but it needs a real submit node.
