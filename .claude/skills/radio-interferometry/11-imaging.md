# 11 — First-Pass Imaging

## Purpose

Guide the first-pass continuum or spectral-cube imaging after
`CORRECTED_DATA` has been written by applycal. This is Phase 3.

Self-calibration is Phase 4 and is out of scope here.

---

## Placeholder reference

All placeholders are populated from Phase 1–2 tool outputs before calling `ms_tclean`.

| Placeholder | Source tool | What to extract |
|---|---|---|
| `{VIS}` | provided | full MS path (not calibrators.ms) |
| `{TARGET_FIELD}` | user confirmed (see Step 0) | CASA field selection string, e.g. `'2~8'` or `'3C391_C1'` |
| `{IS_MOSAIC}` | user confirmed (see Step 0) | True if imaging multiple pointings together |
| `{STOKES}` | user confirmed | default `'I'`; `'IQUV'` etc. accepted |
| `{POINTING_CENTERS}` | `ms_field_list` | RA/Dec of each target pointing (mosaic only) |
| `{MAX_BASELINE_M}` | `ms_baseline_lengths` | `max_baseline_m` |
| `{CENTER_FREQ_HZ}` | `ms_observation_info` | centre frequency in Hz |
| `{BANDWIDTH_HZ}` | `ms_spectral_window_list` | total bandwidth in Hz |
| `{DISH_DIAMETER_M}` | `ms_antenna_list` | dish diameter (all antennas same for connected arrays) |
| `{TELESCOPE}` | `ms_observation_info` | `telescope_name` |
| `{N_ANT}` | `ms_antenna_list` | number of unflagged antennas |
| `{T_ON_SOURCE_S}` | `ms_scan_list` | total integration time on science target in seconds |
| `{WORKDIR}` | provided | directory for image output |

---

## Step 0 — Confirm field selection and Stokes

Before deriving any parameter, confirm with the user what to image.
Do not assume — field selection has direct consequences for gridder choice,
image size, and whether a mosaic is needed.

Ask explicitly if not stated:

1. **Which fields?** Show the target fields from `ms_field_list` and ask:
   - Image all target fields together as a mosaic?
   - Image a single field only — which one?
   - Image a specific subset — which fields?

2. **Stokes?** Default is `'I'`. Ask only if the observation has full-polarisation
   data (RR/RL/LR/LL or XX/XY/YX/YY) and the user has not stated a preference.
   Valid values: `'I'`, `'IV'`, `'IQUV'`, `'RR'`, `'LL'`, `'XX'`, `'YY'`.
   If calibration was Stokes I only (no polcal), do not offer `'IQUV'`.

Record confirmed values as `{TARGET_FIELD}`, `{IS_MOSAIC}`, and `{STOKES}`.

---

## Step 0.5 — CORRECTED coherence gate (mandatory, before any tclean)

Imaging cannot rescue incoherent CORRECTED data — it yields noise whose brightest
residual spike mimics a source. Gate on `ms_corrected_stats` first, using the same
in-band `chan_start/chan_end` as the gain/delay solves:

```
ms_corrected_stats(field='{PHASE_FIELD},{TARGET_FIELD}', chan_start=..., chan_end=...)
```

| Field | Check | Fail → action |
|---|---|---|
| phase cal | `phase_rms_deg` < 30° | calibration didn't take — do NOT image; re-solve (skill 07) |
| phase cal | `amp_robust_std` < ~20% of `amp_median` | bad gains — fix first |
| target | `amp_median` > 0, `amp_robust_std` not ≫ `amp_median` | decorrelated — imaging will be noise |

A failed gate means the Step 9 verdict will read `marginal`/FAIL: that is
calibration failure, not a faint source. Do not report the peak as a detection.

---

## Step 1 — Choose imaging mode

Determine `specmode` before deriving any other parameter.

| Condition | `specmode` |
|---|---|
| Single SPW, continuum science | `'mfs'` |
| Multiple SPWs aggregated, continuum science | `'mfs'` |
| Spectral line science or per-channel imaging requested | `'cube'` |

Default to `'mfs'` unless the user explicitly asks for a cube.

---

## Step 2 — Choose deconvolver

