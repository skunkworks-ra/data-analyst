# WORKLOG — driver (external loop)

## STATUS (2026-08-11)

- **Goal**: run a CASA reduction as a sequence of long jobs, calling a model
  only at the decision points. No model process may sit idle waiting on a job —
  that is what evicts KV caches and times out shared local inference servers.
- **Next step**: run it for real. `[executor] kind = "local"`,
  `[backend] kind = "claude"`, against a real MS on corrino. Everything below
  has only been exercised with `kind = "dry"` and a stub backend.
- **Blocked on**: nothing.

### Open items

- `opencode`'s non-interactive flag in `config.toml` is a **guess**. Verify
  before first use. The contract itself is backend-agnostic (the decision leaves
  through a file, not stdout), so only the command template should need editing.
- `SlurmExecutor` is written but never executed. `LocalExecutor` and
  `DryExecutor` are exercised. The sbatch body duplicates a little of
  `ms_modify/slurm.py` — fold them together once SLURM is actually used.
- No unit tests yet. The validator was checked by a one-off script covering all
  nine refusal paths; that should become `tests/unit/test_driver_validate.py`.
- Per-tool call caps are designed but **not implemented** — only the total
  `step_cap` and the identical-call cycle detector are live. User deferred them
  to keep the first cut simple.
- `recipe.yaml` `alma_12m` lists `ms_applycal` for the priors-then-split step.
  ALMA actually needs a split into a new MS afterwards, and the driver has no
  concept of the active MS changing except by rescanning for `*.ms`. Revisit
  when ALMA data goes through this.

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
