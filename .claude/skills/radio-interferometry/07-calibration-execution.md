# 07 — Calibration Execution

## Purpose

This document guides the full calibration solve sequence after the pre-calibration
workflow (skill 10) has produced a clean calibrators.ms with CORRECTED populated.

Sequence: initial phase → delay → bandpass → gain (flux) → gain (phase, append)
→ fluxscale → quality gate → applycal per field type.

## Execution protocol

Every `execute=False` tool call generates a CASA script that runs as a background
job. **Wait for it to finish, however long it takes** — do not impose timeouts or
retry counters; CASA solves on real data are long-running. Confirm success by the
expected output (caltable on disk, return code 0) after the job exits.

---

## Placeholder reference

Fill every `{PLACEHOLDER}` from Phase 1–2 tool outputs before calling any solve tool.

| Placeholder | Source tool | What to extract |
|---|---|---|
| `{VIS}` | `ms_observation_info` | absolute path to the calibrators MS |
| `{FLUX_FIELD}` | `ms_field_list` | field name of the primary flux/bandpass calibrator |
| `{BP_FIELD}` | `ms_field_list` | same as FLUX_FIELD unless a dedicated BP cal is present |
| `{PHASE_FIELD}` | `ms_field_list` | field name(s) of phase calibrators, comma-separated |
| `{TARGET_FIELD}` | `ms_field_list` | field name of the science target |
| `{CAL_FIELDS}` | `ms_field_list` | comma-separated names of ALL calibrators |
| `{ALL_SPW}` | `ms_spectral_window_list` | spw selection string, e.g. `'0~15'` |
| `{CENTER_CHANNELS}` | `ms_spectral_window_list` | from `ms_spectral_window_list.suggested.center_channels_string` |
| `{WIDE_CHANNELS}` | `ms_spectral_window_list` | from `ms_spectral_window_list.suggested.wide_channels_string` |
| `{CORRSTRING}` | `ms_correlator_config` | from `ms_correlator_config.corrstring_casa` |
| `{REFANT}` | `ms_refant` | reference antenna name, e.g. `'ea09'` |
| `{FLUX_STANDARD}` | `ms_field_list` + band | see §Flux standards |
| `{MINBLPERANT}` | `ms_antenna_list` | from `ms_antenna_list.recommended_minblperant` |
| `{INT_TIME_S}` | `ms_scan_list` | integration time in seconds |
| `{PRIORCALS}` | `ms_verify_priorcals` | from `ms_verify_priorcals.priorcals_list` |
| `{WORKDIR}` | provided in prompt | directory to write all caltables into |

---

## Field selection: names, never numeric IDs

Pass field **names** to every `field`, `reference`, `transfer`, and `gainfield`
argument — never numeric IDs. `split()` re-indexes fields (a `split(field='0,1,2,4')`
renumbers to `0,1,2,3`), so an ID correct on the parent MS silently selects the
wrong source on the split, corrupting the transfer with no error. Names survive
`split()` unchanged. Use the `{*_FIELD}` placeholders; if a tool warns of numeric
IDs, convert to names before proceeding.

---

## Flux standards by band

| Band | Frequency range | Standard | CASA name |
|---|---|---|---|
| P-band | 200–500 MHz | Scaife-Heald 2012 | `'Scaife-Heald 2012'` |
| L-band | 1–2 GHz | Perley-Butler 2017 | `'Perley-Butler 2017'` |
| S/C/X/Ku/K | 2–26 GHz | Perley-Butler 2017 | `'Perley-Butler 2017'` |
| Q-band+ | > 40 GHz | Perley-Butler 2017 | `'Perley-Butler 2017'` |

For VLA P-band with 3C147: use `standard='Scaife-Heald 2012'`. Perley-Butler 2017
does not cover P-band.

---

## solint guidance

| Solve step | solint | combine | Rationale |
|---|---|---|---|
| Initial phase (G0) | `'int'` | — | Per-integration; removes time-variable phase decorrelation before BP solve |
| Delay (K) | `'inf'` | `'scan'` | One delay per antenna over all time; delay is quasi-static |
| Bandpass (B) | `'inf'` | `'scan'` | One BP solution over all scans on the BP calibrator |
| Gain flux cal (G) | `'inf'` | — | One solution per scan; preserves scan-level structure |
| Gain phase cal (G, append) | `'inf'` | — | Same; appended to the same table |

