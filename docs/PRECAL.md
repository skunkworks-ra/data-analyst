# PRECAL.md — Pre-Calibration + Initial RFI Flagging Plan

## Context

Before the initial bandpass solve is scientifically valid, four preparatory steps
must run on the MS. This plan adds those steps as generate+verify tools following
the same contract established in REFANT_BP.md and retrofit.md. After the bandpass
script runs, the initial RFI pass uses rflag + tfcrop together in flagdata list mode
on residual data (CORRECTED − MODEL), not on raw DATA.

The evla scripted pipeline reference is:
- `stages/preflag.py::run_preflag`
- `stages/priorcals.py::run_priorcals`
- `stages/setjy.py::run_setjy`
- `stages/initial_rflag.py::run_initial_rflag`

---

## Slice 1 — ms_preflag

**Tool:** `ms_apply_preflag` — `src/ms_modify/preflag.py`
**Server:** `ms_modify`

### What it does

Applies all deterministic pre-calibration flags in a single `flagdata` pass,
then splits calibrator fields into `calibrators.ms`.

### Flagging steps (in order, all in one call)

1. **Online flags** from importasdm `.flagonline.txt` — `mode='list', inpfile=...`
2. **Shadow flags** — `mode='shadow', tolerance=0.0`
3. **Zero-amplitude clip** — `mode='clip', clipzeros=True`
4. **Conservative tfcrop** — `mode='tfcrop', timecutoff=3.0, freqcutoff=3.0`
   (broadband RFI only; fine RFI happens post-BP on residuals)
5. **Extend flags** across polarizations — `mode='extend', extendpols=True`

All five are applied via `flagdata(mode='list', inpfile=cmds.txt)` so they
run in a single pass — efficient and auditable from one file.

### Calibrator split

After flagging, split calibrator fields to `workdir/calibrators.ms`:
```python
split(vis=ms_path, outputvis=cal_ms, field=cal_fields, keepflags=False)
```
`keepflags=False` drops flagged rows entirely — downstream calibration never
sees them.

### Inputs

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `ms_path` | str | required | Full MS |
| `workdir` | str | required | Output directory |
| `cal_fields` | str | required | CASA field selection string for calibrators |
| `online_flag_file` | str | `""` | Path to `.flagonline.txt` from importasdm |
| `shadow_tolerance_m` | float | `0.0` | Shadow tolerance in metres |
| `do_tfcrop` | bool | `True` | Apply conservative tfcrop pass |
| `execute` | bool | `False` | Generate script only (False) or run (True) |

### Outputs

- `workdir/preflag_cmds.txt` — flagcmd list file
- `workdir/preflag.py` — self-contained script
- `data.cal_ms` in response — path to `calibrators.ms` (or None if not run)

### New inspect tool: `ms_online_flag_stats`

**Tool:** `src/ms_inspect/tools/online_flags.py`
One question: how many online flag commands exist and what do they cover?
Reads the `.flagonline.txt` file (pure text parse — no CASA needed).
Returns: n_commands, antennas flagged, reason code breakdown, time range.

---

## Slice 2 — ms_generate_priorcals

**Tool:** `ms_generate_priorcals` — `src/ms_modify/priorcals.py`
**Server:** `ms_modify`

### What it does

Runs `gencal` to generate the four deterministic prior calibration tables that
must be prepended to every subsequent gaincal/bandpass/applycal call.

### Tables generated (in order)

| Table | `caltype` | Required | Notes |
|-------|-----------|----------|-------|
| `gain_curves.gc` | `'gc'` | Yes | VLA elevation gain curves |
| `opacities.opac` | `'opac'` | Yes | Per-SPW zenith opacity from weather |
| `requantizer.rq` | `'rq'` | VLA post-2011 | MJD ≥ 55616.6 only; skip if absent |
| `antpos.ap` | `'antpos'` | No | Skip if gencal returns empty table |

### Output in response

```json
{
  "priorcals": [
    "/workdir/gain_curves.gc",
    "/workdir/opacities.opac",
    "/workdir/requantizer.rq"
  ],
  "skipped": ["antpos.ap"],
  "skip_reasons": {"antpos.ap": "gencal returned 0-row table"}
}
```

The `priorcals` list is the exact list to pass as `priorcals=` to
`ms_initial_bandpass`.

### Inputs

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `ms_path` | str | required | calibrators.ms (or full MS) |
| `workdir` | str | required | Output directory |
| `execute` | bool | `False` | Generate script only or run |

### Script output

`workdir/priorcals.py` — self-contained, generates all tables.

### New inspect tool: `ms_verify_priorcals`

**Tool:** `src/ms_inspect/tools/priorcals_check.py`
One question: do the prior caltables exist and are they non-empty?
Same pattern as `ms_verify_caltables` — filesystem + table `nrows()` check.

---

## Slice 3 — ms_setjy

**Tool:** `ms_setjy` — `src/ms_modify/setjy.py`
**Server:** `ms_modify`

### What it does

Sets flux density models for all standard VLA calibrators found in the MS,
using the Perley-Butler 2017 standard. Does **not** handle polarization angle
models (that is a CALPOL.md concern).

### Logic

