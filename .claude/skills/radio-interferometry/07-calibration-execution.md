# 07 — Calibration Execution

## Purpose

This document guides decisions during the CALIBRATION_PREFLAG, CALIBRATION_SOLVE,
and CALIBRATION_APPLY stages. You are filling CASA script templates from Phase 1-3
tool outputs and reading WILDCAT_METRICS to decide whether to loop or advance.

---

## Template placeholder reference

Fill every `{PLACEHOLDER}` from Phase 1-3 tool outputs. The tools that supply each
value are listed below.

| Placeholder | Source tool | What to extract |
|---|---|---|
| `{VIS}` | `ms_observation_info` | absolute path passed in ms_path |
| `{FLUX_FIELD}` | `ms_field_list` | field ID (integer string) of the flux/bandpass calibrator |
| `{BP_FIELD}` | `ms_field_list` | same as FLUX_FIELD unless a dedicated BP cal is present |
| `{DELAY_FIELD}` | `ms_field_list` | same as FLUX_FIELD for VLA; the field used for delay solving |
| `{PHASE_FIELD}` | `ms_field_list` | field ID(s) of phase calibrators, comma-separated |
| `{TARGET_FIELD}` | `ms_field_list` | field ID of the science target |
| `{CAL_FIELDS}` | `ms_field_list` | comma-separated field IDs of ALL calibrators |
| `{ALL_SPW}` | `ms_spectral_window_list` | spw selection string, e.g. `'0~15'` |
| `{CORRSTRING}` | `ms_correlator_config` | parallel-hand correlations: `'RR,LL'` (circular) or `'XX,YY'` (linear) |
| `{REFANT}` | `ms_refant` | recommended reference antenna name, e.g. `'ea09'` |
| `{FLUX_STANDARD}` | `ms_field_list` + band | calibrator model standard — see §Flux standards below |
| `{MINBLPERANT}` | `ms_antenna_list` | minimum baselines per antenna; use 4 for VLA, 3 for compact arrays |
| `{INT_TIME_S}` | `ms_scan_list` | integration time in seconds (for solint) |
| `{WORKFLOW_ID}` | provided in prompt | workflow_id integer |
| `{PHASE_SCAN_IDS}` | `ms_scan_list` | scan IDs on the phase calibrator, comma-separated |

---

## Flux standards by band

| Band | Frequency range | Standard | CASA name |
|---|---|---|---|
| P-band | 200–500 MHz | Scaife-Heald 2012 | `'Scaife-Heald 2012'` |
| L-band | 1–2 GHz | Perley-Butler 2017 | `'Perley-Butler 2017'` |
| S/C/X/Ku/K | 2–26 GHz | Perley-Butler 2017 | `'Perley-Butler 2017'` |
| Q-band+ | > 40 GHz | Perley-Butler 2017 | `'Perley-Butler 2017'` |

For VLA P-band with 3C147: use `standard='Scaife-Heald 2012'`. Do NOT use Perley-Butler
at P-band — it does not have a P-band model for 3C147.

---

## CALIBRATION_PREFLAG — decision thresholds

After reading `WILDCAT_METRICS` from the previous job (or baseline flagging if first pass):

| Condition | Decision |
|---|---|
| `overall_flag_frac < 0.15` AND `n_spw_heavy == 0` | Advance → CALIBRATION_SOLVE |
| `overall_flag_frac < 0.15` AND a few SPWs > 0.30 | Consider another pass if still < 3 iterations |
| `overall_flag_frac >= 0.15` AND `< 3 iterations` | Loop → CALIBRATION_PREFLAG |
| `overall_flag_frac >= 0.15` AND `3 iterations used` | Orchestrator escalates automatically; do not loop |
| `overall_flag_frac > 0.60` | Dataset is likely unusable; note in summary but do not block |

`n_spw_heavy` = number of SPWs with per-SPW flag fraction > 0.30. For P-band, SPWs 1, 2,
9, and 10 are expected to be heavily flagged — this is normal and does not indicate a
problem with the rflag algorithm.

---

## CALIBRATION_SOLVE — decision thresholds

After reading `WILDCAT_METRICS` from the solve job:

| Metric | Good | Warning | Loop back |
|---|---|---|---|
| `bp_flagged_frac` | < 0.10 | 0.10–0.20 | > 0.20 |
| `gain_flagged_frac` | < 0.08 | 0.08–0.15 | > 0.15 |
| `n_antennas_lost` | 0–1 | 2–3 | > 3 |

Choose CALIBRATION_APPLY when `bp_flagged_frac < 0.20` AND `gain_flagged_frac < 0.15`.
Choose CALIBRATION_PREFLAG (loop) when solutions are too heavily flagged — more RFI
excision in the DATA column is needed before solving is useful.

`n_antennas_lost` means antennas with no valid solutions in the gain table. Losing
1–2 antennas at P-band is common (dead or heavily RFI-affected) and does not warrant
a loop. Losing > 4 antennas suggests a systematic problem — flag it in the summary.

---

## CALIBRATION_APPLY — what to report

After the apply job completes, read `WILDCAT_METRICS` and report all three key values
in the `calibration_done` checkpoint question finding:

- `bp_flagged_frac` — from the solve job metrics
- `gain_flagged_frac` — from the solve job metrics
- `post_cal_flag_frac` — from the apply job metrics (flagged fraction of CORRECTED_DATA)

Set severity based on the worst metric:
- All metrics in "Good" range → `info`
- Any metric in "Warning" range → `warning`
- Any metric above loop threshold OR n_antennas_lost > 3 → `critical`

---

## solint guidance

| Goal | solint | Notes |
|---|---|---|
| Delay solve | `'inf'` | One solution over full scan; delay is quasi-static |
| Initial phase (for BP) | `'int'` | Per-integration; removes fast phase before BP solve |
| Bandpass | `'inf'` | One BP solution over the full calibrator scan |
| Gain (amplitude+phase) | `'int'` | Per-integration; captures time-variable ionosphere |

For VLA P-band, `solint='int'` on gains is important because the ionosphere varies
on timescales of seconds at 200–500 MHz.

---

## Calibration table naming convention

Tables are written to `/data/jobs/{WORKFLOW_ID}/`:

| Table | Name pattern |
|---|---|
| Delay | `delay.K` |
| Bandpass | `bandpass.B` |
| Gain (initial phase for BP) | `initial_phase.G0` |
| Gain (final amp+phase) | `gain.G` |
