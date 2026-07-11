# 10 — Pre-Calibration Workflow

Use `ms_workflow_status(ms_path, workdir)` to probe where you are before starting any step.

## Purpose

This document guides decisions across the full pre-calibration sequence:
online flag assessment → preflag → priorcals → flux model → refant selection
→ initial bandpass → initial RFI flagging on residuals.

The goal is a clean calibrator-only MS with CORRECTED populated and a first
pass of RFI removed, ready for the full calibration solve.

---

## Sequence overview

```
ms_import_asdm(execute=False, ...)       → generate import_asdm.py
  → run import_asdm.py as background job; wait for completion however long it takes
ms_verify_import(ms_path, flag_file)     → confirm MS valid + .flagonline.txt present

ms_online_flag_stats(flag_file)          → assess online flags before applying
ms_apply_preflag(execute=False, ...)     → generate preflag.py + preflag_cmds.txt
  → run preflag.py as background job; wait for completion however long it takes
ms_flag_summary(calibrators.ms)          → establish baseline flag fraction
                                           (do NOT run in parallel with ms_antenna_flag_fraction)

ms_generate_priorcals(execute=False)     → generate priorcals.py
  → run priorcals.py as background job; wait for completion however long it takes
ms_verify_priorcals(workdir)             → confirm all required tables exist

ms_setjy(execute=False, ...)             → generate setjy.py
  → run setjy.py as background job; wait for completion however long it takes

ms_refant(calibrators.ms, field=bp_field)    → refant ranked on BP cal
ms_refant(calibrators.ms, field=phase_field) → refant ranked on phase cal (pick antenna healthy on both)
ms_initial_bandpass(execute=False, ...)   → generate initial_bandpass.py
  → run initial_bandpass.py as background job; wait for completion however long it takes
ms_verify_caltables(...)                  → confirm init_gain.g + BP0.b valid
ms_plot_caltable_library(               → plot both caltables; review before proceeding
    caltables=[init_gain.g, BP0.b],
    workdir=workdir,
)

ms_residual_stats(field_id=bp_field_id)  → inspect amplitude distribution
ms_apply_initial_rflag(execute=False)    → generate initial_rflag.py
  → run initial_rflag.py as background job; wait for completion however long it takes
ms_flag_summary(calibrators.ms)          → before/after flag delta
```

---

## Step 0 — ASDM ingestion

Run `ms_import_asdm` before anything else if starting from raw ASDM data.

**Always generate the script first (`execute=False`).** The script is short —
review it to confirm the output paths are correct before running.

Fixed parameters (not configurable):
- `ocorr_mode='co'` — cross-correlations only; auto-correlations are dropped
- `savecmds=True` — online flags written to `<ms_name>.flagonline.txt`
- `applyflags=False` — flags are NOT applied during import; `ms_apply_preflag` owns flagging

After running `import_asdm.py`, call `ms_verify_import` to confirm:

| Check | Pass | Fail |
|-------|------|------|
| `ms_valid` | True | MS directory exists but no `table.info` — import failed mid-run |
| `flag_file_exists` | True | `.flagonline.txt` absent — re-run with `savecmds=True` |
| `flag_file_n_commands` | > 0 | Empty file — no online flags recorded (unusual for VLA; note it) |
| `ready_for_preflag` | True | All three above must pass before proceeding |

Pass `online_flag_file` from the `ms_import_asdm` response directly to
`ms_apply_preflag` as its `online_flag_file` parameter.

---

## Step 1 — Online flag assessment

Run `ms_online_flag_stats` on the `.flagonline.txt` file before applying flags.

| Output | What to check | Action |
|--------|--------------|--------|
| `n_commands` | Total online flag commands | > 500 is high — note in summary |
| `n_antennas_flagged` | Number of antennas with online flags | > 5 is elevated — inspect reason breakdown |
| `reason_breakdown` | Per-reason flag counts | `ANTENNA_NOT_ON_SOURCE` dominant = normal; `OUT_OF_RANGE` or `SUBREFLECTOR_ERROR` = antenna problem |

Online flags are applied as-is — do not edit them. They are deterministic hardware records.

---

## Step 2 — Pre-calibration flagging and calibrator split