For VLA P-band, the ionosphere varies on timescales of seconds — see 08-pband-specifics.md
for P-band-specific solint overrides.

### solint SNR check — run before Step 4

Before committing to `solint='inf'` for the gain solve, verify the predicted SNR
is adequate. Use the flux density from `ms_setjy` output (`{FLUX_JY}`).

```
ms_gaincal_snr_predict(
    ms_path        = {VIS},
    field_name     = {FLUX_FIELD},
    solint_seconds = -1,          # -1 = use full scan length (equivalent to solint='inf')
    snr_threshold  = 3.0,
    flux_jy        = {FLUX_JY},   # Stokes I flux density from ms_setjy
)
```

Read `predicted_snr_per_spw[antenna, spw]` from the output:

| Result | Action |
|--------|--------|
| All SPWs > 5.0 | Proceed with `solint='inf'` as planned |
| Any SPW 3.0–5.0 | Proceed; note the low-SNR SPWs in the run log |
| Any SPW < 3.0 | Try `solint_seconds` equal to your scan length / 2 in the tool; if still < 3.0, use `combine='scan'` in the gaincal call |

If `flux_jy` is UNAVAILABLE (setjy was not run or the source is not in the catalogue),
the tool returns UNAVAILABLE for all SNR fields. Skip the check and proceed; note it.

---

## Calibration table naming convention

Tables are written to `{WORKDIR}/`:

| Table | Filename | Notes |
|---|---|---|
| Initial phase | `initial_phase.G0` | Temporary; used only as prior for K and B |
| Delay | `delay.K` | Quasi-static; applied to all subsequent solves |
| Bandpass | `bandpass.B` | Final bandpass; replaces BP0.b from initial_bandpass |
| Gain (pre-fluxscale) | `gain.G` | Contains both flux and phase cal solutions |
| Gain (flux-scaled) | `gain.fluxscaled` | Output of fluxscale; applied to target |

---

## Caltable solution flagging (after each table, before it's applied)

Once a caltable is created and *before* it is applied on-the-fly as a prior in
the next solve, run `ms_flag_caltable` on it to catch RFI-contaminated outlier
solutions that still passed the solve-time SNR cut. This keeps a few bad
solutions from propagating into every downstream solve.

| Table | When | mode (auto) | sigma |
|---|---|---|---|
| `bandpass.B` | after Step 3, before Step 4 gain solve | tfcrop | 5.0 |
| `gain.G` | after Step 4, before fluxscale | rflag | 5.0 |
| `dterms.D` (polcal) | after the D-term solve (skill 09) | rflag | 5.0 |
| `delay.K` | — | — | **do not flag** — one value per antenna; inspect with `ms_calsol_stats` and flag bad antennas explicitly instead |

`ms_flag_caltable` auto-routes the mode from the table's VisCal type, so you
normally pass only `caltable_path`, `workdir`, and `sigma`. Default `sigma=5.0`
is gentle — it catches the worst outliers without over-flagging.

**Read the reported flagged fraction:**

| Flagged fraction after | Action |
|---|---|
| < 30% | Normal — outliers removed; proceed |
| ≥ 30% | The a-priori (visibility) flagging was insufficient. **Do not just loosen sigma.** Improve the upstream preflag/RFI excision and redo *this* solve. If it is still ≥ 30% after redoing, raise `sigma` to 6.0 |

The order matters: flagged caltable solutions are a *symptom* of unflagged RFI
in the visibilities. Loosening sigma hides the symptom; fixing the preflag
removes the cause.

---

## Step 1 — Initial phase calibration (G0)

**Purpose:** remove fast phase variations across time on the bandpass calibrator
before the bandpass solve. Without this, vector-averaging across integrations
de-correlates the bandpass solution.

