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

### Status (2026-08-31)

Work-order steps 0-3 are done; steps 4-8 are not started. The whole of "What
the driver does" was rewritten on this date, after a session that removed three
things the first draft had: the threshold file, the stage-to-tool table, and any
form of refusal beyond "there is no script to submit". **Read that section
before writing any code — the design question it settles is the one that keeps
coming back, and re-deriving it wastes a session.**

`src/analyst_driver/db.py` exists from step 3 and is expected to be rewritten
rather than extended. Five findings against it, so they are not re-derived:

1. The pairing that must not drift — write the journal record, then insert the
   rows — lives in a *test helper*, not in `db.py`. The loop would write it a
   second time, and the round-trip test cannot see the divergence. `db.py` must
   own one `record_turn()` that both the loop and the tests call.
2. The turn record is written when a turn *ends*, so a job submitted before a
   crash has no file and `rebuild` cannot see it. Write the record at
   submission and update it at completion.
3. `metrics` has no `flag` column, and a run-level metric (`turn_id` NULL) has
   nowhere to live in the journal, so `rebuild` drops it silently.
4. `checksum_path` reads every byte of every artifact. A science MS is
   gigabytes. Decide which artifact kinds get a content hash.
5. The journal holds one job per turn; the schema allows many.

---

## The turn

One turn advances one run by one stage.

1. **Sense** — build a brief from ground truth only. Call `ms_workflow_status`,
   read the previous job's exit code and output paths. No model involved.
2. **Decide** — run the agent harness as a one-shot subprocess with the brief.
   It reads the skills, calls read-only `ms_inspect` tools, calls one
   `ms_modify` tool with `execute=False`, and prints a decision JSON naming the
   generated script. Then it exits.
3. **Read the result** — parse the decision JSON and confirm the script it
   names exists. Compare what the decision claims against the artifacts on
   disk, and write both sides into the record. Nothing here refuses a decision
   except an absent script, and nothing here judges the science. See "What the
   driver does".
4. **Dispatch** — submit the script through the executor. The model is not
   running.
5. **Record** — on job completion, write the journal record and the rows, then
   exit.

`analyst-driver step` advances one run. `analyst-driver step --all` sweeps every
active run and advances each one whose job has finished. All state lives on disk
and in the database, so the driver can exit between turns, survive a login-node
reboot, and use the same code path for the local executor.

---

## Design decisions

### The filesystem is the truth; the database is an index

Nothing in the loop may read state that exists only in the database or only in
RAM, and `analyst-driver rebuild` must reconstruct every row from files. This
mirrors `src/ms_inspect/tools/workflow_status.py`, which derives stage purely
from files.

Two different sets of files carry that truth, and conflating them was an error
in an earlier draft of this plan:

- **The reduction's own record** — CASA products, the generated scripts and
  `ms_reduction_log`. Authoritative for what the reduction did.
- **The driver's journal** — `<run_key>/turns/NNNN.json`, one record per turn.
  Authoritative for the model's half of the turn: the brief it was given, the
  decision it returned, the tools it called and what they returned, and the
  cost of the call. CASA never sees any of this, so it cannot come from the
  products. The journal is written before the rows are inserted, so a crash
  between the two loses nothing.

`rebuild` replays the journal. It must never re-measure the products, because a
retried stage overwrites the previous attempt's output (see the retry-overwrite
defect below).

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
| `metrics` | `run_id`, `turn_id`, `name`, `value`, `unit`, `flag` |

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

**The harvest names no keys.** Every `ms_inspect` tool wraps its numbers as
`{"value": ..., "flag": ...}`. The driver walks the response envelope and writes
one row per numeric leaf, with the name taken from the tool and the key path —
`ms_apply_initial_rflag.flag_fraction`, `ms_image_stats.dynamic_range` — and the
completeness flag carried along. The driver holds no list of interesting
measurements, so a number newly reported by a tool appears in the index with no
driver change. You name the measurement when you ask the question, not when the
row is written. The flag must be stored: without it an `UNAVAILABLE` row
averages in as though it were a measurement.

**Token counts are recorded and never used.** No requirement needs them, each
backend reports usage differently, so the columns are often NULL. Aggregate them
only alongside a count of how many turns supplied them. They may never drive
control: the driver does not tell the model about its budget, because a model
that knows it is short of budget trades away science to finish.

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

### What the driver does

**It renders a brief, runs the harness, submits the job the harness produced,
waits, and writes down what happened.** That is the whole role.

