# 07 — Calibration Execution

## Purpose

This document guides the full calibration solve sequence after the pre-calibration
workflow (skill 10) has produced a clean calibrators.ms with CORRECTED populated.

Sequence: initial phase → delay → bandpass → gain (flux) → gain (phase, append)
→ fluxscale → quality gate → applycal per field type.

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
| `{CENTER_CHANNELS}` | `ms_spectral_window_list` | central ~10% of channels per SPW, e.g. `'0:27~36'`; avoid edge channels |
| `{WIDE_CHANNELS}` | `ms_spectral_window_list` | wide channel range avoiding only rolloff edges, e.g. `'0:5~58'` |
| `{CORRSTRING}` | `ms_correlator_config` | `'RR,LL'` (circular) or `'XX,YY'` (linear) |
| `{REFANT}` | `ms_refant` | reference antenna name, e.g. `'ea09'` |
| `{FLUX_STANDARD}` | `ms_field_list` + band | see §Flux standards |
| `{MINBLPERANT}` | `ms_antenna_list` | 4 for VLA, 3 for compact arrays |
| `{INT_TIME_S}` | `ms_scan_list` | integration time in seconds |
| `{PRIORCALS}` | `ms_verify_priorcals` | list of prior caltable paths [antpos, rq, opac, ...] |
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

**After running:** solutions should show smooth phase variation with time and no
antennas completely flagged. A single integration with a phase jump on one antenna
is acceptable — do not re-solve. Phase RMS > 60° across integrations on most
antennas suggests a problem with the source model or the data column.

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

**Inspect delay values:**
- Delays relative to refant should be within ±30 ns for most arrays
- VLA: typically within ±5 ns after a recent configuration change
- Delays > ±50 ns on one antenna suggest a hardware or cabling problem — note in summary
- Delays of ±200+ ns may indicate a polarization feed swap — escalate

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

**After running — quality gate:**

| Metric | Good | Warning | Action |
|---|---|---|---|
| Overall flagged fraction | < 0.10 | 0.10–0.20 | > 0.20 → loop to CALIBRATION_PREFLAG |
| Antennas with no BP solutions | 0–1 | 2–3 | > 3 → check refant and bp_field selection |
| Phase residuals after K removal | < 10° RMS | 10–30° | > 30° → delay solve may have failed |

BP amplitudes should be smooth and close to 1.0 across channels, with the same
shape for both polarizations on a given antenna (within ~10%). Large amplitude
excursions at band edges are normal — these channels will be edge-flagged later.

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

**After running — quality gate:**

| Metric | Good | Warning | Action |
|---|---|---|---|
| Overall flagged fraction | < 0.08 | 0.08–0.15 | > 0.15 → loop to CALIBRATION_PREFLAG |
| Antennas lost (no solutions) | 0–1 | 2–3 | > 3 → check data quality and refant |
| Amplitude scatter per antenna | < 5% RMS | 5–15% | > 15% → suspect antenna or RFI |
| Phase scatter per antenna | < 20° RMS | 20–45° | > 45° → ionospheric conditions or bad data |

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
- `applymode='calflagstrict'`: flag data where any required solution is missing

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
    applymode  = 'calflagstrict',
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
