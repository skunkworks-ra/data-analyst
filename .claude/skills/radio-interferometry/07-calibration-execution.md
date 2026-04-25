# 07 — Calibration Execution

## Purpose

This document guides the full calibration solve sequence after the pre-calibration
workflow (skill 10) has produced a clean calibrators.ms with CORRECTED populated.

Sequence: initial phase → delay → bandpass → gain (flux) → gain (phase, append)
→ fluxscale → quality gate → applycal per field type.

## Execution protocol

Every tool call with `execute=False` generates a CASA script. That script is
then run as a background job. **Wait for it to finish, however long it takes.**
Do not impose artificial timeouts or retry counters. CASA calibration jobs
on real data are long-running by nature — patience and persistence are required.
Monitor by checking for the expected output (caltable on disk, return code 0)
after the job exits. Never kill a job because it appears slow.

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

### Recovery Tree 1: Caltable Not Produced

**Symptoms:** gaincal script completed, but `{WORKDIR}/gain.G` does not exist
OR exists but is empty (`n_total_solutions = 0`).

**Diagnostic path:**

1. **Check refant availability in the MS:**
   ```
   ms_antenna_list(ms_path={VIS})
   # Confirm {REFANT} name appears in antenna list
   ```
   If refant not found → use top 3 from `ms_refant` output, in order.

2. **Check prior caltables (delay.K, bandpass.B):**
   ```
   ms_verify_caltables(...)
   ```
   If either missing or empty → restart from Step 2 (delay solve).

3. **Check pre-solve flag fraction by field:**
   ```
   ms_flag_summary(ms_path={VIS}, field={FLUX_FIELD})
   ```
   - If > 50% of the data is flagged → escalate to **Pre-flagging review**
     (Section CALIBRATION_PREFLAG in 10-precal-workflow.md)
   - If < 50% but still high (30–50%) → gaincal may have failed due to insufficient SNR
     → Try **Retry option A** below

4. **Retry option A: Try alternative refants (recommended first)**
   - Run gaincal three times with refants from `ms_refant` ranked list (1st, 2nd, 3rd)
   - Each call: change only `refant={REFANT_N}` in the gaincal call; keep all other params
   - **If any produces a caltable:** use it and proceed to inspection
   - **If all three fail:** go to Retry option B