Three layers, and none of them holds another's facts:

| layer | owns |
|---|---|
| MCP tools | the measurement. Numbers, no verdict. |
| skills | what a number means, and what value is acceptable |
| driver | mechanics, and the record |

**The driver may report a number. It may not name a verdict.** Everything below
follows from that one line.

Its work falls in three categories, and blurring the second into the third is
the mistake this section exists to prevent.

**1. Things it cannot proceed without.** The decision must name a script that
exists. Without one there is nothing to submit, so the turn stops and the reason
is recorded. This is not a judgement — no action is available. `max_turns` (100,
in `config.toml`; refine after the first real runs) is the other one: at the cap
the run goes to `needs_human` and stops.

**2. Things it observes and writes down.** It records, and does not act on:

- the script named by the decision, its mtime, and the tool named in its header
  (every generated script opens with `Auto-generated by ms_gaincal (ms_modify)`)
  beside the tool the decision claims;
- each `{name, value, source}` the model cited, beside the value found in the
  cited file;
- which tools the turn called and what each returned;
- the brief the turn was given, and the job's exit code and logs.

It writes both values and stops there. "decision cited `flag_fraction = 0.12`;
`measurements.json` says 0.92" is a fact. MISMATCH, FAIL, or a refusal is a
verdict, and it would steer the next turn exactly as a threshold would.

**3. Science. Never.**

Three earlier ideas are dropped, and the reasons matter more than the ideas:

- **`verifier.yaml`, a table of thresholds** — 0.50 for flag fraction, 100 for
  dynamic range — writing PASS or FAIL into the next brief. Those numbers are
  science, they already exist in skills 07, 10 and 11, and a second copy drifts;
  that file's own header admitted it. A brief that says FAIL is a gate wearing a
  report's clothes.
- **Product checks.** `ms_workflow_status` already reports whether each stage's
  product exists, and reports UNAVAILABLE rather than guessing when a probe
  fails. The driver must not re-check any of it.
- **A stage→tool table (the "leash")**, refusing a decision that names a tool
  from another stage. Going back a stage is sometimes correct —
  `07b-gaincal-recovery.md` exists for that — so a table built from a linear
  playbook would refuse legitimate recovery. The harm it was meant to prevent is
  that an out-of-stage `ms_modify` call destroys an earlier artifact, and the
  fix for that belongs in the tools, which must version or archive an output
  instead of deleting it.

**The driver holds no list of tool names.** It reads the name from the decision
and from the script header. It needs no schema of its own either: the model
calls the tool through MCP, where the pydantic input model — `GaincalInput`,
`TcleanInput` — rejects a bad parameter at call time and the model corrects it
inside its own turn. A generated script exists only if the parameters were
already valid. Re-checking that in the driver would re-run a check that has
provably passed, and the servers are children of the harness, so by the time the
driver runs they are gone anyway.

**What this costs, stated plainly.** No process outside the model ever disagrees
with the model. A model can cite a wrong number and the job still runs. If a run
goes wrong, the record will show that the model saw the numbers and accepted
them. Two things make that tolerable: each turn is a fresh model reading
measurements it did not produce, which is closer to a fresh reviewer than to
self-assessment; and the record carries both sides of every claim, so a wrong
one is visible afterwards even though nothing blocked it at the time. For a
paper making a quality claim, a human control run remains the only real answer.

### The stage vocabulary needs reconciling — with the ALMA ladder, not before it

`ms_workflow_status` emits `import_asdm`, `set_intents`, `apply_preflag`,
`generate_priorcals`, `initial_bandpass`, `apply_initial_rflag_then_applycal`,
`delay_bandpass_gain`, `first_image`, `selfcal_or_done`, and two
`probe_failed_*` labels. `.claude/skills/radio-interferometry/00-playbook.md`
states the same transitions keyed on human-language states. **The two sets do
not correspond**, and that mismatch is live today, independent of the driver:

- The playbook's `MS imported, intents present → /inspect` has no label, because
  inspection writes no product a disk-derived tool can see. No fix needed: it
  folds into `apply_preflag`.
- The playbook forks to polcal after the calibration solve. There is no polcal
  label — `workflow_status.py` looks for `delay.K`, `bandpass.B`, `gain.G` and
  `gain.fluxscaled` and nothing polarization. Fixing this is a tool change.
- `selfcal_or_done` covers three playbook rows: polcal done, first image done,
  and post-cal RFI flagging.

