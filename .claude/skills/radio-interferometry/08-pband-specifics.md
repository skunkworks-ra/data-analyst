# 08 — P-band Specifics (200–500 MHz, VLA)

## Scope

This document applies to VLA P-band observations (EVLA low-band, 200–500 MHz).
All content is specific to this band and supplements general calibration guidance
in 07-calibration-execution.md.

Reference observation: 3C129 P-band continuum, B-configuration, CASA 6.7.2 guide.

---

## Flux density calibration at P-band

**Use `standard='Scaife-Heald 2012'`** for all VLA P-band flux calibration.

Perley-Butler 2017 does not include a P-band model for the standard VLA flux
calibrators. Using it at P-band will silently apply an incorrect flux scale.

Standard P-band flux calibrators:
- **3C147** — recommended; well-modelled in Scaife-Heald 2012
- **3C286** — usable; less preferred at P-band due to extended structure
- **3C48** — usable

CASA call:
```python
setjy(vis=vis, field=bpcal_field, standard='Scaife-Heald 2012', usescratch=True)
```

---

## Parallactic angle correction

**Always set `parang=True`** in `gaincal`, `bandpass`, and `applycal` at P-band.

The VLA P-band feeds are dipoles fixed to the dish. As the dish rotates to track
a source, the feed rotates with it (parallactic angle rotation). Omitting `parang=True`
leaves an uncorrected time-variable phase slope that will corrupt gain solutions.

---

## Ionospheric effects

At P-band, the ionosphere is the dominant calibration challenge:

- **Dispersive delay**: phase ∝ TEC / ν. At 300 MHz, a 1 TECU change causes
  ~5 ns of delay — significantly larger than at L-band.
- **Faraday rotation**: polarisation angle rotates ∝ RM / ν². At P-band this
  can be many turns even for modest rotation measures.
- **Time variability**: the ionosphere changes on timescales of seconds to minutes.
  Use `solint='int'` for gain calibration to track this variation.

In the delay solve, expect antenna delays of ±20–50 ns relative to the reference
antenna. Values much larger than 50 ns suggest a problem with that antenna or
polarisation. Delays near zero for all antennas except the reference is correct.

---

## Known RFI spectral windows

The default VLA P-band setup (16 × 16 MHz SPWs, 224–480 MHz) has predictable
persistent RFI in specific SPWs:

| SPWs | Frequency range | Source |
|---|---|---|
| 1, 2 | 240–272 MHz | Continuous broadband RFI |
| 9, 10 | 368–400 MHz | Continuous broadband RFI |

These SPWs are expected to be heavily flagged (> 50%) after rflag. This is normal
and does not indicate a calibration problem. Do not use them for bandpass or gain
solving if flagging is > 60%.

Additional SPWs may be affected depending on local RFI environment.
SPWs 16, 17, 18 (if present) are auxiliary wide/narrow windows — treat separately.

---

## Bandpass ripples

Sinusoidal amplitude and phase ripples are present in all VLA P-band data.
They arise from signal reflections in cables and within the dish structure.

Characteristics:
- Visible in bandpass amplitude plots as ± 10–50% modulation around the mean
- Present in both polarisations but may differ in amplitude and phase between them
- Quasi-stationary over the duration of a single observation
- Removed correctly by bandpass calibration

**These ripples are not a sign of data corruption.** They are expected and the
bandpass solve handles them. If ripples are unusually large (> 50% of mean amplitude)
for a specific antenna, that antenna may have a cable or connector problem.

---

## Hanning smoothing recommendation

Narrow RFI spikes cause Gibbs ringing in adjacent channels. Hanning smoothing
suppresses this before calibration. For the 16-SPW P-band setup, apply
Hanning smoothing to SPWs 0–15 before beginning the calibration sequence:

```python
hanningsmooth(vis=rawvis, outputvis=msdata, datacolumn='data', spw='0~15')
```

Note: after Hanning smoothing, spectral resolution is halved (effective channel
width doubles). This is acceptable for continuum P-band work.

---

## Double data descriptor issue (older data)

Some older P-band datasets have two DATA_DESCRIPTION entries per spectral window —
one pointing to circular (RL) polarisation and one to linear (XY). Symptoms:
- Tool outputs show duplicate SPW entries
- Calibration tasks fail with "inconsistent polarisation basis" errors

Recovery: use the `fixlowband()` contributed task. Check for this if Phase 1 tool
outputs show unexpected duplicate SPW IDs.

---

## Recommended solint values for P-band

| Solve step | Recommended solint | Reason |
|---|---|---|
| Initial phase (before BP) | `'int'` | Remove fast ionospheric phase per integration |
| Delay | `'inf'` | Quasi-static; one value per calibrator scan is sufficient |
| Bandpass | `'inf'` | Time-averaged BP over full calibrator scan |
| Gain (amplitude+phase) | `'int'` | Ionosphere varies on integration timescales |

Use `minsnr=2.0` for bandpass (P-band SNR is lower than at higher frequencies)
and `minsnr=3.0` for gain calibration.

---

## Reference antenna selection at P-band

Prefer a mid-array antenna with:
- No known RFI sensitivity issues
- Stable gain solutions in the initial phase solve
- Low flag fraction from `ms_antenna_flag_fraction`

For the 3C129 dataset, `ea09` (W08) is the standard reference antenna used in
the CASA guide. If `ms_refant` recommends a different antenna, use that recommendation
unless it has a flag fraction > 20%.
