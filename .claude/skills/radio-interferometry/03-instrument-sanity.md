# 03 — Instrument Sanity: Interpreting Phase 2 Output

## Array configuration and imaging implications

### VLA configuration classification

From `ms_baseline_lengths.max_baseline_m`:

| Config | B_max | L-band resolution | L-band LAS | Typical use |
|--------|-------|-------------------|------------|-------------|
| D | ≤ 1,030 m | ~45″ | ~16′ | Extended emission, low surface brightness |
| C | ≤ 3,400 m | ~14″ | ~5′ | Intermediate, small groups |
| B | ≤ 11,100 m | ~4.3″ | ~1.6′ | Compact sources, high resolution |
| A | ≤ 36,400 m | ~1.3″ | ~30″ | Highest resolution, compact sources only |

For B-array and above: sources larger than LAS will have flux resolved out.
Always quote both resolution and LAS in your Phase 2 summary.

### MeerKAT baseline structure

MeerKAT has a hybrid core-remote layout:
- **Core (< 1 km):** ~40 antennas clustered. High surface-brightness sensitivity.
- **Inner array (1–4 km):** ~20 antennas.
- **Outer array (4–8 km):** few antennas.
- **Maximum baseline:** ~8 km (L-band resolution ~4″)

MeerKAT's strength is short-baseline sensitivity. The LAS at L-band is ~1°.
For extended HI science, the minimum baseline (typically 29–30 m) is often
the primary figure of merit.

### uGMRT baseline structure

uGMRT consists of 30 antennas in 6 arms:
- **Central square (600 m × 600 m):** 14 antennas. Provides short spacings.
- **6 arms, 14 km total:** 16 arm antennas. Maximum baseline ~14 km at 1.4 GHz.
- At Band 3 (650 MHz): maximum baseline ≈ 30 kλ → resolution ~7″

The central square antennas are critical for recovering extended emission.
If any central square antennas appear heavily flagged, note the impact on LAS.

---

## Elevation effects on data quality

Elevation affects data through three independent mechanisms:

### 1. Atmospheric emission and absorption
Below ~20°, the atmosphere contributes significantly to system temperature.
T_sys rises steeply: at 10° elevation, T_sys can double vs zenith.
This affects sensitivity, not phase — low-elevation data is noisy, not corrupt.

### 2. Ionospheric phase errors (< 2 GHz)
At low frequencies (P-band, L-band, uGMRT bands), the ionosphere introduces
direction- and time-dependent phase errors. These are worse at:
- Low elevation (longer path through ionosphere)
- Night-time at low latitudes (scintillation)
- Solar maximum periods
Self-calibration or ionospheric modeling (DDECal, EveryBeam) is needed
for P-band and uGMRT observations below ~500 MHz.

### 3. Primary beam effects
The primary beam gain drops with elevation through beam squinting for
alt-az mounts. This is generally a second-order effect but relevant for
wide-field polarimetry.

**Recommendation table:**

| Elevation | Data status | Recommended action |
|-----------|-------------|-------------------|
| < 10° | Almost certainly unusable | Flag all data in affected scans |
| 10°–20° | Poor quality | Flag, or use only for total intensity with caution |
| 20°–30° | Acceptable | Note in data quality assessment |
| > 30° | Normal | No special action |

---

## Parallactic angle and polarimetry

### Why PA coverage matters (ALT-AZ arrays only)

For alt-az mounted telescopes, the feed rotates with respect to the sky
as the source moves. This rotation allows:
1. **D-term (leakage) calibration:** different PA samples allow separation
   of the instrumental polarisation (D-terms) from the source polarisation.
2. **Cross-hand phase calibration:** rotation of the parallactic angle
   modulates the cross-hand visibilities.

Without PA coverage, D-terms are degenerate with the source polarisation
and cannot be solved for independently.

### Coverage requirements (sky-frame PA range)

| PA range | Status |
|----------|--------|
| < 30° | Insufficient. D-term solutions will be unreliable. |
| 30°–60° | Marginal. May be acceptable with a known-polarisation calibrator. |
| ≥ 60° | Adequate for standard CASA `polcal` D-term solutions. |
| ≥ 120° | Excellent. Robust D-term solutions. |

### Equatorial mounts (WSRT, some older arrays)

The feed does NOT rotate with the sky. PA is constant throughout the
observation. Use a different strategy: observe at multiple HA positions
on different days, or use a model of the known D-terms.

### Practical note on VALIDATION PENDING status

Until `ms_parallactic_angle_vs_time` is cross-validated against
`casatools.measures`, treat `pa_feed_deg` as a directional estimate only.
Quote `pa_sky_deg` range for coverage assessments. Flag in your output
that the feed-frame values are pending validation.

---

## Flag fraction interpretation

### Preflight before reading FLAG data

Always call `ms_flag_preflight` before `ms_antenna_flag_fraction`. The probe completes in
seconds and returns:

| Field | How to use |
|-------|-----------|
| `estimated_runtime_min` | If > 10 min, warn the user before proceeding |
| `recommended_workers` | Pass as `n_workers` to `ms_antenna_flag_fraction` |
| `will_parallelize` | False means single-process is optimal (small MS or few rows) |
| `data_volume_gb` | Include in the runtime warning message to the user |

Example warning text when `estimated_runtime_min > 10`:
> "Reading the FLAG column on this MS will take approximately {estimated_runtime_min} min
> ({data_volume_gb} GB). Proceeding."

Do not call `ms_antenna_flag_fraction` and `ms_flag_summary` in parallel — both open
the MS and `flagdata(mode='summary')` acquires a write-lock even in read-only mode.
Run them sequentially.

### System-level perspective

Overall flag fraction of a full observation:
- < 5%: clean dataset, benign RFI environment
- 5–20%: typical for L-band in populated areas
- 20–40%: heavy RFI, may affect calibration quality
- > 50%: severely compromised — assess whether calibration is feasible

### Per-antenna diagnosis

Look for antennas with flag fraction substantially above the array median.

**Differential diagnosis of high flag fraction:**

| Pattern | Likely cause |
|---------|--------------|
| Single antenna, flag fraction > 80% | Receiver failure, parked antenna, known offline |
| Single antenna, flag fraction 30–80% | Intermittent electronics, bad correlator connection |
| Multiple adjacent antennas high | Arm-level failure (uGMRT) or baseline-board issue (VLA) |
| All antennas high in a single scan | RFI event, correlator restart, pointing error |
| High `n_flag_commands_online` but low data flag fraction | Online flags not applied — run `flagcmd(action='apply')` |

### Impact on calibration

Antennas with flag fraction > 80% should be excluded from calibration
solution intervals (`solint`) to avoid biasing the solutions.

CASA `gaincal` will attempt to solve for all antennas present in the data.
An antenna with 85% flagged data contributes only 15% usable data to its
solution — the solution may be formally valid but poorly constrained.

Recommend: set `minblperant=4` in `gaincal` (default is 4 but confirm),
and visually inspect solutions for heavily flagged antennas.