Also `workflow_status.py:55` hardcodes the priorcal tables to `gain_curves.gc`
and `opacities.opac` with a `< 2` test, which is VLA-only. The ALMA ladder work
adds rungs and therefore labels. Reconcile the vocabulary as part of that
change, or it gets written twice. The driver reads whatever labels the tool
emits and hardcodes none of them.

---

## Files

New package `src/analyst_driver/` — five modules. Split only at genuine seams: the
backend, the executor and the store are swappable; everything else is one loop.

| file | responsibility |
|---|---|
| `cli.py` | `analyst-driver init/step/status/rebuild`; parses `config.toml`; console script in `pyproject.toml` |
| `loop.py` | the five stages, brief rendering, decision schema, the metric harvest |
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

0. **DONE — Branch and plan file.** In `/Users/ssekhar/src/skunkworks-ra/radio-analyst`,
   create branch `driver-v2` from `main`. Copy this plan to `PLAN.md` at the repo
   root and commit it as the first commit on the branch. `PLAN.md` is scaffolding:
   **delete it in the final commit before any merge to `main`.** All driver work
   happens on this branch; nothing is pushed unless the user asks.

   This commits to a repository other than `agents-md`, so it needs explicit
   confirmation at execution time. Repo:
   **`/Users/ssekhar/src/skunkworks-ra/radio-analyst`**

1. **DONE — Delete the old branch locally only** — `git branch -D external-loop`. Do
   **not** touch `origin/external-loop`; the user handles the remote.
2. **DONE — Record the retry-overwrite defect** in
   `/Users/ssekhar/src/agents-md/skunkworks-ra/radio-analyst/CLAUDE.md`: CASA
   products are overwritten in place on a retry, so a second gaincal destroys the
   first `gain.G`. The correct fix is in the `ms_modify` tools and the skills, not
   the driver. Until then the driver records checksum and mtime per artifact so
   the database still knows which attempt produced the file on disk.
3. `db.py` — schema, writes, tests. No model, no executor. **DONE, and due for
   a rewrite** — see the five findings under Status.
4. `executors.py` with `local` only, plus brief rendering in `loop.py`. Prove one
   turn end to end with a stub backend returning a fixed decision.
5. Decision schema, the claim-against-artifact record, and the metric harvest
   in `loop.py`.
6. `backends.py` — `claude` first, then `opencode`, then `codex`.
7. `executors.py` — `slurm` over the existing `slurm.py`, then `htcondor`.
8. `cli.py`, including `step --all` and `rebuild`.

---

## Verification

- **Unit** — `pixi run test-unit`. Cover: schema round-trip; `attempt` and
  `outcome` selecting the right repeated stage; a cited number the source file
  does not carry being recorded with both values and NOT refused; a decision
  naming a script that does not exist stopping the turn; the metric harvest
  finding a numeric leaf nobody named; brief rendering from a synthetic
  `ms_workflow_status` payload; executor state transitions against a fake
  scheduler.
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

1. **Retries overwriting CASA products.** Now the worst item, because dropping
   the stage→tool table removed the thing that would have blocked an
   out-of-stage `ms_modify` call. Every generated script deletes its output
   before writing, so a late re-run of `ms_initial_bandpass` overwrites `BP0.b`
   and corrupts the flag state of the non-bandpass calibrators. No later turn
   can undo it. The fix is in the tools — version or archive the output — and
   until it lands, artifact checksums are all that says which attempt produced
   the file on disk.
2. **Stage 2 output discipline.** Getting a harness to emit one clean JSON
   object and nothing else is hard, and it differs per backend. Mitigation:
   parse the last well-formed JSON object in stdout; treat a parse failure as a
   retryable turn, not a run failure.
3. **Adopting an already-running job on resume.** If the driver restarts while a
   job is queued, it must recognise the handle and not resubmit. This constrains
   when the journal is written: a record written only at the end of a turn
   leaves a submitted job invisible to `rebuild`.
4. **Nothing outside the model ever disagrees with it.** Accepted deliberately —
   see "What the driver checks". The consequence is that the record cannot show
   an independent judgement of a run, only that the model saw the numbers.
5. **`rebuild` drifting from the write path.** Two code paths must agree on how
   the journal maps to rows. Test them against each other, not separately, and
   through the same function the loop calls.
6. **HTCondor** is unwritten and untested here. Lowest risk — isolated behind the
   executor contract — but it needs a real submit node.