| Condition | `deconvolver` | Notes |
|---|---|---|
| `specmode='cube'` | `'hogbom'` | Always |
| `{BANDWIDTH_HZ} / {CENTER_FREQ_HZ} > 0.2` | `'mtmfs'`, `nterms=2` | Wideband; also produces a spectral index map |
| Otherwise | `'hogbom'` | Default for first-pass |

Multiscale CLEAN is deferred. Run hogbom first; if the residual image shows
coherent extended structure after the first pass, re-run with `deconvolver='multiscale'`
and scales derived from the synthesized beam.

---

## Step 3 — Derive cell size

```
lambda_m         = c / {CENTER_FREQ_HZ}          # c = 2.998e8 m/s
max_bl_lambda    = {MAX_BASELINE_M} / lambda_m    # baseline in wavelengths
cell_rad         = 1.0 / (max_bl_lambda * 3)      # factor 3: avoid over-sampling
cell_arcsec      = cell_rad * (180 * 3600 / pi)
```

Round `cell_arcsec` to 1 significant figure (e.g. 2.47" → 2.5"). State the
value and record it as `{CELL}`.

---

## Step 4 — Derive image size

Primary beam FWHM:
```
pb_fwhm_arcsec = (1.02 * lambda_m / {DISH_DIAMETER_M}) * (180 * 3600 / pi)
```

**Always image out to the first primary-beam sidelobe.** Stopping at the FWHM
leaves bright sources in the first sidelobe (radius ≈ 1.6 × FWHM, where the PB
gain is still a few percent) undeconvolved — their sidelobes alias back across
the field and limit the dynamic range. The first null sits at ≈ 1.2 × FWHM
radius and the first sidelobe peak at ≈ 1.6 × FWHM radius, so the image
**diameter** must be ≈ 3 × FWHM.

**Single pointing:**
```
imsize_pixels = ceil(pb_fwhm_arcsec * 3 / cell_arcsec)   # diameter = 3 × FWHM → covers first sidelobe (~1.6 FWHM radius)
```

**Mosaic:** compute the bounding box of all pointing centres in `{POINTING_CENTERS}`,
convert angular extent to pixels, then pad by `1.5 * pb_fwhm_arcsec` on each side
so every pointing's first sidelobe is imaged:
```
imsize_pixels = ceil((mosaic_extent_arcsec + 3 * pb_fwhm_arcsec) / cell_arcsec)
```

(If compute or memory is the binding constraint, dropping to a `2 × FWHM`
diameter — first null only — is the fallback, but record it explicitly as a
deviation: bright first-sidelobe sources will not be cleaned.)

Round `imsize_pixels` **up** to the nearest composite number of the form
2ᵃ × 3ᵇ × 5ᶜ. Common values: 240, 256, 320, 360, 384, 480, 512, 600, 640,
720, 800, 900, 1024. Do not use a prime number — tclean will run extremely slowly.

Record as `{IMSIZE}`.

---

## Step 5 — Check W-term significance

```
fresnel = {DISH_DIAMETER_M}**2 / ({MAX_BASELINE_M} * lambda_m)
```

| `fresnel` | Action |
|---|---|
| ≥ 0.9 | W-terms negligible |
| < 0.9 | W-projection required; set `wprojplanes` per table below |

`wprojplanes` scaling:

| `fresnel` | `wprojplanes` |
|---|---|
| 0.7–0.9 | 16 |
| 0.4–0.7 | 32 |
| 0.1–0.4 | 64 |
| < 0.1 | 128 |

---

## Step 6 — Choose gridder

| Condition | `gridder` | `wprojplanes` |
|---|---|---|
| Mosaic AND telescope in `{VLA, ALMA}` AND W-terms required | `'awp2'` | from Step 5 |
| Mosaic AND telescope in `{VLA, ALMA}` AND W-terms not required | `'awp2'` | not set |
| Mosaic AND telescope NOT in `{VLA, ALMA}` | see note below | from Step 5 if required |
| Single pointing AND W-terms required | `'wproject'` | from Step 5 |
| Single pointing AND W-terms not required | `'standard'` | not set |

Match against the **canonical** telescope name returned by `ms_inspect`
(`telescope` field) — `VLA`, not the raw `MS::OBSERVATION::TELESCOPE_NAME`
string, which may be `EVLA` or `JVLA`. Name normalization happens in the
telescope profile (`src/ms_inspect/data/telescopes/*.yaml`, `aliases`).