1. Read field list from MS.
2. Cross-match against bundled calibrator catalogue (`util/calibrators.py`).
3. For each flux standard found, call `setjy(standard='Perley-Butler 2017')`.
4. Warn if field is 3C84 (resolved — should use component model) or
   3C138/3C48 (variable / partially polarized below 4 GHz).

### Script output

`workdir/setjy.py` — self-contained.

### Inputs

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `ms_path` | str | required | calibrators.ms |
| `workdir` | str | required | Output directory |
| `standard` | str | `'Perley-Butler 2017'` | Flux standard |
| `execute` | bool | `False` | Generate or run |

---

## Slice 4 — Initial RFI Flagging (rflag + tfcrop on residuals, list mode)

**Tool:** `ms_apply_initial_rflag` — `src/ms_modify/initial_rflag.py`
**Server:** `ms_modify`

### What it does

After the initial bandpass script has run and CORRECTED is populated,
runs rflag + tfcrop **together** on the **residual** column
(`datacolumn='residual'`, i.e. CORRECTED − MODEL) in a single
`flagdata(mode='list')` call.

### Why list mode

Running both in one `flagdata(mode='list', inpfile=...)` call:
- Single pass over the data — faster on large MSs
- Atomic: both flagging modes commit together in one flagmanager version
- Auditable: the list file is the complete record of what was done

### The flag command list file

```
mode='rflag' datacolumn='residual' timedevscale=5.0 freqdevscale=5.0 action='apply'
mode='tfcrop' datacolumn='residual' timecutoff=4.0 freqcutoff=4.0 action='apply'
```

Written to `workdir/initial_rflag_cmds.txt`. Script calls:
```python
flagdata(vis=ms, mode='list', inpfile='initial_rflag_cmds.txt', flagbackup=True)
```
`flagbackup=True` saves a versioned backup automatically (no separate
`flagmanager` call needed).

### Inputs

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `ms_path` | str | required | calibrators.ms (CORRECTED + MODEL must exist) |
| `workdir` | str | required | Output directory |
| `timedevscale` | float | `5.0` | rflag time deviation threshold |
| `freqdevscale` | float | `5.0` | rflag frequency deviation threshold |
| `timecutoff` | float | `4.0` | tfcrop time deviation threshold |
| `freqcutoff` | float | `4.0` | tfcrop frequency deviation threshold |
| `execute` | bool | `False` | Generate or run |

### Script output

- `workdir/initial_rflag_cmds.txt` — the flagcmd list
- `workdir/initial_rflag.py` — self-contained driver script

### New inspect tool: `ms_residual_stats`

**Tool:** `src/ms_inspect/tools/residual_stats.py`
One question: what does the amplitude distribution of CORRECTED − MODEL look like?
Reads CORRECTED and MODEL columns for the bandpass calibrator field and computes
per-SPW amplitude statistics. Use before rflag to choose thresholds, after rflag
to verify.

---

## Execution sequence

```
Slice 1: ms_apply_preflag         src/ms_modify/preflag.py + server.py
         ms_online_flag_stats      src/ms_inspect/tools/online_flags.py + server.py
         tests/unit/test_preflag.py

Slice 2: ms_generate_priorcals    src/ms_modify/priorcals.py + server.py
         ms_verify_priorcals       src/ms_inspect/tools/priorcals_check.py + server.py
         tests/unit/test_priorcals.py

Slice 3: ms_setjy                 src/ms_modify/setjy.py + server.py
         tests/unit/test_setjy.py

Slice 4: ms_apply_initial_rflag   src/ms_modify/initial_rflag.py + server.py
         ms_residual_stats         src/ms_inspect/tools/residual_stats.py + server.py
         tests/unit/test_initial_rflag.py

Final:   integration stubs         tests/integration/test_tools.py
         CLAUDE.md tool inventory  update table
```

---

## End-to-end skill flow after implementation

```
ms_observation_info(ms)                   → telescope, dates, duration
ms_apply_preflag(execute=False, ...)      → preflag.py + preflag_cmds.txt written
  [user runs preflag.py]
ms_online_flag_stats(...)                 → n_online_flags, reason breakdown
ms_flag_summary(...)                      → baseline flag fraction after preflag

ms_generate_priorcals(execute=False, ...) → priorcals.py written
  [user runs priorcals.py]
ms_verify_priorcals(...)                  → all 4 (or 3) tables present + valid

ms_setjy(execute=False, ...)             → setjy.py written
  [user runs setjy.py]

ms_refant(calibrators.ms)                → ranked refant list
ms_initial_bandpass(execute=False, ...)  → initial_bandpass.py written (with priorcals)
  [user runs initial_bandpass.py]
ms_verify_caltables(...)                 → init_gain.g + BP0.b valid

ms_apply_initial_rflag(execute=False, ...) → initial_rflag_cmds.txt + initial_rflag.py
  [user runs initial_rflag.py]
ms_flag_summary(...)                     → before/after delta
ms_residual_stats(...)                   → amplitude distribution check
```

---

## Key conventions carried forward

- All `ms_modify` tools: `execute=False` default, always write script first
- All flagging scripts: `flagbackup=True` or explicit `flagmanager save` before applying
- Script filenames are deterministic (no timestamps) — safe to re-generate and inspect
- `priorcals` list from `ms_generate_priorcals` is the canonical input to
  `ms_initial_bandpass`
