# 02 — Phase 2: Instrument Sanity Thresholds

Run after Phase 1 is complete. Requires valid telescope name and antenna table.

## 2.1 `ms_antenna_list`

| Check | Pass | next_stage |
|-------|------|-----------|
| Antenna names non-numeric | Pass | Numeric names → `stop — repair antenna table` |
| No orphaned antenna IDs | Pass | Orphaned IDs → `stop — repair antenna table` |
| Any position `(0,0,0)` | None | Flag those antennas; baselines involving them are geometrically wrong |

Expected antenna counts:
- VLA: 27 (sometimes fewer for maintenance)
- MeerKAT: 64 (MeerKAT+: 80)
- uGMRT: 30

## 2.2 `ms_baseline_lengths`

VLA configuration classification:

| Config | B_max | L-band resolution | L-band LAS |
|--------|-------|-------------------|------------|
| D | ≤ 1,030 m | ~45″ | ~16′ |
| C | ≤ 3,400 m | ~14″ | ~5′ |
| B | ≤ 11,100 m | ~4.3″ | ~1.6′ |
| A | ≤ 36,400 m | ~1.3″ | ~30″ |

Always report both resolution and LAS. Sources larger than LAS will have flux resolved out.

## 2.3 `ms_elevation_vs_time`

| Elevation | Action |
|-----------|--------|
| < 10° | Flag all data in affected scans |
| 10°–20° | Flag or use only total intensity with caution; note |
| 20°–30° | Note in summary |
| > 30° | Normal |

Low-elevation scans must be flagged before calibration, not after.

## 2.4 `ms_parallactic_angle_vs_time`

NOTE: `pa_feed_deg` carries `validation_status: "PENDING"`. Use `pa_sky_deg` for coverage assessment only.

| PA sky range | Assessment | Action |
|-------------|-----------|--------|
| < 30° | Insufficient | D-term solutions unreliable; flag; polcal NOT feasible |
| 30°–60° | Marginal | May be acceptable with known-polarisation calibrator; use `Df+QU` |
| ≥ 60° | Adequate | Standard polcal D-term solutions possible |

Report per-field PA range, not array-average.

## 2.5 `ms_shadowing_report`

| Finding | Action |
|---------|--------|
| `shadowing_detected: true` | Inspect `shadowed_antennas` — antennas with `shadow_flag_fraction > 0` |
| Shadowing on flux/bandpass cal scans | Serious — excise shadowed antennas from those scans explicitly |
| Shadowing at low elevation (D-config expected) | Note antennas and shadow fraction |
| `method.flag == "INFERRED"` | `flagdata(mode='shadow')` failed — only FLAG_CMD entries reported; do not treat absence as no shadowing |

## 2.6 `ms_antenna_flag_fraction`

Always call `ms_flag_preflight` first, then `ms_antenna_flag_fraction` with `n_workers=1`.
Do NOT run `ms_antenna_flag_fraction` and `ms_flag_summary` in parallel.

| Flag fraction | Assessment |
|---------------|-----------|
| < 5% | Clean |
| 5–20% | Typical for L-band in populated areas |
| 20–40% | Heavy RFI; may affect calibration |
| > 50% | Severely compromised; assess calibration feasibility |

Per-antenna patterns:
| Pattern | Likely cause |
|---------|-------------|
| Single antenna > 80% | Receiver failure / offline |
| Single antenna 30–80% | Intermittent electronics |
| Multiple adjacent antennas high | Arm-level failure (uGMRT) or baseline board (VLA) |
| All antennas high in 1 scan | RFI event or correlator restart |
| High `n_flag_commands_online` but low data flag fraction | Online flags not applied — run `flagcmd(action='apply')` |

Antennas > 80% flagged: exclude from refant candidates; note in summary.

## Phase 2 stop conditions

| Condition | next_stage |
|-----------|-----------|
| Numeric antenna names | `stop — repair` |
| All antenna positions at origin | `stop — no UV coordinates computable` |
| `ms_observation_info` returns no telescope name (from Phase 1) | `stop — repair first` |
