# 05 — Calibration Solve (K/G/B sequence)

## Solve sequence

| Step | Table | gaintype | solint | combine | Applied-on-top-of |
|------|-------|----------|--------|---------|-------------------|
| G0 initial phase | `initial_phase.G0` | G/p | `'int'` | — | priorcals |
| K delay | `delay.K` | K | `'inf'` | `'scan'` | priorcals + G0 |
| B bandpass | `bandpass.B` | B | `'inf'` | `'scan'` | priorcals + G0 + K |
| G gain (all cals) | `gain.G` | G/ap | `'inf'` | — | priorcals + K + B |
| fluxscale | `gain.fluxscaled` | — | — | — | from gain.G |

Always pass `parang=True` on every gaincal/bandpass/applycal call.
Use `interp='nearest,nearestflag'` for the K table in all downstream calls.

## Flux standard by band

| Band | Frequency | Standard |
|------|-----------|---------|
| P | 200–500 MHz | `'Scaife-Heald 2012'` |
| L / S / C / X / Ku / K / Q | ≥ 1 GHz | `'Perley-Butler 2017'` |

## SNR pre-check (before Step G)

Run `ms_gaincal_snr_predict` with `solint_seconds=-1`:

| `predicted_snr` | Action |
|-----------------|--------|
| All SPWs > 5.0 | Proceed with `solint='inf'` |
| Any SPW 3.0–5.0 | Proceed; note low-SNR SPWs |
| Any SPW < 3.0 | Try `solint = scan_length / 2`; if still < 3.0 use `combine='scan'` |
| UNAVAILABLE | Skip check; proceed; note |

## Inspection thresholds

### G0 (initial_phase)
| Field | Threshold | Action |
|-------|-----------|--------|
| `phase_rms_deg` all antennas | < 60° | > 60° most antennas: STOP — suspect source model |
| `overall_flagged_frac` | < 0.15 | > 0.15: check refant + CENTER_CHANNELS |

### K (delay)
| Field | Threshold | Action |
|-------|-----------|--------|
| `delay_ns` any antenna | abs < 30 ns (VLA typical) | > 200 ns: ESCALATE — possible feed swap |
| `overall_flagged_frac` | < 0.10 | Check WIDE_CHANNELS selection |

### B (bandpass)
| Field | Threshold | Next stage if exceeded |
|-------|-----------|----------------------|
| `overall_flagged_frac` | < 0.10 | 0.10–0.20: note; > 0.20: `next_stage=loop_to_preflag` |
| `n_antennas_lost` | ≤ 1 | 2–3: check refant/bp_field; > 3: STOP |
| `phase_rms_deg` | < 10° | 10–30°: warn; > 30°: re-run K step |
| `outliers.low_snr` non-empty | empty | SNR < 3 on BP: hard concern |

### G (gain)
| Field | Threshold | Next stage if exceeded |
|-------|-----------|----------------------|
| `overall_flagged_frac` | < 0.08 | 0.08–0.15: note; > 0.15: `next_stage=loop_to_preflag` |
| `n_antennas_lost` | ≤ 1 | > 3: check data quality + refant |
| `amp_std / amp_mean` (flux cal) | < 5% | > 15%: suspect antenna or RFI |
| `phase_rms_deg` (flux cal) | < 20° | > 45°: ionospheric or bad data |
| `amp_mean` (flux cal) | ≈ 1.0 | Large deviation: wrong setjy model |
| `amp_mean` (phase cal) | > flux cal | Expected — fluxscale corrects |

## Fluxscale check

Derived flux density of phase calibrator must be within 20% of expected value from VLA calibrator manual.
If > 20% deviation: prior caltables or flux model suspect — do not proceed to applycal.

## Decision gate — proceed to applycal?

Thresholds vary by band. Use the row matching the observed band:

| Band | BP flagged frac limit | Gain flagged frac limit |
|------|-----------------------|-------------------------|
| P (200–500 MHz) | < 0.60 | < 0.60 |
| L (1–2 GHz) | < 0.60 | < 0.60 |
| S (2–4 GHz) | < 0.40 | < 0.40 |
| C / X (4–12 GHz) | < 0.20 | < 0.15 |
| All other bands | < 0.40 | < 0.40 |

If BP or gain flagged fraction exceeds the band threshold → `next_stage=CALIBRATION_PREFLAG`.

Additional gates (all bands):

| Condition | Threshold | Action if failed |
|-----------|-----------|-----------------|
| Antennas lost | ≤ 3 | If consistently absent: flag globally, retry solve |
| fluxscale flux deviation | < 20% | Do not applycal — `next_stage=CALIBRATION_PREFLAG` |
| `gain.fluxscaled` on disk | confirmed | Re-run fluxscale |

## Recovery priority order (caltable not produced / low SNR)

1. Try top-3 refants from `ms_refant` in order
2. Relax: `minsnr=3.0`, `minblperant=3`
3. For low SNR only: try `combine='scan'` (weak cals only, never flux cal)
4. If all fail: `next_stage=escalate`

## Escalation hard stops

| Condition | next_stage |
|-----------|-----------|
| All 3 refants fail to produce caltable | `escalate` |
| SNR < 2.0 after refant + solint + combine | `escalate` |
| Bright flux cal flag jump > 20% | `loop_to_preflag` |
| Coverage never reaches 50% | `escalate` |