**Unsupported mosaic telescope:** use `'wproject'` if W-terms required, else
`'standard'`. Warn the user: primary beam mosaicing is not applied automatically
for this telescope — the image will not be primary-beam corrected across pointings.

---

## Step 7 — Estimate cleaning threshold

Radiometer equation RMS:
```
n_baselines  = {N_ANT} * ({N_ANT} - 1) / 2
sigma_jy     = SEFD / sqrt(2 * {BANDWIDTH_HZ} * {T_ON_SOURCE_S} * n_baselines)
threshold    = 3 * sigma_jy
```

SEFD reference values (Jy). Band codes are **per telescope** — uGMRT numbers its
own bands and they do not map onto VLA band letters:

| Telescope | Band | SEFD (Jy) |
|---|---|---|
| VLA | P | 2600 |
| VLA | L | 420 |
| VLA | S | 370 |
| VLA | C | 310 |
| VLA | X | 280 |
| MeerKAT | L | 400 |
| MeerKAT | S | 380 |
| uGMRT | 2 (125–250 MHz) | 1500 |
| uGMRT | 3 (250–500 MHz) | 350 |
| uGMRT | 4 (550–850 MHz) | 285 |
| uGMRT | 5 (1000–1460 MHz) | 300 |

> **Authoritative source:** `src/ms_inspect/data/telescopes/<telescope>.yaml`,
> key `sefd_jy`. This table is a cross-telescope comparison view only. Read the
> profile for the value you actually put into the calculation above, and use its
> band code — bands absent from `sefd_jy` yield SEFD UNAVAILABLE by design, which
> you must report rather than substitute a guess.

Express `threshold` in mJy, e.g. `'0.5mJy'`. This is a starting estimate —
tclean will stop at this level or at `niter`, whichever comes first.

Set `niter=50000` as the default upper bound. Adjust down for quick diagnostic
runs (`niter=1000`).

---

## Step 8 — Call ms_tclean

```
ms_tclean(
    ms_path      = {VIS},
    imagename    = {WORKDIR}/{imagename},
    field        = {TARGET_FIELD},
    stokes       = {STOKES},
    specmode     = {specmode},
    deconvolver  = {deconvolver},
    nterms       = 2,              # only when deconvolver='mtmfs'
    gridder      = {gridder},
    wprojplanes  = {wprojplanes},  # only when gridder='wproject'
    cell         = {CELL},
    imsize       = [{IMSIZE}, {IMSIZE}],
    weighting    = 'briggs',
    robust       = 0.5,
    niter        = 50000,
    threshold    = {threshold},
    pbcor        = True,
    savemodel    = 'modelcolumn',
    workdir      = {WORKDIR},
    execute      = False,
)
```

`savemodel='modelcolumn'`: writes MODEL_DATA into the MS, required for self-cal (Phase 4).

Generate the script first (`execute=False`), review it, then run it as a
background job. Wait for completion however long it takes — tclean on a
real mosaic can run for hours.

---

## Step 9 — Quality assessment

Call `ms_image_stats` on the pbcor image:

```
ms_image_stats(
    image_path  = {WORKDIR}/{imagename}.image.pbcor,
    beam_image  = {WORKDIR}/{imagename}.psf,
)
```

Quality gates:

| Metric | Expected | Action if not met |
|---|---|---|
| `rms_jy` | Within 2× of radiometer estimate | > 2×: residual RFI or calibration artefacts; check CORRECTED column |
| `dynamic_range` | > 100 for calibrators; > 20 for typical targets | < 20: imaging artefacts dominant; check PSF sidelobes |
| `beam_major_arcsec` | Close to `lambda/max_baseline_m * (180*3600/pi)` | Large deviation: uv coverage gaps or flagging holes |
| `peak_jy` | Positive, above threshold | Negative peak > rms: clean diverged; reduce gain or niter |

If `rms_jy` is > 3× the radiometer estimate, run `ms_residual_stats` on the
CORRECTED column before re-imaging — the problem is likely in the calibration,
not the imaging parameters.

A `detection_pass`=false verdict (peak-to-noise < 10; fail ≤ 5) means no reliable
source — cross-check Step 0.5. If that gate failed, it's calibration decorrelation,
not imaging.