`ms_apply_preflag` applies five flagging steps in a single pass and splits
calibrators to `calibrators.ms`.

**Always generate the script first (`execute=False`).** Review `preflag_cmds.txt`
before running — it is the complete audit record of what will be flagged.

After running, call `ms_flag_summary(calibrators.ms)` to establish the baseline:

| Flag fraction after preflag | Assessment |
|----------------------------|-----------|
| < 10% | Clean dataset — proceed normally |
| 10–25% | Moderate — note per-antenna distribution; check for outlier antennas |
| 25–40% | Elevated — inspect which reason dominates (shadow? online flags? tfcrop?) |
| > 40% | High — identify the driver before proceeding; may indicate hardware problem |

If a single antenna accounts for > 80% of the flagging, consider excluding it
from `refant` candidates and noting it in the summary.

---

## Step 3 — Prior calibration tables

`ms_generate_priorcals` generates four deterministic tables. After running,
verify with `ms_verify_priorcals`:

| Table | Required | If missing |
|-------|----------|-----------|
| `gain_curves.gc` | Yes — hard stop | Data cannot be calibrated without elevation gain curves |
| `opacities.opac` | K-band and above | Empty table: hard stop at K-band and above; note and continue below K-band |
| `requantizer.rq` | VLA post-2011 only | Safe to skip for pre-WIDAR data; note in summary |
| `antpos.ap` | No | Skip silently if gencal returns empty; note if returned with rows |

A missing `gain_curves.gc` or `opacities.opac` means `gencal` could not access
the telescope correction database. Do not proceed — escalate.

---

## Step 4 — Flux density model (setjy)

`ms_setjy` sets Stokes I flux models for all flux standard calibrators found.

**Check the warnings field in the response:**

| Warning | Action |
|---------|--------|
| `CALIBRATOR_RESOLVED_WARNING` | Use the component model listed in the warning, not a point source model |
| 3C84 present | Set `uvrange='>5klambda'` in initial bandpass to exclude extended emission |
| 3C138 present at K/Ka/Q | Source was in flare in early 2025 — note in summary; flux scale may be affected |
| 3C48 present below 4 GHz | PA is unstable at these frequencies — viable for Stokes I only |

The returned `flux_fields` list confirms which fields received a model. If a
flux calibrator is missing from this list, check that its field name matches
the catalogue (use `ms_field_list` cross-match for disambiguation).

**If polarization calibration is in scope for this dataset, call `ms_setjy` with
`usescratch=True`** (default is `False`). `ms_setjy_polcal` forces `usescratch=True`
later, and `usescratch` cannot be mixed within one MS without corrupting the flux
scale. Set it consistently from the start. See skill 09 Step 1 and skill 07 Step 6.

---

## Step 5 — Reference antenna selection

**Score the refant on every calibrator it must serve, not just the bandpass
calibrator.** `ms_refant`'s flag heuristic is computed only over the `field` you
pass it. An antenna can rank top on the bandpass cal yet be heavily flagged on
the phase cal — different scans, elevation, and RFI — and scoring on the BP field
alone is blind to that. The same refant is used for all solves (G0, K, B, and the
gain solve over flux+phase cals), so it must be well populated on all of them. The
phase cal especially: the science target inherits its gain solutions, so a refant
weak on the phase cal corrupts the target even when the bandpass looks perfect.

Run `ms_refant` once per calibrator field and choose the antenna that ranks well
on **all** of them:

```
ms_refant(calibrators.ms, field=bp_field)      → ranked list scored on the BP cal
ms_refant(calibrators.ms, field=phase_field)   → ranked list scored on the phase cal
```

A `flag_score` near 0 means the antenna is fully flagged on that field — never
pick it, however high it ranks on another. Prefer the highest-ranked antenna with
a healthy `flag_score` in every list over the top pick of any single list.

| Condition | Action |
|-----------|--------|
| Candidate `flag_score` near 0 on *any* calibrator field | Disqualify — dead on that field; drop to the next antenna healthy on all fields |
| Top-ranked antenna flagged > 30% (from `ms_flag_summary`) | Use rank-2 antenna |
| Top-ranked antenna has `flag_score` ≫ `geo_score` (periphery of array) | Note this — peripheral refants can cause phase-wrapping on long baselines at high freq |
| Only one heuristic used (completeness flag `INFERRED`) | Accept result but note lower confidence |
| Fewer than 3 antennas scored (`n_antennas` < 3) | Escalate — array is too sparse for reliable refant selection |

