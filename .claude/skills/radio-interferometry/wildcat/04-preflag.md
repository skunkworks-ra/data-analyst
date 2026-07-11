# 04 — Pre-Calibration Flagging Sequence

## Sequence (order is required)

```
ms_verify_import → ms_online_flag_stats → ms_apply_preflag → ms_flag_summary
→ ms_generate_priorcals → ms_verify_priorcals → ms_setjy → ms_refant
→ ms_initial_bandpass → ms_verify_caltables → ms_plot_caltable_library
→ ms_residual_stats → ms_apply_initial_rflag → ms_flag_summary
```

## Import verification

| `ms_verify_import` field | Pass | next_stage |
|--------------------------|------|-----------|
| `ms_valid` | True | Re-run import |
| `flag_file_exists` | True | Re-run with `savecmds=True` |
| `flag_file_n_commands` | > 0 | Unusual for VLA; note |
| `ready_for_preflag` | True | All three above must pass |

## Online flag assessment

| `ms_online_flag_stats` field | Watch for |
|------------------------------|-----------|
| `n_commands` | > 500: high — note |
| `n_antennas_flagged` | > 5: elevated — inspect reason breakdown |
| `reason_breakdown` | `ANTENNA_NOT_ON_SOURCE` normal; `OUT_OF_RANGE` / `SUBREFLECTOR_ERROR` = antenna problem |

## Flag fraction after preflag

| Overall flag fraction | Assessment | next_stage |
|----------------------|-----------|-----------|
| < 10% | Clean | proceed |
| 10–25% | Moderate | note; proceed |
| 25–40% | Elevated | identify driver (shadow? online? tfcrop?) |
| > 40% | High | identify driver before proceeding |

If single antenna > 80% of flagging: exclude from refant candidates.

## Prior caltables

| Table | Required | If missing |
|-------|----------|-----------|
| `gain_curves.gc` | Hard required | `next_stage=stop — cannot calibrate` |
| `opacities.opac` | K-band and above | Hard stop at K+; note and continue below K |
| `requantizer.rq` | VLA post-2011 | Safe to skip for pre-WIDAR |
| `antpos.ap` | No | Skip if empty; note if has rows |

## setjy warnings → actions

| Warning | Action |
|---------|--------|
| `CALIBRATOR_RESOLVED_WARNING` | Use component model from warning text; do NOT use point source model |
| 3C84 present | Pass `uvrange='>5klambda'` to initial bandpass |
| 3C138 at K/Ka/Q | Source may be in flare (early 2025); note flux scale uncertainty |

## Refant selection

Use top-ranked antenna from `ms_refant` unless:
- Top antenna flag fraction > 30% → use rank-2
- Fewer than 3 antennas scored → escalate (array too sparse)

Record top 3 refants. If initial bandpass fails, try rank-2 before changing other parameters.

## Residual stats → rflag thresholds

`p95_amp / median_amp` > 8 per SPW: run `ms_rfi_channel_stats` first.
Calibrator elevation < 30°: use `timedevscale=7, freqdevscale=7` instead of defaults.

## rflag flag delta

| Flag fraction increase | Assessment | next_stage |
|------------------------|-----------|-----------|
| < 5% | Clean | `proceed_to_solve` |
| 5–15% | Moderate (normal L-band) | `proceed_to_solve` |
| 15–30% | Heavy | Proceed with caution; note driving fields/SPWs |
| > 30% | Severe | Identify driver (bad scan? persistent SPW?) before proceeding |

## Decision gate — proceed to calibration solve?

| Condition | next_stage |
|-----------|-----------|
| Overall flag fraction < 30% AND no caltable failures | `proceed_to_solve` |
| Overall flag fraction 30–50% | `proceed_to_solve_with_note` |
| Overall flag fraction > 50% | `escalate` |
| Per-calibrator flag fraction ≥ 50% on gain calibrator | Relax rflag thresholds; re-run rflag step |
| `gain_curves.gc` missing | `stop` |
| `opacities.opac` missing at K-band+ | `stop` |
| Initial bandpass caltables invalid | `stop — diagnose` |
| Specific antenna > 80% flagged | Note; exclude from refant; `proceed` |

After rflag: re-check chosen refant's flag fraction. If > 50%, switch to next refant and re-run initial bandpass.