**Call:**
```
ms_gaincal(
    ms_path     = {VIS},
    field       = {BP_FIELD},
    spw         = {CENTER_CHANNELS},       # central ~10% of channels only
    caltable    = {WORKDIR}/initial_phase.G0,
    gaintype    = 'G',
    calmode     = 'p',
    solint      = 'int',
    refant      = {REFANT},
    minsnr      = 5.0,
    minblperant = {MINBLPERANT},
    gaintable   = {PRIORCALS},
    workdir     = {WORKDIR},
    execute     = False,
)
```

**Inspect G0 solutions:**
```
ms_calsol_stats(caltable_path = {WORKDIR}/initial_phase.G0)
```

| Field | Index | Threshold | Action if exceeded |
|---|---|---|---|
| `phase_rms_deg[ant, spw=0, field=0]` | all antennas | < 60° | > 60° on most antennas → suspect source model or data column; do not proceed |
| `overall_flagged_frac` | scalar | < 0.15 | > 0.15 → check refant and CENTER_CHANNELS selection |
| `antennas_lost` | list | empty or 1 | > 1 → note antenna names; re-examine flagging |
| `outliers.low_snr` | list | empty | non-empty → inspect named antennas; low SNR on G0 is a warning, not a hard stop |

A single integration with a phase jump on one antenna is acceptable — do not re-solve.

---

## Step 2 — Delay calibration (K)

**Purpose:** solve for a single delay (phase slope vs frequency) per antenna per
polarization. This removes the bulk of the bandpass phase slope so that the bandpass
phase solutions in Step 3 are nearly flat.

**Call:**
```
ms_gaincal(
    ms_path     = {VIS},
    field       = {BP_FIELD},
    spw         = {WIDE_CHANNELS},         # wide range, avoid only rolloff edges
    caltable    = {WORKDIR}/delay.K,
    gaintype    = 'K',
    solint      = 'inf',
    combine     = 'scan',
    refant      = {REFANT},
    minsnr      = 5.0,
    minblperant = {MINBLPERANT},
    gaintable   = {PRIORCALS} + [initial_phase.G0],
    workdir     = {WORKDIR},
    execute     = False,
)
```

**Inspect K solutions:**
```
ms_calsol_stats(caltable_path = {WORKDIR}/delay.K)
```

| Field | Index | Threshold | Action if exceeded |
|---|---|---|---|
| `delay_ns[ant, spw, field=0, corr]` | all antennas | abs value < 30 ns | > 50 ns on one antenna → hardware or cabling problem; note in summary |
| `delay_rms_ns[spw, field=0]` | all SPWs | < 10 ns | > 10 ns → delay solve may have failed on that SPW |
| `overall_flagged_frac` | scalar | < 0.10 | > 0.10 → check WIDE_CHANNELS selection |
| `outliers.low_snr` | list | empty | non-empty → inspect named antennas; a delay SNR outlier often signals a hardware problem worth noting |

VLA typically shows delays within ±5 ns after a recent configuration change.
Delays of ±200+ ns on any antenna may indicate a polarization feed swap — escalate.

---

## Step 3 — Bandpass calibration (B)

**Purpose:** solve for the complex antenna response as a function of frequency.
Applied on-the-fly to all subsequent gain solves and to the science data at applycal.

**Call:**
```
ms_bandpass(
    ms_path     = {VIS},
    field       = {BP_FIELD},
    spw         = {ALL_SPW},
    caltable    = {WORKDIR}/bandpass.B,
    solint      = 'inf',
    combine     = 'scan',
    refant      = {REFANT},
    minsnr      = 3.0,
    minblperant = {MINBLPERANT},
    gaintable   = {PRIORCALS} + [initial_phase.G0, delay.K],
    interp      = [''] * len(PRIORCALS) + ['', 'nearest,nearestflag'],
    workdir     = {WORKDIR},
    execute     = False,
)
```

**interp note:** use `'nearest,nearestflag'` for the K table — linear interpolation
of a delay solution makes no physical sense and creates artifacts at scan edges.

**Inspect B solutions:**
```
ms_calsol_stats(caltable_path = {WORKDIR}/bandpass.B)
```

