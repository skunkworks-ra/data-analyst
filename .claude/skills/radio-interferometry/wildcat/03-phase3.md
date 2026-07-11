# 03 — Phase 3: Diagnostic Routing and Go/No-Go

## Cross-consistency checks (run after all Phase 1+2 tools)

| Check | What to compare | Action if mismatch |
|-------|----------------|-------------------|
| Duration vs scan count | `total_duration_s` ≈ sum of scan durations | > 10% discrepancy: missing scans or gaps |
| Field count | `ms_field_list.n_fields` == unique field names in `ms_scan_list` | Mismatch: field defined but not observed |
| SpW count | `ms_spectral_window_list.n_spw` == `ms_correlator_config.n_spw` | Inconsistency: subtable corruption |
| Antenna vs baselines | n_baselines = n × (n−1) / 2 | Mismatch: orphaned antenna IDs |
| Flux cal time | `ms_scan_intent_summary` flux cal fraction | > 30%: likely not a science dataset |

## Go/No-Go verdict rules

**GO:** All Phase 1 and Phase 2 checks passed. No unresolved SUSPECT or UNAVAILABLE fields on critical quantities.

**GO WITH CONDITIONS:** One or more non-fatal issues present. Issue the appropriate condition:
- Resolved calibrator without component model → must set model before calibration
- Low-elevation scans → must flag before calibration
- PA coverage 30°–60° → polcal marginal; use `Df+QU`; document
- Flag fraction 20–40% → note and monitor; proceed
- 1–3 antennas > 30% flagged → note; may affect solution quality

**NO-GO:** Any of these conditions → do not proceed to calibration:
- `telescope_name` blank + antenna positions all at origin
- All antenna positions at (0,0,0)
- No flux calibrator present
- Initial bandpass caltables missing or invalid
- `gain_curves.gc` missing
- FLAG column corrupt (all reads fail)

## Polcal feasibility verdict (deterministic)

| `ms_pol_cal_feasibility` verdict | `next_stage` |
|----------------------------------|-------------|
| `FULL` | `proceed_polcal` |
| `LEAKAGE_ONLY` | `proceed_polcal_leakage_only` |
| `DEGRADED` | `proceed_polcal_with_caution` |
| `NOT_FEASIBLE` | `skip_polcal` |

## Antenna flag fraction routing

| Per-antenna flag fraction | Action |
|--------------------------|--------|
| > 80% | Exclude from refant candidates; note in summary |
| > 30% | Do not use as refant unless no other option |
| < 30% (and array median < 20%) | Normal |

## Flag chain: when a Phase 1 check fails

| Failed check | Effect on downstream |
|-------------|---------------------|
| No telescope name (1.1) | STOP Phase 2; all telescope-specific quantities are UNAVAILABLE |
| Numeric antenna names (2.1) | STOP Phase 2 steps 2.2–2.6; geometry unreliable |
| Any `INSUFFICIENT_METADATA` | Record exact repair command from error; do not infer |

## Summary format (for `summary` field in JSON contract)

Report these five facts in the summary field:
1. Telescope + config + band
2. Total duration + n_fields + n_spw
3. Worst flag fraction (overall and worst single antenna)
4. Calibrator roles present (GO) or missing (NO-GO trigger)
5. Go/No-Go verdict + conditions if any