Record the top 3 cross-field candidates. If a solve fails or a gain solve flags
heavily, first check whether the refant is weak on that solve's field, then try
the next cross-field candidate before changing other parameters.

For 3C84 observations: pass `uvrange='>5klambda'` to `ms_initial_bandpass`
regardless of refant choice. The extended emission contaminates solutions on
short baselines independent of the reference antenna.

---

## Step 6 — Initial bandpass

`ms_initial_bandpass` runs three CASA tasks in sequence (gaincal → bandpass → applycal)
and populates the CORRECTED column.

**This tool runs casatasks — it can take several minutes on large MSs.**

**Set `applycal_field` to the bandpass calibrator field at this stage.** It is a
required argument with no default. Gains are solved on the bandpass calibrator
only (`bp_field`), so it is the only field with a valid solution. The Step 3
applycal must be restricted to that field — applying to other fields under the
default `applymode='calflagstrict'` flags them for lack of a matching solution
and silently corrupts the FLAG state of every non-BP calibrator (observed on
AB1345). There is no flagversions rollback in this build, so the only recovery
is to re-split `calibrators.ms`. Pass `applycal_field=bp_field` here; reserve
`applycal_field=''` (all fields) for stages where all fields genuinely have
solutions.

If the before/after `ms_flag_summary` delta on the bandpass calibrator is still
unexpectedly high, re-run with `applymode='calflag'` (softer — does not flag on
partial per-polarization solutions).

**Never re-run `ms_initial_bandpass` before the final gain/pol solves.** It is a
one-shot bootstrap to populate CORRECTED for residual rflag on the BP cal — not
something to iterate. If you need a clean FLAG state for the final solves,
re-split `calibrators.ms` rather than re-running this tool.

After the script completes, verify with `ms_verify_caltables`:

| Check | Pass | Fail |
|-------|------|------|
| `init_gain.g` exists | Has rows | Missing or 0 rows → re-run with different `bp_scan` selection |
| `BP0.b` exists | Has rows | Missing or 0 rows → check `bp_field` selection and `refant` |
| `corrected_written` | True | False → applycal silently failed; check MS permissions |

After verification passes, call `ms_plot_caltable_library` on both tables:

```python
ms_plot_caltable_library(
    caltables=[f"{workdir}/init_gain.g", f"{workdir}/BP0.b"],
    workdir=workdir,
)
```

Inspect the HTML dashboard before proceeding. Key things to look for:

| Plot | What to check | Action if bad |
|------|--------------|---------------|
| `init_gain.g` amplitude vs time | Should be smooth, all antennas tracking together | Outlier antenna → exclude from refant list |
| `init_gain.g` phase vs time | Should be near-zero (pre-bandpass short solution) | Large phase jumps → check scan selection; try shorter solint |
| `BP0.b` amplitude vs channel | Should be smooth per antenna; RFI shows as spikes | Persistent spike channels → note for `ms_rfi_channel_stats` after rflag |
| `BP0.b` phase vs channel | Linear slope across band is acceptable; sharp discontinuities are not | Discontinuity → possible missed delay; verify `init_gain.g` passed |
| Flagged fraction in plots | Any antenna with > 30% flagged solutions | If ≥ 1 antenna fully absent from plots → re-check refant choice |

If `ms_plot_caltable_library` returns error entries (any table in the `errors` field), do not proceed — re-run the initial bandpass step.

If either caltable is missing, do not proceed to rflag. Diagnose:
1. Was `bp_field` correct? Cross-check against `ms_field_list` intents.
2. Was the refant heavily flagged on the bandpass calibrator scans? Try rank-2.
3. Were prior caltables passed in `priorcals`? If `opacities.opac` was missing earlier, this will fail silently.

---

## Step 7 — Residual amplitude inspection

Before applying rflag, call `ms_residual_stats(field_id=<bp_field_id>)` to
inspect the CORRECTED − MODEL amplitude distribution.