| Field | Index | Threshold | Action if exceeded |
|---|---|---|---|
| `overall_flagged_frac` | scalar | < 0.10 | 0.10–0.20 → note; > 0.20 → loop to CALIBRATION_PREFLAG |
| `n_antennas_lost` | scalar | ≤ 1 | 2–3 → check refant and bp_field; > 3 → hard stop |
| `phase_rms_deg[ant, spw, field=bp_field_idx]` | all antennas | < 10° | 10–30° → warn; > 30° → delay solve likely failed; re-run Step 2 |
| `amp_array[ant, spw, field=bp_field_idx, :]` | all antennas | smooth, ~1.0 | Large mid-band excursions → suspect antenna; edge roll-off is normal |
| `outliers.low_snr` | list | empty | non-empty → inspect named antennas; SNR < 3 on BP is a hard concern |
| `outliers.amp_outliers` | list | empty | non-empty → antenna has anomalous amplitude shape; check against `amp_array` for that antenna |

Both polarizations on a given antenna should show the same amplitude shape within ~10%.

**Drilling into an outlier:** `ms_calsol_stats` caps `outliers.low_snr` and
`outliers.amp_outliers` to the worst rows. When one of those lists names an
antenna (here or at any other calsol_stats check in this sequence) and you
need the full per-(antenna, SpW, field) picture, read the sidecar directly
with `ms_calsol_stats_detail(npz_path=..., kind='low_snr'|'amp_outliers'|'antenna',
antenna=..., spw=..., field=...)` rather than re-solving or re-running the plot.

**Before using `bandpass.B` in Step 4, flag its solutions** with `ms_flag_caltable`
(tfcrop, sigma=5.0) — see "Caltable solution flagging" above.

---

## Step 4 — Gain calibration, all calibrators

**Purpose:** solve for complex antenna gains on all calibrators in a single call.
Solving all fields together produces one table that fluxscale can directly use
to compare flux and phase calibrator solutions.

`solnorm=False` is required — we need the absolute amplitude scale. Gain
amplitudes for the flux calibrator should be close to 1.0 (setjy already set
the correct model). Phase calibrator amplitudes will be higher (fainter source
→ larger correction); fluxscale rescales them in Step 5.

**Call:**
```
ms_gaincal(
    ms_path     = {VIS},
    field       = '{FLUX_FIELD},{PHASE_FIELD}',  # all calibrators in one call
    spw         = {WIDE_CHANNELS},
    caltable    = {WORKDIR}/gain.G,
    gaintype    = 'G',
    calmode     = 'ap',
    solint      = 'inf',
    solnorm     = False,
    refant      = {REFANT},
    minsnr      = 3.0,
    minblperant = {MINBLPERANT},
    gaintable   = {PRIORCALS} + [delay.K, bandpass.B],
    interp      = [''] * len(PRIORCALS) + ['nearest,nearestflag', 'nearest'],
    parang      = True,
    workdir     = {WORKDIR},
    execute     = False,
)
```

---

## Step 4b — Gaincal recovery procedures

**When to use this section:** After running the gaincal call in Step 4, before
moving to inspection. If any of the four failure modes below are detected, use
the corresponding recovery tree to diagnose and retry with modified parameters.

### Source classification (pre-flight)

**Assigning `{PHASE_FIELD}`:** If multiple phase calibrators are present, use
`ms_field_list` and read `nearest_phase_cal.name` + `nearest_phase_cal.separation_deg`
for each target field. Assign the nearest phase calibrator to each target. If two targets
share the same nearest cal, use that cal for both.

Before the gaincal call, classify each field in `{FLUX_FIELD},{PHASE_FIELD}` into
one of four types. Use `ms_field_list` output and domain knowledge to decide.
This classification determines acceptable flag thresholds and recovery strategies.

| Source type | Characteristics | Pre-solve flag threshold | Acceptable flag-jump threshold |
|---|---|---|---|
| Bright flux cal (3C286, 3C147) | Strong, point-like; model well-known | ≤ 5% | ≤ 8% |
| Phase calibrator | Moderate strength; coherent structure expected | ≤ 10% | ≤ 12% |
| Weak calibrator | Faint but point-like; SNR limit | ≤ 15% | ≤ 15% |
| Resolved calibrator (Cas A, Cyg A, Tau A) | Extended structure; large UV range | ≤ 3% (high RFI sensitivity) | ≤ 6% |

