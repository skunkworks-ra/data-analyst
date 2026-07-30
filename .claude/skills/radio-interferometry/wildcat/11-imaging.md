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

**Single-vs-mosaic guard:** before asking anything, count the science target
fields in `ms_field_list` (cross-check with `ms_scan_list` pointings). If only
one target field exists, this is a single-pointing observation — a user request
for a "mosaic" can only mean primary-beam-corrected wide-field imaging
(awp2/awproject gridder), not multi-pointing mosaicking. State this explicitly
and set `{IS_MOSAIC}=False`.

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

**Array-configuration cross-check (mandatory):** `{MAX_BASELINE_M}` must come
from `ms_baseline_lengths` on the actual MS, never from an assumed array
configuration. Estimate the synthesized beam:

```
beam_arcsec ≈ (lambda_m / {MAX_BASELINE_M}) * (180 * 3600 / pi)
```

and verify `cell ≈ beam/3` to `beam/5`. State both numbers. A cell derived for
the wrong assumed configuration wastes compute quadratically: e.g. a 4" cell
against a VLA D-config 40" beam is 10× oversampled — the image is valid but
gridding cost and image size balloon for no resolution gain. The max baseline
itself identifies the configuration (VLA: ~1 km D, ~3.4 km C, ~11 km B, ~36 km A).

---

## Step 4 — Derive image size

Primary beam FWHM:
```
pb_fwhm_arcsec = (1.02 * lambda_m / {DISH_DIAMETER_M}) * (180 * 3600 / pi)
```

**Single pointing:**
```
imsize_pixels = ceil(pb_fwhm_arcsec * 2 / cell_arcsec)
```

**Mosaic:** compute the bounding box of all pointing centres in `{POINTING_CENTERS}`,
convert angular extent to pixels, then add `pb_fwhm_arcsec` padding on each side:
```
imsize_pixels = ceil((mosaic_extent_arcsec + 2 * pb_fwhm_arcsec) / cell_arcsec)
```

**PB-driven image size for awp2/awproject (overrides the above):** when the
gridder (Step 6) will be `awp2` or `awproject`, the image must cover the full
primary beam response, not just the source:

- Compute `pb_fwhm_arcsec` at the **lowest frequency in the selected SPWs**
  (the PB is widest there), not at band centre.
- Minimum field of view: `imsize × cell ≥ 2 × pb_fwhm(ν_min)`.
- If the source extent is comparable to the beam (extent > 0.5 × FWHM), extend
  coverage to the pblimit radius — for `pblimit ≈ 0.02` that is ~2.5 × FWHM.
- `ms_tclean` defaults to `pblimit = -0.01` (no PB blanking), so the field of
  view must be wide enough that the A-term normalization sees the whole beam;
  do not rely on blanking to hide an undersized image.

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
| Single pointing, wide-field/low-freq, telescope in `{VLA, ALMA}` | `'awp2'` | from Step 5 |
| Single pointing AND W-terms required | `'wproject'` | from Step 5 |
| Single pointing AND W-terms not required | `'standard'` | not set |

Match against the **canonical** telescope name returned by `ms_inspect`
(`telescope` field) — `VLA`, not the raw `MS::OBSERVATION::TELESCOPE_NAME`
string, which may be `EVLA` or `JVLA`. Name normalization happens in the
telescope profile (`src/ms_inspect/data/telescopes/*.yaml`, `aliases`).

**awp2 is not mosaic-only.** For a single pointing where the source extent is
comparable to the primary beam, or where direction-dependent PB effects matter
(beam squint, feed asymmetry — typical for VLA L-band wide-field), `'awp2'` is
the right gridder: it models the DD primary beam and subsumes W-projection.

**awp2 checklist:**
- Always pass `wprojplanes` explicitly (from Step 5). If omitted, CASA defaults
  to 1 — no W-projection — silently.
- awp2 does **not** use a cfcache; that is an `awproject`-only mechanism. Do not
  expect convolution functions to be persisted to disk — the CF/geometry
  computation (potentially many hours) is repeated on every run.
- awp2 does not implement `conjbeams`: with `specmode='mfs'` it needs several
  major cycles to converge the wideband flux normalization. A single major
  cycle yields a dirty image with wrong flux scaling — do not judge source
  detection from it. Prefer `specmode='mvc'` for wideband awp2 imaging
  (supported by `ms_tclean`).
- If using `'awproject'` instead of `'awp2'`: always pass `cfcache` with a
  persistent path — without it, awproject recomputes convolution functions
  from scratch on every run (potentially many hours).

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

**Threading:** before launching the tclean script, set `OMP_NUM_THREADS` to the
number of available CPUs (e.g. `OMP_NUM_THREADS=$(nproc)`). Do not let it
default to 1 — gridding and FFT steps are OpenMP-parallel and a single-threaded
run multiplies wall-clock time on expensive gridders.

**Dirty-image gate before long runs:** for any expensive gridder (awp2,
awproject, wproject on large images), first run with `niter=0` and inspect the
dirty image peak against the expected source brightness. If the chosen
`threshold` exceeds the dirty peak, tclean performs zero CLEAN iterations and
restores an empty model — a full-cost run producing a noise image. Set the
threshold from the radiometer estimate (Step 7) only after the dirty peak
confirms the source is present at the expected level.

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