5. **Retry option B: Relax solution thresholds**
   - Change: `minsnr=3.0` (from 5.0) and `minblperant=3` (from 4)
   - Keep: same refant (use the ranked list's top antenna)
   - **If this produces a caltable:** note in summary as "degraded SNR, lower minblperant"
   - **If still fails:** escalate (see **Escalation** below)

6. **Escalation:** If all three refants + threshold relaxation fail, the gaincal
   solve is fundamentally broken. Possible causes:
   - Bandpass solve failed (re-do Step 3)
   - All major antennas are flagged (check for online flags or RFI; loop to precal)
   - Wrong field selection (re-check `{FLUX_FIELD}` from ms_field_list)
   - CASA/caltask version incompatibility (check CASA version)

---

### Recovery Tree 2: Low SNR (overall_snr_mean < 3.0)

**Symptoms:** gaincal produced a caltable, but `snr_mean < 3.0` or > 20% of
antennas have SNR < 2.0.

**Diagnostic path:**

1. **Assess the source and expected SNR:**
   - Use source classification (above) to decide if low SNR is expected
   - Bright cal (3C286, 3C147): SNR should be > 10; < 5 is bad
   - Phase cal (faint): SNR > 3 is acceptable
   - Weak source: SNR > 2 is acceptable if coverage is good
   - Resolved cal: SNR > 5 expected (extended structures need high SNR)

2. **Check if the problem is refant-dependent:**
   ```
   # Re-run gaincal with the 2nd and 3rd refants from ms_refant
   ```
   - **If SNR improves with a different refant:** use the better refant for all remaining steps
   - **If SNR remains low regardless of refant:** the data quality is poor; continue to next option

3. **Check if solint is too tight (too much vector-averaging):**
   - Current: `solint='inf'` (one solution over all scans)
   - **Retry with:** `solint='int'` (one solution per integration)
   - **Rationale:** Per-integration solutions have more data per fit; time variations
     are solved independently rather than averaged away
   - **Risk:** produces more solutions to flag; limits applycal interpolation
   - **If SNR improves:** use `solint='int'` and proceed
   - **If SNR stays low:** continue to next option

4. **Check bandpass quality:**
   - Return to Step 3 and inspect bandpass with `ms_calsol_stats`
   - If bandpass shows SNR < 5 itself → bandpass solve was weak
   - **Re-solve bandpass (Step 3)** with modified parameters, then re-attempt gain solve

5. **Check combine parameter (for weak sources only):**
   - Current: `combine=''` (no combining)
   - **Retry with:** `combine='scan'` (combine all scans on a calibrator)
   - **Rationale:** weak sources benefit from combining scans; single-scan solutions may have too few baselines
   - **Risk:** loses time resolution (may miss gain time-variation)
   - **Only for weak calibrators; do not use for flux calibrators**

6. **Escalation:** If SNR stays < 3 after trying different refants and modified solint:
   - The observation has insufficient data quality for reliable gain solutions
   - Possible causes: excessive RFI not caught by preflag, bad weather, equipment issue
   - **Action:** Document in summary, flag as "low SNR"; consider whether the dataset is usable

---

### Recovery Tree 3: Excessive Flag Jump (flag_delta > threshold)

**Symptoms:** gaincal completed and caltable exists, but `flag_after - flag_before`
exceeds the source-type threshold (e.g., > 8% for bright cal, > 12% for phase cal).

**Diagnostic path:**

1. **Classify the flag jump:**
   - Small jump (3–5% above threshold): Expected minor RFI detection; acceptable with note
   - Large jump (> 5% above threshold): Indicates real data-quality problem

2. **Check if the source is resolved:**
   - Use `ms_field_list` output and source catalogues (e.g., VLA calibrator manual)
   - Resolved sources (Cas A, Cyg A, Tau A, 3C84) are sensitive to UV range
   - **If resolved:** retry with tighter UV range
     ```
     # Add to gaincal call: uvrange='0~1000k' (or equivalent baseline limit)
     ```
   - **If point-like:** continue to next option

3. **Check for RFI in online flags:**
   ```
   ms_online_flag_stats(flag_file={ORIGINAL_ASDM}/.flagonline.txt)
   # Examine: reason_breakdown (look for RFI-like categories)
   ```
   - If heavy RFI flagging in online flags → data quality is already poor
   - Consider looping to precal for additional RFI excision

4. **Check per-antenna flag contribution:**
   ```
   ms_flag_summary(ms_path={VIS}, field={FLUX_FIELD}, per_antenna=True)
   # Identify antennas where flag_delta is largest
   ```
   - **If concentrated in 1–2 antennas:** suspect hardware issue on those antennas
     - Retry with `refant` set to an antenna NOT in the high-flag list
   - **If spread across all antennas:** systematic RFI; loop to precal

5. **Retry with longer solint (if source is unresolved):**
   - Current: `solint='inf'`
   - **Retry with:** `solint='10s'` or `solint='1min'` (example; adjust to data)
   - **Rationale:** shorter integrations capture more data per fit; longer solutions reduce outlier detection
   - **If flag_delta decreases:** use the longer solint
   - **If flag_delta unchanged:** the issue is not integration time; escalate

6. **Escalation:** If flag_delta remains > threshold after refant and solint tweaks:
   - Possible causes: strong RFI environment, broken receiver chain, sky interference
   - **Action:** Document the high flagging in the summary; consider whether the solutions are scientifically useful despite the flag delta

---

### Recovery Tree 4: Low Coverage (< 50% solutions)

**Symptoms:** gaincal produced a caltable, but `n_flagged_solutions / n_total_solutions > 0.5`
(more than half the solutions are flagged).

**Diagnostic path:**

1. **Check which antennas are missing:**
   ```
   ms_calsol_stats(caltable_path={WORKDIR}/gain.G)
   # Inspect: solutions_per_antenna (count non-flagged solutions per antenna)
   ```
   - **If 1–2 antennas are missing solutions:** likely refant issue or hardware problem
   - **If > 3 antennas missing:** data quality problem or field selection error

2. **Check if concentrated in a few antennas:**
   - **If yes:** try alternate refants (Recovery Tree 1, Retry A)
   - **If distributed:** continue to next option

3. **Relax minblperant:**
   - Current: `minblperant={MINBLPERANT}` (typically 4)
   - **Retry with:** `minblperant=3` (or lower)
   - **Rationale:** each antenna needs at least N baselines to contribute; lowering N allows peripheral antennas
   - **Risk:** weaker solutions with lower SNR
   - **If coverage improves to > 50%:** use this setting
   - **If still low:** continue to next option

4. **Check solint vs data density:**
   - If `solint='inf'` and the calibrator was observed in very few scans (< 3)
   - **Retry with:** `solint='int'` (per-integration solutions)
   - **Rationale:** more solutions per antenna across more integrations
   - **If coverage improves:** use per-integration solint
   - **If still low:** continue to next option

5. **Check prior caltables (delay.K, bandpass.B):**
   - Missing or bad prior solutions will cause downstream gaincal to fail
   - ```
     ms_verify_caltables(ms_path={VIS}, init_gain_table=..., bp_table={WORKDIR}/bandpass.B)
     ```
   - If bandpass is corrupt → restart from Step 3

6. **Escalation:** If coverage stays < 50% after all retries:
   - The dataset has fundamentally poor SNR or flagging
   - **Action:** Document and escalate to data-quality review

---

### Escalation criteria (hard stop conditions)

Stop and escalate to data-quality review if **any** of the following hold:

| Condition | Action |
|---|---|
| All 3 refants fail to produce a caltable | Check MS structure (antenna table, MAIN table consistency) |
| SNR stays < 2.0 after refant + solint + combine tries | Data quality too poor for this science goal |
| Bright flux calibrator flags jump > 20% | Strong RFI or hardware issue; loop to precal + online flags review |
| Coverage never reaches 50% | Possibly wrong field selection or MS corruption; verify with `listobs` |
| Refant not found in antenna list | MS antenna table is corrupt or incomplete |

When escalating: provide to the user:
- Which refant was tried, in order
- The SNR values or coverage % at each attempt
- The original `gain.G` output (if produced)
- A recommendation: retry preflag + RFI excision, or flag dataset as non-usable

---

## Step 5 — Inspect gain solutions

```
ms_calsol_stats(caltable_path = {WORKDIR}/gain.G)
```

The `gain.G` table contains solutions for both flux and phase calibrators. Use
`field_names` from the output to identify which field index corresponds to each.

| Field | Index | Threshold | Action if exceeded |
|---|---|---|---|
| `overall_flagged_frac` | scalar | < 0.08 | 0.08–0.15 → note; > 0.15 → loop to CALIBRATION_PREFLAG |
| `n_antennas_lost` | scalar | ≤ 1 | > 3 → check data quality and refant |
| `amp_std[ant, spw, field=flux_idx]` | flux cal | < 5% of `amp_mean` | > 15% → suspect antenna or RFI |
| `phase_rms_deg[ant, spw, field=flux_idx]` | flux cal | < 20° | > 45° → ionospheric or bad data |
| `amp_mean[ant, spw, field=flux_idx]` | flux cal | close to 1.0 | Large deviation → setjy model may be wrong |
| `amp_mean[ant, spw, field=phase_idx]` | phase cal | systematically higher than flux cal | Expected — fluxscale will correct this |
| `outliers.low_snr` | list | empty | non-empty → inspect named antennas; use Recovery Tree 2 if > 20% of antennas listed |
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

After fluxscale, gain amplitudes for both calibrators should be similar in
magnitude — the phase calibrator corrections should no longer be systematically
higher or lower than the flux calibrator.

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

## Step 7 — Apply calibration

Applycal is called separately for each field category to ensure the correct
gain solutions are interpolated correctly for each.

**Key parameters across all calls:**
- `gainfield`: selects which rows from `gain.fluxscaled` apply to each field
- `interp`: `'nearest'` for calibrators; `'linear'` for target (interpolate between adjacent cal scans)
- `calwt=False`: VLA data weights are not properly calibrated; calibrating them produces nonsensical results
- `applymode`: calibrators use `'calflagstrict'`; science target uses `'calflagstrict'` if
  per-calibrator flag fraction in `calibrators.ms` post-rflag is < 50%, otherwise `'calonly'`.
  `calonly` leaves data without a matching gain solution uncalibrated but unflagged — prefer it
  for the target when calibrator flagging was heavy.

### 7a — Flux calibrator

```
ms_applycal(
    ms_path    = {VIS},
    field      = {FLUX_FIELD},
    gaintable  = {PRIORCALS} + [delay.K, bandpass.B, gain.fluxscaled],
    gainfield  = [''] * len(PRIORCALS) + ['', '', {FLUX_FIELD}],
    interp     = [''] * len(PRIORCALS) + ['nearest,nearestflag', 'nearest', 'nearest'],
    calwt      = False,
    applymode  = 'calflagstrict',
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
    applymode  = 'calflagstrict',
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
    applymode  = 'calonly',        # or 'calflagstrict' if calibrator flag fraction < 50%
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

---

## parang parameter

Use `parang=True` in all gaincal, bandpass, and applycal calls, always.
It costs nothing and is required for correct polarization calibration later.
Omitting it when polcal is added later forces a full recalibration from scratch.