**Decision:** If a field's pre-solve flag fraction (from `ms_flag_summary` before
gaincal call) exceeds the threshold for its type, see **Recovery 3: Excessive
flag jump** below before attempting gaincal.

### Pre-flight checklist

Before running the gaincal call in Step 4, verify:

1. **Refant availability:**
   ```
   ms_refant(ms_path={VIS}, field={FLUX_FIELD},{PHASE_FIELD})
   ```
   Confirm `{REFANT}` is in the returned ranked list (top 3 preferred).
   If not present, select the top-ranked antenna from the output.

2. **Prior caltables:**
   ```
   ms_verify_caltables(
       ms_path={VIS},
       init_gain_table={WORKDIR}/initial_phase.G0,
       bp_table={WORKDIR}/bandpass.B
   )
   ```
   Confirm both tables exist and are valid (`caltables_valid=True`).

3. **Pre-solve flag state:**
   ```
   ms_flag_summary(ms_path={VIS}, field={FLUX_FIELD},{PHASE_FIELD})
   ```
   Record `per_field.flag_fraction` for each calibrator. Use as baseline to
   detect flag jump post-solve (see **Post-flight validation** below).

### Post-flight validation (after gaincal completes)

After the gaincal script finishes, run these four checks:

**Check 1: Caltable existence and coverage**
```
ms_calsol_stats(caltable_path={WORKDIR}/gain.G)
```
Look for:
- `n_total_solutions` > 0 (solutions were actually computed)
- `n_flagged_solutions` / `n_total_solutions` < 0.5 (at least 50% coverage)
- If either fails → **Recovery 1: Caltable Not Produced**

**Check 2: SNR quality**
```
# From ms_calsol_stats output, inspect:
overall_snr_mean                 # Should be > 3.0, ideally > 5.0
outliers.low_snr                 # List of {antenna, spw, field, snr} entries below snr_min
```
- If `snr_mean < 3.0` → **Recovery 2: Low SNR**
- If `outliers.low_snr` is non-empty and covers > 20% of antennas → **Recovery 2: Low SNR** (refant retry)

**Check 3: Flag state comparison (delta check)**
```
ms_flag_summary(ms_path={VIS}, field={FLUX_FIELD},{PHASE_FIELD})
# Compare per_field.flag_fraction before and after gaincal
flag_delta = flag_after - flag_before
```
- For each field, calculate `flag_delta`
- Bright cal: acceptable if `flag_delta` ≤ 8%
- Phase cal: acceptable if `flag_delta` ≤ 12%
- Weak source: acceptable if `flag_delta` ≤ 15%
- Resolved: acceptable if `flag_delta` ≤ 6%
- If any field exceeds threshold → **Recovery 3: Excessive flag jump**

**Check 4: Solution distribution (outlier check)**
```
# From ms_calsol_stats, inspect:
outliers.amp_outliers            # List of {antenna, spw, field, amp, n_sigma} entries
```
- Expected: `outliers.amp_outliers` is empty; antenna-to-antenna amplitude variation ~20–30% is normal
- Red flag: one antenna appears in `amp_outliers` across multiple SPWs → **Recovery 1: Caltable Not Produced**
  (refant dependency issue) OR **Recovery 4: Low Coverage**

**If a post-flight check above routed you to a Recovery Tree, or to Escalation,
read `07b-gaincal-recovery.md`** — it holds the diagnostic procedures for Recovery
Trees 1–4 (Caltable Not Produced, Low SNR, Excessive Flag Jump, Low Coverage) and
the hard-stop Escalation criteria. The happy path does not need it.

---

## Step 5 — Inspect gain solutions

```
ms_calsol_stats(caltable_path = {WORKDIR}/gain.G)
```

The `gain.G` table contains solutions for both flux and phase calibrators. Use
`field_names` from the output to identify which field index corresponds to each.

