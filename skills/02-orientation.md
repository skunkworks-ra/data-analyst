# 02 — Orientation: Interpreting Phase 1 Output

## Completeness flag interpretation

When you receive tool output, evaluate the `completeness_summary` first.
Then inspect individual field flags.

| Flag | What it means for your analysis |
|------|----------------------------------|
| `COMPLETE` | Use the value directly |
| `INFERRED` | Use with stated confidence; note inference basis in your report |
| `PARTIAL` | Identify which fraction is missing; assess whether analysis is still valid |
| `SUSPECT` | Do not use. Describe the suspicion and block downstream computation |
| `UNAVAILABLE` | Cannot compute. State this explicitly; do not substitute a guess |

Never silently upgrade a flag. If a field is `SUSPECT`, every downstream
quantity that depends on it is also at least `SUSPECT`.

---

## Band identification reference

### VLA / EVLA / JVLA

| Band | Frequency | Primary science use |
|------|-----------|---------------------|
| P | 230–470 MHz | Large-scale structure, SNRs, pulsars |
| L | 1–2 GHz | HI 21 cm, OH masers, continuum |
| S | 2–4 GHz | Continuum, masers |
| C | 4–8 GHz | Continuum, ammonia, methanol |
| X | 8–12 GHz | Continuum, SiO masers |
| Ku | 12–18 GHz | Continuum |
| K | 18–26.5 GHz | H₂O masers, ammonia |
| Ka | 26.5–40 GHz | Continuum |
| Q | 40–50 GHz | Continuum, high-z lines |

### MeerKAT

| Band | Frequency | Primary science use |
|------|-----------|---------------------|
| UHF | 544–1088 MHz | HI at moderate z, pulsars |
| L | 856–1712 MHz | HI 21 cm (z=0), continuum |
| S | 1.75–3.5 GHz | Continuum, masers |

### uGMRT

| Band | Frequency | Primary science use |
|------|-----------|---------------------|
| Band 1 | 120–250 MHz | Diffuse emission, pulsars |
| Band 2 | 250–500 MHz | Large-scale structure |
| Band 3 | 550–750 MHz | HI at z~0.4, continuum |
| Band 4 | 700–950 MHz | Continuum, OH |
| Band 5 | 1050–1450 MHz | HI, continuum |

---

## Scan intent vocabulary

CASA uses a defined vocabulary for scan intents. The most common:

| Intent string | Meaning |
|---------------|---------|
| `CALIBRATE_FLUX#ON_SOURCE` | Flux density scale calibration |
| `CALIBRATE_BANDPASS#ON_SOURCE` | Bandpass shape calibration |
| `CALIBRATE_PHASE#ON_SOURCE` | Complex gain (phase + amplitude) calibration |
| `CALIBRATE_DELAY#ON_SOURCE` | Antenna-based delay calibration |
| `CALIBRATE_POLARIZATION#ON_SOURCE` | D-term (leakage) calibration |
| `CALIBRATE_POL_ANGLE#ON_SOURCE` | Absolute polarisation angle calibration |
| `OBSERVE_TARGET#ON_SOURCE` | Science target |
| `SYSTEM_CONFIGURATION` | Slew, setup, dummy scan |
| `UNSPECIFIED` | No intent set (treat as unknown) |

When `ms_scan_intent_summary` returns groups with `FIELD:` prefix, intents
were absent and breakdown is by field name. Treat as `INFERRED` quality.

---

## Mosaic observations

If `ms_field_list` returns multiple fields with the same `source_id`, this
is a mosaic — multiple pointings of the same extended source.

Key implications:
- **Imaging:** requires mosaic deconvolution (CASA `tclean` with `gridder='mosaic'`)
  not standard single-field imaging.
- **Calibration:** all mosaic pointings share the same calibration solutions —
  do not calibrate each pointing independently.
- **Primary beam:** the mosaic footprint must be planned around the primary beam
  FWHM: θ_pb ≈ 1.02 λ / D where D is the dish diameter.
  VLA (25 m): L-band θ_pb ≈ 27 arcmin.
  MeerKAT (13.5 m): L-band θ_pb ≈ 58 arcmin.
  uGMRT (45 m): L-band θ_pb ≈ 25 arcmin.

---

## Typical observation failure signatures in Phase 1

| Symptom | Likely cause | Action |
|---------|--------------|--------|
| `telescope_name` = blank | UVFITS conversion without metadata | Repair OBSERVATION subtable before any analysis |
| Only 1 field | Calibrator-only or test observation | Not a science dataset — confirm intent |
| Scan list shows only 1 scan | Single snapshot or timing issue | Check if MS is a sub-selection of a larger track |
| `total_duration_s < 600` | Very short track | Note that UV coverage and sensitivity will be poor |
| All fields have `intents == []` and no catalogue match | UVFITS import from old system | Use field names as sole guide; flag all intents as UNAVAILABLE |
| `n_spw == 1` and `n_channels == 1` | Heavily time-and-frequency averaged | No spectral analysis or per-channel bandpass possible |