---

## Polarization frequency cubes (IQUV)

Use this path when the science needs Stokes Q/U as a function of frequency —
rotation-measure (RM) work, depolarization studies, or any check that the
polarization calibration (skill 09) holds across the band. Spectral-line cubes
(continuum subtraction, velocity frames) are a separate workflow and out of
scope here.

### Why a cube, not wideband MFS

`deconvolver='mtmfs', nterms=2` (Step 2's wideband continuum path) **cannot**
image `stokes='IQUV'` — the Taylor-term expansion is defined for total
intensity only, and CASA will error or silently mis-model Q/U/V. So for
polarization spectral coverage you image a **frequency cube**:
`specmode='cube'`, `stokes='IQUV'`, `deconvolver='hogbom'` (per-plane CLEAN; no
joint spectral deconvolution). This trades the wideband sensitivity/spectral-
index benefit of mtmfs for honest per-channel polarization.

A wideband Stokes-I headline image (mtmfs) and an IQUV cube (hogbom) are
complementary, not alternatives — run both if you need both the deep I image
and the polarization spectrum.

### Channelization

Default to **per-SPW-chunk** planes, not per-native-channel. One plane per SPW
(or per N-MHz chunk) gives enough λ² sampling for RM synthesis without the cost
and per-plane noise of full spectral resolution. This mirrors the chunking
`ms_setjy_polcal` already uses to fit the polarization model.

- Derive `width` from the chunk size: e.g. a 1 GHz SPW split into 64 MHz
  planes → `width='64MHz'`, `nchan=16`. Match the chunking you used in polcal
  so the model and the cube share a frequency grid.
- Set `start` to the band's low edge and `outframe='LSRK'` (TOPO is acceptable
  for continuum polarization, but be explicit).
- Use **per-native-channel** only for narrow fractional bandwidth, or when RM
  is large enough that Q/U rotates within a chunk (chunk Δ(λ²) must keep the
  intra-chunk RM rotation well below a radian — otherwise bandwidth
  depolarization washes out the signal you are trying to measure).

### Call

Pass the cube args through `ms_tclean` (no separate tool):

```
ms_tclean(
    ms_path     = {VIS},
    imagename   = {WORKDIR}/{imagename}_iquv,
    field       = {TARGET_FIELD},
    stokes      = 'IQUV',
    specmode    = 'cube',
    deconvolver = 'hogbom',
    nchan       = {N_CHUNKS},
    start       = {BAND_LOW_EDGE},   # e.g. '1.0GHz'
    width       = {CHUNK_WIDTH},     # e.g. '64MHz'
    outframe    = 'LSRK',
    gridder     = {gridder},         # Steps 5–6 as usual
    cell        = {CELL},
    imsize      = [{IMSIZE}, {IMSIZE}],
    weighting   = 'briggs',
    robust      = 0.5,
    niter       = 50000,
    threshold   = {threshold},
    workdir     = {WORKDIR},
    execute     = False,
)
```

Cell size and imsize are derived as in Steps 3–4 using the **highest** frequency
in the band (smallest beam → finest cell), so every plane is adequately sampled.

### Quality gates (per-plane)

`ms_image_stats` returns a `planes` array (per Stokes, per channel) for a cube.
Reason over it — the tool does not interpret:

| Check | Expectation | If violated |
|---|---|---|
| Stokes I `rms_jy` per plane | rises smoothly toward band edges / RFI-flagged chunks | a single spiking plane = residual RFI or a flagged-out chunk; flag and re-image or drop the plane |
| Q, U `peak_jy` per plane | present and varying smoothly with frequency | Q/U at noise across all planes when the source is known-polarized = polcal (skill 09) didn't apply, or parang was off in applycal |
| V `peak_jy` per plane | near noise (most sources have negligible circular pol) | significant V everywhere = leakage (D-term) error; revisit skill 09 |
| fractional pol = sqrt(Q²+U²)/I | physically plausible (≲ a few–10% for most sources) | > ~20% smoothly across the band = I likely wrong (model/fluxscale); spiky = per-plane artefact |

Compute fractional polarization and RM-related quantities yourself from the
per-plane numbers — these are skill-level interpretations, deliberately not in
the tool.
