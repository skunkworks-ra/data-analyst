# 07 — Calibration Apply: Metric Interpretation

## What you receive

`WILDCAT_METRICS` from two completed CASA jobs:
- `CALIBRATION_SOLVE`: `bp_flagged_frac`, `gain_flagged_frac`, `n_antennas_lost`,
  `t_delay`, `t_bp`, `t_gain` (caltable paths)
- `CALIBRATION_APPLY`: `post_cal_flag_frac`, `per_spw_flag_frac`, `n_spw_heavy`

The CASA scripts have already run. Interpret the numbers and decide whether to
proceed autonomously or surface a checkpoint to the operator.

## Decision: auto_proceed or surface?

| Condition | auto_proceed | severity |
|-----------|-------------|---------|
| All fractions < 0.20 AND `n_antennas_lost` ≤ 1 | `true` | `info` |
| Any fraction 0.20–0.40 OR `n_antennas_lost` 2–3 | `false` | `warning` |
| Any fraction > 0.40 OR `n_antennas_lost` > 3 | `false` | `critical` |

Apply the worst single condition across all three fractions.

**If `auto_proceed: true`:** set `checkpoint_questions: []`. The workflow will advance
to imaging without human intervention.

**If `auto_proceed: false`:** populate `checkpoint_questions` with one entry (see below).

## Summary content rules

Always state the actual numbers from WILDCAT_METRICS — do not editorialize:
- `bp_flagged_frac`, `gain_flagged_frac` from CALIBRATION_SOLVE
- `post_cal_flag_frac`, `n_spw_heavy` from CALIBRATION_APPLY
- `n_antennas_lost`

## Checkpoint question (only when auto_proceed: false)

```json
{
  "id": "calibration_done",
  "finding": "<actual values + inspect: t_bp=<path> t_gain=<path> t_delay=<path>>",
  "severity": "<warning|critical>",
  "question": "Calibration complete. Proceed to imaging or loop back for another calibration pass?",
  "options": ["proceed", "loop_back", "exit"],
  "recommendation": "<proceed|loop_back>",
  "timeout_seconds": 300,
  "timeout_default": "proceed"
}
```

- `finding` must include the caltable paths from WILDCAT_METRICS so the operator knows what to inspect
- `recommendation`: use `loop_back` only if `post_cal_flag_frac > 0.40` or `n_antennas_lost > 3`; otherwise `proceed`
- `timeout_seconds`: 300 for warning, 600 for critical
- `timeout_default` is always `"proceed"` — the pipeline does not stall on silence

## Routing the operator sees

| Answer | What happens |
|--------|-------------|
| `proceed` | → IMAGING_PIPELINE |
| `loop_back` | → CALIBRATION_PREFLAG (counter reset) |
| `exit` | → STOPPED |
| *(timeout)* | → `timeout_default` (proceed) |