| p95_amp / median_amp ratio (per SPW) | Interpretation |
|--------------------------------------|---------------|
| < 3 | Clean — bandpass solve succeeded; RFI tail is minimal |
| 3–8 | Moderate tail — rflag at default thresholds (5.0, 5.0) is appropriate |
| > 8 | Heavy tail — strong RFI present; consider running `ms_rfi_channel_stats` first to identify the worst SPWs before rflag |
| Ratio varies widely across SPWs | Band-specific RFI pattern — note which SPWs are worst |

A high ratio in a specific SPW (e.g. SPW 0 at L-band) often indicates persistent
narrowband RFI (GPS, GSM). Cross-check with `ms_rfi_channel_stats` annotations.

---

## Step 8 — Initial RFI flagging on residuals

**`ms_apply_initial_rflag` requires a `field` argument — there is no all-field
default, by design.** Residual rflag is only meaningful on a field whose
CORRECTED column is genuinely calibrated at the point you call it.

**At this stage that field is the bandpass calibrator only (`field={BP_FIELD}`).**
`ms_initial_bandpass` solves gains on the bandpass calibrator alone, so it is the
only field with a valid CORRECTED column right now. On every other field,
CORRECTED−MODEL is dominated by uncorrected gain/phase error — not RFI — so a
residual pass there flags almost the entire field (observed: 9.2% → ~90% overall,
phase cals ~97%), recoverable only by re-splitting the calibrators.

Residual rflag on the *other* calibrators happens later (see 07-calibration-execution.md),
once the full gain solve + applycal has populated valid CORRECTED for them — at
that point you pass those fields instead. The rule is invariant; the specific
field changes with the stage.

`ms_apply_initial_rflag` runs rflag + tfcrop on the residual column in a single
flagdata list-mode pass. `flagbackup=True` saves a versioned backup automatically.

If the phase calibrator minimum elevation during its scans (from `ms_elevation_vs_time`
Phase 2 output) is < 30°, use `timedevscale=7, freqdevscale=7` instead of the
defaults (5, 5). Elevated system temperature at low elevations inflates scatter
and causes false positives at tighter thresholds.

Call `ms_flag_summary` before and after. Assess the flag delta:

| Flag fraction increase (delta) | Assessment |
|-------------------------------|-----------|
| < 5% | Light RFI — dataset is clean; proceed to full calibration |
| 5–15% | Moderate RFI — normal for L-band and below; proceed |
| 15–30% | Heavy RFI — note which fields/SPWs drove the flagging; proceed with caution |
| > 30% | Severe — do not proceed blindly; identify the driver (single bad scan? persistent SPW?) |

If the delta is > 30%, check whether a single scan or SPW accounts for most of it.
A single bad scan can be excluded and the rflag repeated without it.

---

## Decision gate — proceed to full calibration solve?

After Step 8, make a go/no-go decision:

| Condition | Decision |
|-----------|----------|
| Overall flag fraction < 30% AND no caltable failures | Proceed to calibration solve |
| Overall flag fraction 30–50% | Proceed with explicit note in summary |
| Overall flag fraction > 50% | Escalate — dataset may not calibrate well |
| Per-calibrator flag fraction ≥ 50% on the gain calibrator | Relax rflag thresholds and re-run Step 8 — do not proceed |
| `gain_curves.gc` missing | Hard stop — do not proceed |
| `opacities.opac` missing at K-band and above | Hard stop — do not proceed |
| Initial bandpass caltables invalid | Hard stop — diagnose before proceeding |
| Specific antenna > 80% flagged | Note and exclude from refant; proceed |

**Refant check:** After Step 8, re-check the chosen refant's flag fraction in
`calibrators.ms`. If it exceeds 50%, switch to the next antenna in the
`refant_list` from `ms_refant` and re-run Step 6 (initial bandpass) before
passing to the calibration solve.

Pass the following forward to the calibration solve:
- `refant`: top-ranked surviving antenna from `ms_refant`
- `priorcals`: list from `ms_verify_priorcals`
- `bp_field`: the bandpass calibrator field selection
- `workdir`: directory containing `init_gain.g` and `BP0.b`
