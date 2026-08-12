# WORKLOG — analyst_driver (external loop)

## STATUS (2026-08-11)

- **Goal**: run a CASA reduction as a sequence of long jobs, calling a model
  only at the decision points. No model process may sit idle waiting on a job —
  that is what evicts KV caches and times out shared local inference servers.
- **Next step**: run it for real. `[executor] kind = "local"`,
  `[backend] kind = "claude"`, against a real MS on corrino. Everything below
  has only been exercised with `kind = "dry"` and a stub backend.
- **Then**: the permissions work — designed with the user, not built. Read-only
  tool flags for the backend, a path-root allowlist extending
  `ms_modify/pathguard.py`, and an approval gate before submit for the first
  live runs.
- **Blocked on**: nothing.

### Open items

- `opencode`'s non-interactive flag in `config.toml` is a **guess**. Verify
  before first use. The contract itself is backend-agnostic (the decision leaves
  through a file, not stdout), so only the command template should need editing.
- `SlurmExecutor` has never met a real scheduler. It is unit-tested against a
  mocked `sbatch`/`sacct`; `LocalExecutor` and `DryExecutor` are exercised for
  real. The sbatch body duplicates a little of `ms_modify/slurm.py` — fold them
  together once SLURM is actually used.
- Per-tool call caps are designed but **not implemented** — only the total
  `step_cap` and the identical-call cycle detector are live. User deferred them
  to keep the first cut simple.
- `recipe.yaml` `alma_12m` lists `ms_applycal` for the priors-then-split step.
  ALMA actually needs a split into a new MS afterwards, and the driver has no
  concept of the active MS changing except by rescanning for `*.ms`. Revisit
  when ALMA data goes through this.

---

## 2026-08-11 — packaged as `analyst-driver`

Moved `driver/` to `src/analyst_driver/` and added an `analyst-driver` console
script, matching how `ms-inspect` / `ms-modify` / `ms-create` already work.

Why: `pixi run` searches the cwd and its parents for a manifest, so a task
could only ever be invoked from the repo — and the normal case is driving a run
from the directory the data lives in. A shell wrapper worked but was the wrong
shape when the repo already installs itself editable and ships console scripts.
`pixi install` now builds the binary; one symlink makes it global.

Dropped: `bin/analyst-driver`, and the pixi task with it. `sys.path.insert` is
gone from `driver.py`; the modules import each other as `analyst_driver.*`.
The package no longer copies anywhere and runs standalone — a property nobody
asked for, and not the one that matters. The reproducibility guarantee is that
`init` freezes `config.toml` and `PROMPT.md` into the run directory, which is
unchanged.

Added `init --root` and `init --config` so a different run location, executor
or backend needs no edit inside the installed package. `--config` is what the
dry-run smoke test uses.

Python packages cannot contain a hyphen, so the directory is `analyst_driver`
and only the command is `analyst-driver`. Same split as `ms_inspect` /
`ms-inspect`.

---

## 2026-08-11 — built the loop

Designed with the user in conversation, then written in one pass. The shape:

```
tick: poll job → harvest → verify → render brief → call model once →
      validate → generate script → submit → commit_turn → exit
```

Design decisions worth keeping (the reasoning, not the code):

- **The model emits a tool call, never code.** `ms_modify` tools generate the
  script. So the automated path and the reproducible path are the same path.
- **Evidence is a list of numbers with sources, not prose.** The driver opens
  the cited file and checks the number within 2 percent. That is a lie detector
  that needs no human. A rationale in prose cannot be checked; keep both.
- **The verifier decides nothing.** It reports into the brief as evidence and
  the model chooses. It also reports *how many checks ran*, because a check that
  cannot fail is not evidence.
- **No budget in the brief.** The driver enforces `step_cap` and the wall clock
  silently. A model that knows it is nearly out of steps trades away science to
  finish. Deliberate: the run parks with no warning to the model, and the state
  on disk loses nothing.
- **Section order in `BRIEF.md` is load-bearing.** Sections 1–4 are identical at
  every wake, so a prefix-caching backend hits them.
- **Script generation sits inside the refusal loop.** A tool that rejects the
  model's parameters is the strictest available check, so it becomes a refusal
  handed back to the model, not a crash.
- **`whitelist.yaml` declares no parameter types.** `validate.py` reads the real
  `run()` signature with `inspect.signature`, so the whitelist cannot drift.
- Four actions only: `run`, `redo`, `done`, `ask`. `skip` was cut (meaningless
  once the model picks the next tool freely) and `stop` was cut (it duplicated
  `ask`).

### Smoke test, and what it caught

Built a 6-antenna simulated MS plus a stub backend, ran with `kind = "dry"`.
Loop completed: preflag → refusal → done, with a clean ledger, replay script and
git history. Four defects the test caught, all now fixed:

1. `ms_reduction_log`'s `render` returns the replay script's **path**, not its
   text. We were writing that path into `replay.py`. Now we let the tool write
   `reduction_replay.py` and only confirm it exists.
2. The brief dumped raw `measurements.json` truncated at 1800 chars — invalid
   JSON, and `per_antenna` alone would swamp it on a real VLA run. Replaced with
   a scalar rendering; arrays over 8 entries become a count plus a pointer.
3. The brief advertised `script.py` in the step directory. The real name varies
   per tool (`preflag.py`, `apply_rflag.py`). Now it lists the actual files.
4. Section 2 showed `unknown` flag fractions forever, and no instrument line.
   Both are now populated — at init, and again after each harvest.

Also: a validator error message listed `ms_path, workdir, execute` among the
accepted parameters, which invites the model to send exactly what it must not.
Now filtered.

### Not a bug, but nearly logged as one

The simulated MS has `CORRECTED_DATA` (casatools' simulator creates it), so the
`has_corrected` precondition correctly passed on a step the test expected it to
refuse. The refusal path was then proved separately, with a bad parameter name.
Worth remembering: a test that fails to trigger the thing it exists to test
looks exactly like a pass.
