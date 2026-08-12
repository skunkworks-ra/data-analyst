# WORKLOG — analyst_driver (external loop)

## STATUS (2026-08-12)

- **Goal**: run a CASA reduction as a sequence of long jobs, calling a model
  only at the decision points. No model process may sit idle waiting on a job —
  that is what evicts KV caches and times out shared local inference servers.
- **Done**: the loop is built, packaged, reworked and green. **944 unit tests**
  pass, ruff clean. Both input paths complete a dry run from an unrelated
  directory using the installed `analyst-driver` binary: an MS start, and an
  ASDM start that imports, registers `raw`, then splits and registers
  `calibrators`. The input and layout rework designed below is **built** —
  `processed/`, `--input`, planned outputs, `ms_role` plus the registry, and
  preconditions from `ms_workflow_status`. Scope was `main` only.
- **Next step**: **the first real run.** On corrino:

      pixi install
      ln -s <repo>/.pixi/envs/default/bin/analyst-driver ~/bin/
      # copy config.toml, set executor kind = "local", backend kind = "claude"
      analyst-driver init --run-id <id> --input <MS-or-ASDM> --goal "..." \
          --root <work> --config <my-config.toml>
      analyst-driver run --run <run_dir>

  Do one `kind = "dry"` pass first and read `BRIEF.md` and `decisions/` before
  spending any CASA time. Nothing has yet run with a real model or a real job.
- **Then**: the permissions work — designed with the user in conversation, not
  built. Three pieces, in order of value: (1) tighten the backend command in
  `config.toml` to read-only tools plus one Write for the decision file;
  (2) extend `ms_modify/pathguard.py` from "inside workdir" to a declared
  data-root and run-root allowlist, applied to every path-like parameter;
  (3) an approval gate — park before submit until a human acks — for the first
  few live runs. Outside the code: make the raw data read-only, and run under
  a dedicated account.
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
  ALMA needs a split into a new MS afterwards. The registry now handles a step
  producing a new MS, so the mechanism exists — what is missing is a tool that
  performs that split and declares the result as a planned output. Revisit when
  the alma branch is brought up to date.
- Nothing yet writes the `target` role. Imaging falls back to `raw`, which is
  correct for an unsplit run and wrong the moment a target split exists. The
  fallback is deliberate, but it will quietly image the wrong MS if a split
  tool lands without declaring `role: target`.

---

## 2026-08-12 — input and layout rework (BUILT)

The driver imposed an MS as the input and gave every step its own workdir. Both
were wrong. Built against `main` only; the alma branch comes later.

Two bugs the end-to-end smoke test caught that the unit tests did not:

- An evidence item with an empty `source` resolved to the run directory itself,
  and reading it raised `IsADirectoryError` out of the validator rather than
  refusing the decision. `check_evidence` now requires `is_file()`. A model can
  reach this by citing evidence it could not find, so it was a live crash.
- `DryExecutor` never created the planned outputs, so after this change every
  producing tool was harvested as FAILED in dry mode — correct behaviour from
  the new guard, but it would have made dry runs useless. Dry now fabricates
  each planned output (an MS as a directory with a `table.info`) and says so in
  its stdout, so a fabricated product is never mistaken for a real one.

The design as agreed and implemented:

**1. `processed/` is the single workdir.** Every data product — `calibrators.ms`,
`*.G`, `*.gc`, images — lands there. `steps/NNN-<tool>/` keeps the script, the
logs, `rc`, `measurements.json` and `step.json`, which are the provenance trail.

Per-step workdirs were not merely untidy. `ms_apply_preflag` splits calibrators
to `workdir/calibrators.ms` at step one, and `ms_workflow_status(ms_path,
workdir)` reads a single flat workdir by name. Per-step directories scatter the
products and break that tool. The dry executor never caught it because it never
ran the split.

**2. The input may be an ASDM or an MS.** `init --input PATH`, with `--ms` kept
as an alias. Kind is detected, not declared: `table.info` saying Measurement Set,
or an `ASDM.xml`. Anything else is refused at init.

**3. Two tools report their planned outputs.** `preflag.py:150` computes
`cal_ms` and `import_asdm.py` computes `ms_out` at generation time, but both put
the path in the response only on the `execute=True` path — which the driver never
uses. They must report it when `execute=False` too, flagged as planned. The
driver then verifies each planned path exists at harvest; one that never appeared
is a failed step, not a silent success. Globbing for `*.ms` was rejected: the
alma workflow produces several split MSs and a glob cannot tell them apart.

**4. `ms_role` in the whitelist, an MS registry in run.json.** The rule already
exists in the skills — calibration works on `calibrators.ms`, applycal writes the
target fields, imaging reads the targets. So it is data, not a decision:

    ms_apply_preflag  raw          ms_gaincal   calibrators
    ms_applycal       raw          ms_tclean    target

`run.json` holds `{raw, calibrators, target}`, filled as steps produce MSs, and
the driver resolves the role to a path. The model never names an MS, so this
opens nothing on the permissions side. An earlier draft had the model choose;
dropped.

**5. Preconditions come from `ms_workflow_status`, not from bespoke globs.** It
already computes `calibrators_ms_present`, `corrected_populated`,
`priorcals_present`, `initial_bandpass_present`, `final_caltables_present`,
`first_image_present`, and it reports `UNAVAILABLE` rather than `False` when a
probe fails. One call per tick feeds both the preconditions and section 2.

**Do NOT co-opt its `next_recommended_step`.** `workflow_status.py:98-110` is a
hardwired VLA ladder: it will not advance past `generate_priorcals` until
`gain_curves.gc` and `opacities.opac` exist. ALMA's priors are Tsys and WVR, so
on ALMA those files never appear, the tool answers `generate_priorcals` forever,
and it does not warn. Interactively that is a bad hint; in an automated loop it
is an infinite cycle that the cycle detector would turn into a parked run. The
recipe map remains the driver's answer to "what next".

Recipes get `ms_import_asdm` at the head, marked optional and dropped from the
rendered map when the input is already an MS.

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