**Before fluxscale (Step 6), flag the `gain.G` solutions** with `ms_flag_caltable`
(rflag, sigma=5.0) — see "Caltable solution flagging" above. Outlier gain
solutions left in place will bias the fluxscale transfer.

| Field | Index | Threshold | Action if exceeded |
|---|---|---|---|
| `overall_flagged_frac` | scalar | < 0.08 | 0.08–0.15 → note; > 0.15 → loop to CALIBRATION_PREFLAG |
| `n_antennas_lost` | scalar | ≤ 1 | > 3 → check data quality and refant |
| `amp_std[ant, spw, field=flux_idx]` | flux cal | < 5% of `amp_mean` | > 15% → suspect antenna or RFI |
| `phase_rms_deg[ant, spw, field=flux_idx]` | flux cal | < 20° | > 45° → ionospheric or bad data |
| `amp_mean[ant, spw, field=flux_idx]` | flux cal | close to 1.0 | Large deviation → setjy model may be wrong |
| `amp_mean[ant, spw, field=phase_idx]` | phase cal | systematically higher than flux cal | Expected — fluxscale will correct this |
| `outliers.low_snr` | list | empty | non-empty → inspect named antennas; use Recovery Tree 2 (07b) if > 20% of antennas listed |
| `outliers.amp_outliers` | list | empty | non-empty → inspect named antennas; an amplitude outlier on the flux cal is a hard flag before applycal |

---

## Step 6 — Flux scale transfer (fluxscale)

**Purpose:** rescale the phase calibrator gain amplitudes using the known flux
density of the primary calibrator. Produces `gain.fluxscaled` in which both
calibrators share the same amplitude scale.

**Call:**
```
ms_fluxscale(
    ms_path     = {VIS},
    caltable    = {WORKDIR}/gain.G,
    fluxtable   = {WORKDIR}/gain.fluxscaled,
    reference   = {FLUX_FIELD},
    transfer    = [{PHASE_FIELD}],
    incremental = False,
    workdir     = {WORKDIR},
    execute     = False,
)
```

**Check the returned flux density:** compare the derived flux density of the
phase calibrator against the VLA calibrator manual or known source monitoring.
Values deviating by > 20% from the expected value suggest a problem with the
prior caltables or the flux calibrator model.

**An order-of-magnitude-low flux (e.g. ~10× low) is the signature of a mixed
`usescratch` MODEL_DATA collision, not a calibration problem.** It happens when
`ms_setjy_polcal` (always `usescratch=True`) created the physical `MODEL_DATA`
column while the flux/bandpass cals were set with `ms_setjy(usescratch=False)`
(virtual) — leaving their `MODEL_DATA` at the default 1 Jy. fluxscale then
bootstraps off a 1 Jy reference instead of the true model flux. If you see this,
do not retune the solve: re-run `ms_setjy` with `usescratch=True` for the flux
cal so the whole MS uses one consistent physical model, then redo the gain solve
and fluxscale. The rule: all setjy calls on one MS must share the same
`usescratch` (see skill 09 Step 1).

After fluxscale, gain amplitudes for both calibrators should be similar in
magnitude — the phase calibrator corrections should no longer be systematically
higher or lower than the flux calibrator.

---

## Step 6b — Visual inspection: plot all caltables

After fluxscale completes, plot the full calibration library in one call.
This is a mandatory step — do not skip it to save time. The dashboards are
the primary record of calibration quality for the science archive.

```
ms_plot_caltable_library(
    caltable_paths = [
        {WORKDIR}/initial_phase.G0,
        {WORKDIR}/delay.K,
        {WORKDIR}/bandpass.B,
        {WORKDIR}/gain.G,
        {WORKDIR}/gain.fluxscaled,
    ],
    output_dir = {WORKDIR}/plots,
)
```

Check the returned `n_error` count first. If any table failed to plot, read
the `error` field for that entry — a missing table at this stage means a
prior solve step silently produced no output, which must be resolved before
applycal.

For each successfully plotted table, open the HTML dashboard and verify:

