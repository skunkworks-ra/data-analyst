# driver — the external loop

Runs a CASA reduction as a sequence of long jobs, and calls a model only at the
decision points between them.

The model never waits on a job. It reads a brief, writes one JSON decision, and
exits. The driver generates the script, submits it, polls it, harvests the
result, and calls the model again. An eight-hour `tclean` costs two model calls,
not eight hours of held context.

## Install the command

From inside the repo, `pixi run analyst-driver …` works as a task.

You will normally want to drive a run from the directory the data lives in, and
`pixi run` cannot find its manifest from there. So put the wrapper on your PATH
once:

```bash
ln -s ~/src/skunkworks-ra/radio-analyst/bin/analyst-driver ~/bin/analyst-driver
```

It resolves the repo from its own location, so it works from anywhere, through
a symlink, with the same arguments as the task.

## Run it

```bash
analyst-driver init \
    --run-id 3c286_b6 \
    --ms  ~/data/3c286.ms \
    --goal "Calibrate and image 3C286, Band 6, Stokes IQUV continuum." \
    --recipe vla_continuum

analyst-driver run    --run <run_dir>   # loop until DONE
analyst-driver tick   --run <run_dir>   # one pass, then exit
analyst-driver status --run <run_dir>
```

`init` prints the run directory and the exact next command. Runs are created
under `[run] root` in `config.toml`; edit that before the first run.

Set `[executor] kind = "dry"` for the first pass. Scripts are generated but
never executed, so a whole run takes seconds — you get to read the briefs and
the decisions before any CASA time is spent.

`run` blocks, so use tmux and watch it with `status` from another shell.

`run` is a plain Python loop: tick, sleep `poll_seconds`, tick. Only that small
process sleeps. Use `tick` from your own bash loop if you would rather own the
scheduling:

```bash
while :; do
  analyst-driver tick --run "$RUN"; rc=$?
  [ $rc -eq 0 ] || exit $rc     # 10 = DONE, 20 = a human is needed
  sleep 60
done
```

Stop a run at the next tick with `touch <run_dir>/STOP`.

## What is where

| file | what it is |
|---|---|
| `PROMPT.md` | the static contract handed to the model at every step |
| `config.toml` | executor, backend, caps, poll interval |
| `whitelist.yaml` | the tools the model may call, their preconditions and probes |
| `recipe.yaml` | the usual order of steps, per telescope — a map, not a rule |
| `verifier.yaml` | the numeric checks, and the single source of truth for them |
| `driver.py` | the loop |
| `brief.py` | renders `BRIEF.md`, the model's whole view of the world |
| `validate.py` | refuses a bad decision before it costs compute |
| `verifier.py` | applies `verifier.yaml` to a finished step |
| `executors.py` | `local`, `slurm` or `dry` |
| `backends.py` | runs `claude` or `opencode`, once |
| `commit.py` | the five writes that must happen together |
| `state.py` | `run.json` |

## A run directory

```
<run_dir>/
  run.json               step, status, the job in flight — a cache of the rest
  BRIEF.md               regenerated at every wake
  PROMPT.md              frozen at init, so a repo edit cannot change a live run
  decisions/NNN.json     what the model chose, and the driver's provenance block
  steps/NNN-<tool>/      script, logs, rc, measurements.json, step.json
  reduction_log.jsonl    the clean path — only calls that succeeded
  reduction_replay.py    re-rendered from the ledger every turn
  cache/                 MS summary and instrument line
  STOP                   touch this to park the run at the next tick
```

The run directory is its own git repository, committed after every turn.

## The four actions

The model writes exactly one of `run`, `redo`, `done`, `ask`. Anything else is
refused. `ask` parks the run for a human.

## What stops a run

The driver enforces every limit. The model is never told the numbers, because a
model that knows it is short of budget starts trading away the science.

- the recipe reaches `done`
- the same `(tool, params)` reappears inside `cycle_window` steps
- `step_cap` or `wall_clock_hours`
- `max_refusals` invalid decisions at one step
- `STOP` exists
- the model says `ask`

A stop parks the run: `run.json` records `NEEDS_HUMAN` and a reason, and later
ticks do nothing until you clear it.

## Reproducibility

Every model call is recorded as measurements in, decision out. Two things follow:

- **Replay** — `reduction_replay.py` runs the recorded calls in order, with no
  model involved.
- **Diff** — rerun the same data with the model enabled and compare the new
  `decisions/` against the recorded ones.

Evidence is checked, not trusted. Every number the model cites must exist in the
file it cites, within 2 percent. A fabricated number is refused.

## Switching backend or executor

Both are one line in `config.toml`. The decision leaves the model through a file
whose path the prompt names, never through stdout, so a different CLI needs no
code change — only a command template.

Verify `opencode`'s real non-interactive flag before first use. The entry in
`config.toml` is a placeholder.

## Testing without CASA

Set `[executor] kind = "dry"`. Scripts are generated but never run, and every
step returns success at once. That exercises the brief, the validator, the
ledger and the replay script in seconds.
