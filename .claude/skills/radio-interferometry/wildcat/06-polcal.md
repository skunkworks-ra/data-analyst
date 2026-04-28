# 06 — Polarisation Calibration (Kcross → Df → Xf)

## Feasibility gate — run first

Run `ms_pol_cal_feasibility`. Do not proceed without it.

| Verdict | Action |
|---------|--------|
| `FULL` | Proceed through all steps (Kcross → Df/Df+QU → Xf → applycal) |
| `LEAKAGE_ONLY` | Skip setjy_polcal and Xf; run Kcross → Df+QU → applycal; no absolute EVPA |
| `DEGRADED` | Proceed with caution; annotate outputs with variability warning from `variability_note` |
| `NOT_FEASIBLE` | Do not proceed; report `blocker` field in summary; `next_stage=stop_polcal` |

PA spread edge case: if `pa_spread_deg` is 45°–60° and source is bright, use `Df+QU` (more robust at lower coverage). Document reduced coverage.

## Step sequence

| Step | Tool / gaintype | Key parameter | Prerequisite |
|------|-----------------|---------------|-------------|
| 1. Set pol model | `ms_setjy_polcal` | `reffreq_ghz`, `pol_freq_range` | delay.K + bandpass.B + gain.G exist |
| 2. Cross-hand delay | `ms_gaincal` gaintype=`KCROSS` | `combine='scan,spw'`, `smodel=[1,0,1,0]` | priorcals + K + B + G |
| 3. D-term leakage | `ms_polcal` poltype=`Df` or `Df+QU` | `combine='scan'`, `solint='inf'` | + kcross.K |
| 4. Position angle | `ms_polcal` poltype=`Xf` | `combine='scan'`, `solint='inf'` | + dterms.D |
| 5. Apply all | `ms_applycal` | `parang=True`, `calwt=False` | all 7 tables |

## Df vs Df+QU decision table

| Situation | Use |
|-----------|-----|
| 3C286 or 3C138, PA ≥ 45° | `Df` |
| 3C48 at S-band (model from fit_from_catalogue), PA ≥ 45° | `Df` |
| Calibrator Q,U unknown or uncertain, PA ≥ 45° | `Df+QU` |
| Phase cal used as leakage cal (polarisation unknown), PA ≥ 45° | `Df+QU` |
| PA coverage < 45° | D-term solve unreliable — flag; `next_stage=stop_polcal` |

`Df+QU` is safe on any source with PA ≥ 45°. Recovered Q/U are a sanity check: unpolarised → Q/U ≈ noise; polarised → coherent frequency structure.

## Quality thresholds

### Kcross
| Check | Threshold | Action |
|-------|-----------|--------|
| Kcross amplitude | < 2 ns | > 2 ns: flag in summary; check earlier calibration chain |

### D-terms (dterms.D)
| Amplitude | Assessment | Action |
|-----------|-----------|--------|
| < 5% | Good | Proceed |
| 5–10% | Marginal | Check for antenna-specific outliers; proceed with note |
| 10–20% | Elevated | Flag affected antennas in summary |
| > 20% | Flag antenna | Exclude from applycal |

Array-wide D-term > 10% is systematic — escalate.

### Xf (position angle)
| Check | Threshold | Action |
|-------|-----------|--------|
| PA residual | < 5° | Proceed |
| PA residual | 5°–10° | Check polangle[0] vs catalogue; check for RM at L-band |
| PA residual | > 10° | Check: wrong polangle model, RM wrapping, source variability (3C138 varies ~10°/yr) |

## Preferred Xf sources (VLA)
- 3C286: stable PA ~33° all bands — first choice
- 3C138: PA ~−14° at L-band; variable on year timescale — use with caution
- 3C48 at S-band: use when 3C286/3C138 not observed

## applycal table order (RIME required)

```
priorcals → delay.K → bandpass.B → gain.G → kcross.K → dterms.D → polangle.X
```

`parang=True` is mandatory. Omitting it silently corrupts all polarimetric results.

## Caltable names

| Table | Filename |
|-------|----------|
| Cross-hand delay | `kcross.K` |
| D-terms | `dterms.D` |
| Position angle | `polangle.X` |

## Decision summary

| Check | Pass | next_stage if failed |
|-------|------|---------------------|
| PA coverage (leakage cal) | ≥ 45° | `stop_polcal` |
| Kcross amplitude | < 2 ns | Note; continue |
| D-term median | < 5% | Note if < 10%; `escalate` if array-wide > 10% |
| D-term outlier antennas | 0–1 | Exclude > 20% D-term antennas from applycal |
| Xf PA residual | < 5° | Check polangle model / RM |
| `parang=True` in applycal | Always | Never omit |