| Table | What to look for |
|-------|-----------------|
| `initial_phase.G0` | Phase RMS < 60° across all antennas; no single antenna wildly outlying |
| `delay.K` | Delays within ±30 ns; both polarizations consistent; heatmap mostly unflagged |
| `bandpass.B` | Smooth amplitude vs channel per SPW; edge roll-off expected; no mid-band spikes |
| `gain.G` | Flux cal amplitudes close to 1.0; phase cal amplitudes systematically higher (correct); phase RMS < 20° |
| `gain.fluxscaled` | Flux and phase cal amplitudes now comparable in scale |

If any dashboard shows a hard anomaly (antenna completely missing, half the
SPWs flagged in the heatmap, wildly non-smooth bandpass), stop and diagnose
before proceeding to applycal.

---

## Decision gate — proceed to applycal?

Advance to applycal only when all of the following hold:

| Condition | Threshold |
|---|---|
| BP flagged fraction | < 0.20 |
| Gain flagged fraction | < 0.15 |
| Antennas lost | ≤ 3 |
| fluxscale derived flux density | within 20% of expected |
| `gain.fluxscaled` exists on disk | confirmed |

If BP or gain flagged fraction exceeds threshold: loop to CALIBRATION_PREFLAG.
More RFI excision in the DATA column is needed before the solutions will be usable.

If `n_antennas_lost > 3`: do not loop blindly. Check whether the lost antennas
are consistently absent across all solve steps — if so, they are hardware-dead
for this observation. Flag them globally and re-solve.

---

## Antenna-set consistency check — run before Step 7

applycal applies a *stack* of caltables to each visibility. With `applymode='calflag'`,
a visibility is flagged if it has no usable solution in **any** table in the stack, so
the antennas that survive applycal are the **intersection** of the surviving antenna
sets across all stacked tables — not any single table's set. Each table can pass its
own `n_antennas_lost ≤ 3` gate while the intersection still collapses (observed on
AB1345: a target applycal reduced to a 5-antenna intersection). Nothing else in this
workflow intersects the sets, so the collapse is otherwise silent until it shows up as
gutted CORRECTED data — by which point the FLAG damage is done and there is no
flagversions rollback in this build.

Compute the intersection explicitly before applycal:

1. `ms_antenna_list(ms_path={VIS})` → array total `N` = number of antennas.
2. For each table in the applycal stack (`delay.K`, `bandpass.B`, `gain.fluxscaled`,
   plus any priorcals), run `ms_calsol_stats(caltable_path=...)` and compute the
   surviving set: `surviving = set(ant_names) − set(antennas_lost)`.
3. `common` = intersection of `surviving` across all tables.
4. `fraction = len(common) / N`.

Gate on `fraction` (denominator is the full array, from `ms_antenna_list`):

| `fraction` | Action |
|---|---|
| ≥ 0.50 | Antenna set is consistent — proceed to Step 7 |
| < 0.50 | Pathological collapse — **do not applycal blindly.** Branch on processing mode (below) |

**If `fraction < 0.50`:**

- **Interactive / intervention requested:** present this as a decision point. Report
  `N`, `len(common)`, and per-table which antennas each table dropped (the set
  difference `surviving_table − common`). Ask the user whether to diagnose the cause
  or apply anyway with the reduced set.
- **Hands-off / autonomous processing:** do **not** proceed to applycal. Treat the
  collapse as a signal of a pathological upstream failure and inspect:
  - a-priori flagging — `ms_flag_summary` per field/antenna, to find an antenna or SPW
    flagged out across the board;
  - the calibration solution logs and per-table `ms_calsol_stats`, to find an antenna
    that died across multiple solves (e.g. a bad refant, a feed swap, a hardware-dead
    antenna). Resolve the root cause and re-solve before applycal — switching
    `applymode` will not recover antennas that have no solution at all.

---

## Step 7 — Apply calibration

Applycal is called separately for each field category to ensure the correct
gain solutions are interpolated correctly for each.

