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

> **Band edges are NOT listed here.** They live in the telescope profiles at
> `src/ms_inspect/data/telescopes/<telescope>.yaml` (`bands`: `code`, `min_ghz`,
> `max_ghz`, `label`), which carry their provenance in comments. `ms_inspect`
> already resolves the band for you and returns it in the `band` field. If you
> need the edges themselves — to check SPW coverage, or to reason about a
> frequency near a receiver boundary — **Read the YAML for that telescope**. Do
> not reproduce edges from memory: the uGMRT ladder in particular has been
> renumbered, and stale copies of it are wrong.

Bands below are keyed by the `code` in the profile. The science-use column is
editorial context only — it has no effect on any calculation.

### VLA / JVLA

| Band | Primary science use |
|------|---------------------|
| P | Large-scale structure, SNRs, pulsars |
| L | HI 21 cm, OH masers, continuum |
| S | Continuum, masers |
| C | Continuum, ammonia, methanol |
| X | Continuum, SiO masers |
| Ku | Continuum |
| K | H₂O masers, ammonia |
| Ka | Continuum |
| Q | Continuum, high-z lines |

The profile also carries a low-frequency `4` band. VLA bands are intervals with
real gaps between receivers — a frequency in a gap resolves to no band, which is
correct, not a failure.

### MeerKAT

| Band | Primary science use |
|------|---------------------|
| UHF | HI at moderate z, pulsars |
| L | HI 21 cm (z=0), continuum |
| S | Continuum, masers |

UHF and L genuinely overlap (real receiver overlap) — a frequency in the overlap
resolves to both.

### uGMRT

Four wideband receivers since the 2019 upgrade, numbered **2–5**. There is no
commissioned Band 1, and the numbering does not start at the lowest-frequency
receiver you might expect — check the profile before labelling a band.

| Band | Primary science use |
|------|---------------------|
| 2 | Diffuse emission, pulsars |
| 3 | Large-scale structure |
| 4 | HI at z~0.4, continuum, OH |
| 5 | HI, continuum |

### ALMA

Bands 1–10; see `alma.yaml`. Bands 2 and 3 overlap, so a frequency in the
overlap resolves to both — the authoritative band for real data is parsed from
`ALMA_RB_NN` in `SPECTRAL_WINDOW.NAME`, with the frequency table as fallback.

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
