# 08 — Critical Failure Modes and Escalation

## Unrecoverable — NO-GO conditions

| Condition | Symptom | next_stage |
|-----------|---------|-----------|
| `telescope_name` blank AND all antenna positions at origin | Identity + geometry both missing | `stop — metadata repair required` |
| All antenna positions at (0, 0, 0) | `max_baseline_m < 10` | `stop — UV coords uncomputable` |
| No flux calibrator present and no cross-calibration MS | No flux scale, bandpass, or phase solutions | `stop — uncalibratable` |
| FLAG column corrupt | `tb.getcolslice` fails on all rows | `stop — corrupt FLAG column` |
| Single-scan MS with no calibrators | Confirmed by `ms_field_list` + `ms_scan_list` | `stop — no calibration possible` |

## High-priority recoverable failures

### UVFITS-converted MS
Symptoms: `telescope_name` blank, numeric antenna names, all `intents == []`, empty HISTORY.
Action:
1. Repair `TELESCOPE_NAME` in OBSERVATION subtable
2. Repair antenna names from observatory table
3. Set intents manually or accept field-name heuristics

### Missing `gain_curves.gc`
Symptom: `ms_verify_priorcals` reports `gain_curves.gc` missing or empty.
Action: `gencal` could not access the telescope correction database — escalate; do not proceed.

### Initial bandpass caltables missing

| Check | Action |
|-------|--------|
| Wrong `bp_field`? | Cross-check against `ms_field_list` intents |
| Refant heavily flagged on BP scans? | Try rank-2 refant |
| `opacities.opac` missing (K-band)? | Priorcal failure propagates to initial bandpass silently |

### `ms_antenna_flag_fraction` slow or partial results
Action: Always use `n_workers=1` (serial). Only increase workers with explicit user approval.
For very large MSs (> 200 GB): use HTTP transport on the HPC node where data lives.

### `ms_shadowing_report` method unavailable
Symptom: `method.flag == "INFERRED"`.
Action: Only FLAG_CMD shadow entries reported — do not treat absence as no shadowing.

## Online flag anomalies

| `reason_breakdown` pattern | Action |
|---------------------------|--------|
| `ANTENNA_NOT_ON_SOURCE` dominant | Normal — no action |
| `OUT_OF_RANGE` dominant | Antenna hardware problem; note antennas affected |
| `SUBREFLECTOR_ERROR` dominant | Antenna hardware problem; note antennas affected |

## Escalation paths

| Situation | Escalation action |
|-----------|-----------------|
| All 3 refants fail to produce any caltable | Report: which refants tried, SNR/coverage at each attempt; recommend restart from preflag + RFI excision |
| SNR < 2.0 after all recovery attempts | Flag dataset as potentially non-usable; provide coverage % and SNR values |
| Flag delta > 30% after rflag | Identify whether single scan or SPW dominates; targeted excision before re-run |
| `opacities.opac` missing at K-band or above | Hard stop — do not calibrate |
| Coverage never reaches 50% on any caltable | Verify field selection with `ms_field_list`; check for MS corruption |

## MeerKAT-specific notes

- Very early data (pre-2019): scan intents may be absent — use field names
- CAM/CBF metadata: dump time may be in EXPOSURE not INTERVAL column — `ms_correlator_config` handles this

## uGMRT-specific notes

- Arm antenna names (C00, C01, E05): valid — do not confuse with numeric names
- Legacy GMRT data (pre-2017): non-standard format; use `listobs` as sanity check
- uGMRT GWB data (post-2017): generally CASA-compliant