**Key parameters across all calls:**
- `gainfield`: selects which rows from `gain.fluxscaled` apply to each field
- `interp`: `'nearest'` for calibrators; `'linear'` for target (interpolate between adjacent cal scans)
- `calwt=False`: VLA data weights are not properly calibrated; calibrating them produces nonsensical results
- `applymode`: default `'calonly'` for all fields — apply calibration without flagging, so
  post-calibration RFI flagging (skill 13, `ms_postcal_flag`) owns the FLAG column. Use
  `'calflagstrict'` only when you deliberately want apply-time flagging of missing/flagged
  solutions (e.g. a quick-look without a post-cal flag pass).

### 7a — Flux calibrator

```
ms_applycal(
    ms_path    = {VIS},
    field      = {FLUX_FIELD},
    gaintable  = {PRIORCALS} + [delay.K, bandpass.B, gain.fluxscaled],
    gainfield  = [''] * len(PRIORCALS) + ['', '', {FLUX_FIELD}],
    interp     = [''] * len(PRIORCALS) + ['nearest,nearestflag', 'nearest', 'nearest'],
    calwt      = False,
    applymode  = 'calonly',
    flagbackup = True,
    workdir    = {WORKDIR},
    execute    = False,
)
```

### 7b — Phase calibrator

```
ms_applycal(
    ms_path    = {VIS},
    field      = {PHASE_FIELD},
    gaintable  = {PRIORCALS} + [delay.K, bandpass.B, gain.fluxscaled],
    gainfield  = [''] * len(PRIORCALS) + ['', '', {PHASE_FIELD}],
    interp     = [''] * len(PRIORCALS) + ['nearest,nearestflag', 'nearest', 'nearest'],
    calwt      = False,
    applymode  = 'calonly',
    flagbackup = False,
    workdir    = {WORKDIR},
    execute    = False,
)
```

### 7c — Science target

```
ms_applycal(
    ms_path    = {VIS},
    field      = {TARGET_FIELD},
    gaintable  = {PRIORCALS} + [delay.K, bandpass.B, gain.fluxscaled],
    gainfield  = [''] * len(PRIORCALS) + ['', '', {PHASE_FIELD}],
    interp     = [''] * len(PRIORCALS) + ['nearest,nearestflag', 'nearest', 'linear'],
    calwt      = False,
    applymode  = 'calonly',        # post-cal RFI flagging (skill 13) owns FLAG
    flagbackup = False,
    workdir    = {WORKDIR},
    execute    = False,
)
```

**interp='linear' on the target:** the phase calibrator was observed at discrete
times bracketing the target scans. Linear interpolation gives the best estimate
of the gain at the target's observation time. Do not use 'nearest' for the target
— it discards temporal interpolation and produces step discontinuities at scan edges.

---

## Post-applycal assessment

After all three applycal calls complete, inspect the CORRECTED_DATA column:

| Check | Expected | Problem if not met |
|---|---|---|
| Flux cal amplitude vs frequency | Flat, close to model flux | BP solve failed or model column wrong |
| Flux cal phase vs frequency | Flat, near 0° | Delay solve failed |
| Phase cal amplitude vs uv-dist | Flat (point source) or consistent with expected structure | Flux scale wrong or source resolved |
| Phase cal phase vs time | Smooth, low scatter | Remaining RFI or antenna problem |

If the flux calibrator amplitude is not flat across frequency: re-examine the
bandpass solutions (Step 3) before re-running applycal.

If the phase calibrator shows anomalous time structure: consider flagging the
affected scans and re-running Steps 4–6 before re-applying.

**Residual rflag on the other calibrators belongs here.** This is the "later"
pass deferred in 10-precal-workflow.md Step 9: now that applycal has populated a
valid CORRECTED column for *all* calibrators (not just the bandpass cal), a
residual rflag pass on them is finally meaningful. Call `ms_apply_initial_rflag`
with `field` set to the calibrators whose CORRECTED is now valid — never an
all-field pass over fields that were not in this applycal. Re-inspect with
`ms_flag_summary` before/after.

---

## parang parameter

Use `parang=True` in all gaincal, bandpass, and applycal calls, always.
It costs nothing and is required for correct polarization calibration later.
Omitting it when polcal is added later forces a full recalibration from scratch.
