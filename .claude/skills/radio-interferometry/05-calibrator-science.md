# 05 — Calibrator Science

## Why calibrators matter

Radio interferometers measure complex visibilities V(u,v). The instrument
corrupts these through:
- **Bandpass:** frequency-dependent complex gain per antenna
- **Complex gain:** time-varying amplitude + phase per antenna
- **Instrumental polarisation (D-terms):** leakage between feeds
- **Absolute flux scale:** the Jy scale is set by reference to a known source

Calibrators provide known reference signals that allow these corruptions
to be solved for and removed. Wrong calibrator identification or misuse
of a resolved calibrator as a point source propagates errors through
the entire calibration chain.

---

## Flux density standards

### Perley-Butler 2017 (VLA default)
Applies to: VLA (all bands), uGMRT (approximately)
Sources: 3C286, 3C48, 3C138, 3C147, CasA, CygA, TauA, VirA
Scale: tied to Baars et al. 1977, updated with VLA monitoring data
CASA model name: `perley-butler-2017`

### Reynolds 1994 (MeerKAT default for PKS1934-638)
Applies to: MeerKAT, ATCA
Source: PKS1934-638 only
Note: At 1.4 GHz, S ≈ 14.9 Jy. Flux model valid 400 MHz – 8.6 GHz.
Outside this range: use with caution, note uncertainty.

### Stevens 2004 (PKS0408-65)
Applies to: MeerKAT (when PKS1934-638 is not visible)
Source: PKS0408-65 only
Note: Less well-characterised than PKS1934-638. Prefer PKS1934-638 when
both are above 20° elevation.

---

## Resolved calibrator handling

A resolved calibrator treated as a point source will produce:
- Incorrect bandpass amplitude shape (typically suppressed at long baselines)
- Wrong absolute flux scale (flux underestimated)
- Phase solutions that converge to a local minimum, not the true phase

### When to worry

The catalogue in `util/calibrators.py` stores safe UV ranges per band.
If `ms_baseline_lengths` shows the maximum baseline exceeds the safe range,
`ms_field_list` will issue a `CALIBRATOR_RESOLVED_WARNING`.

**When you see this warning:**
1. Do NOT use `setjy(model='point')` or `setjy` without a model.
2. Use the component model: `setjy(vis=..., field='CasA', model='CasA_Epoch2010.0')`
3. The warning message contains the exact `setjy` command to use.
4. For CasA, also apply the secular flux decline correction (~0.6%/yr at GHz frequencies).

### Source-specific resolved calibrator notes

**CasA (Cassiopeia A)**
- ~4 arcmin diameter SNR. Resolved at essentially all VLA configurations above D.
- Flux declines ~0.6%/yr since 1965. Apply epoch correction in `setjy`.
- Component model: `CasA_Epoch2010.0` (update epoch as needed).
- CASA command: `setjy(vis=..., field='CasA', standard='Perley-Butler 2017', epoch='2017.5')`

**CygA (Cygnus A)**
- Double-lobed structure, lobe separation ~1.5 arcmin.
- Core is variable at > 10 GHz on months–years timescale. Avoid as flux
  calibrator at K-band and above.
- At L-band: safe up to ~50 kλ max baseline. Above this, use component model.
- Component model: `3C405_CygA`

**TauA (Crab Nebula)**
- ~7 arcmin SNR diameter. Heavily resolved at B-config and above.
- Flux declines ~0.2%/yr at 1 GHz.
- Useful mainly in D-config or at low frequencies.

**VirA (M87)**
- Core + extended lobes + visible jet. Core variable.
- Component model required at B-config and above at L-band.

---

## Polarisation calibration roles

### 3C286 and 3C138 (VLA)
Both are linearly polarised (~11% at L-band for 3C286, ~8% for 3C138).
They are used for:
1. **Absolute polarisation angle calibration:** `polcal(poltype='Xf')`
   Requires knowing the intrinsic EVPA of the source (tabulated).
   3C286 intrinsic EVPA: ~33° at L-band (defined relative to IAU convention).
2. **D-term calibration:** combined with PA coverage from `ms_parallactic_angle_vs_time`.

### 3C48 at S-band (2–4 GHz)
3C48 has low but usable linear polarisation at S-band and above, and is commonly
observed as the flux/bandpass calibrator. When it is the only well-characterised
calibrator in the observation, it can also serve as a position-angle calibrator.

**Key properties (Perley & Butler 2013):**
- Pol fraction rises from ~1.5% at 2.565 GHz to ~5.4% at 8.435 GHz
- PA at S-band: negative, ranging from −112.9° (2.565 GHz) to −63.4° (8.435 GHz)
- L-band nodes (< 2.0 GHz): PA is undefined due to Faraday rotation wrapping —
  do NOT include these frequencies in the polindex/polangle fit
- Recommended pol_freq_range_ghz for S-band: (2.0, 9.0) — includes 14 nodes with
  well-defined PA, excludes the three RM-wrapped L-band nodes

**Setting the model with ms_setjy_polcal:**
```
ms_setjy_polcal(
    ms_path=..., field='3C48', workdir=...,
    reffreq_ghz=3.0,             # S-band centre
    pol_freq_range_lo_ghz=2.0,
    pol_freq_range_hi_ghz=9.0,
)
```
This calls `fit_from_catalogue("3C48", ...)` and writes `setjy_polcal.py` using
`standard='manual'` with the fitted polynomial coefficients.

### PKS1934-638 (MeerKAT)
Unpolarised to < 0.2%. Do NOT use for polarisation angle calibration.
For MeerKAT polarimetry: use a separate linearly polarised calibrator
observed at multiple parallactic angles.

---

## Polynomial model convention for setjy(standard='manual')

CASA `setjy(standard='manual')` accepts polynomial models for polindex and polangle.
The convention is **ascending coefficient order**: `[c0, c1, c2, ...]` where the
polynomial variable is `x = (f − f_ref) / f_ref`.

```
polindex(f) = c0 + c1·x + c2·x² + ...     (fractional polarisation, 0–1 scale)
polangle(f) = c0 + c1·x + c2·x² + ...     (position angle in radians)
```

**Critical:** `c0` is the value at the reference frequency — it is the intercept,
not a slope. This is the ascending-order convention from
`numpy.polynomial.polynomial.polyfit`. Do NOT use `numpy.polyfit` for these fits
as it returns descending order (`[cn, ..., c1, c0]`), which would make `c0` the
highest-power coefficient — a silent correctness bug.

**Checking a fit is reasonable:**
- `polindex[0]` should match the catalogued pol fraction at reffreq (within ~0.5%)
- `polangle[0]` in radians should match the catalogued PA at reffreq
- For 3C48 at reffreq=3.0 GHz: `polindex[0] ≈ 0.022`, `polangle[0] ≈ −1.69 rad`

---

## Phase calibrator catalog lookup

The bundled NRAO VLA Calibrator Manual (`PhaseCalList.txt`, 1861 sources) is
accessible via `ms_phase_cal_lookup`. Use it to:

1. **Confirm a field is a known phase cal** — cross-match its J2000 position
   against the catalog. A match within 0.5° with pos_accuracy A or B is reliable.
2. **Check viability at your band and array config** — quality codes P/S are
   usable; W is phase-only; X means do not use.
3. **Get UV limits** — if `uvmax_kl` is set, the source is resolved at long
   baselines. Pass this to `gaincal(uvrange='<N>klambda')`.

### Typical call pattern

```
ms_phase_cal_lookup(
    ra_deg=<field RA from ms_field_list>,
    dec_deg=<field Dec from ms_field_list>,
    band_code='L',          # from ms_spectral_window_list centre frequency
    array_config='B',       # from ms_observation_info or antenna spacing
    max_sep_deg=0.5,
    min_quality='S',        # raise to 'P' for amplitude calibration
)
```

### Reading the result

- `status=NOT_FOUND` — the field is not a known VLA phase calibrator at this
  band/config. Check whether it is a target field mislabelled as a calibrator,
  or a southern-hemisphere source not in the VLA catalog.
- `quality_at_config='P'` — excellent. Use for phase and amplitude calibration.
- `quality_at_config='S'` — good. Use for both phase and amplitude calibration
  but expect 3–10% closure errors; inspect solutions after `gaincal`.
- `quality_at_config='W'` — phase-only. Do not use for amplitude calibration
  (`gaincal(calmode='p')` only). `fluxscale` will not give reliable results.
- `quality_at_config='X'` — do not use. Too resolved or too weak. The source
  may still be usable with a component model if one is available.
- `uvmin_kl` set — confused at short spacings (extended structure). Pass
  `uvrange='>Nklambda'` to `gaincal`.
- `uvmax_kl` set — resolved at long spacings. Pass `uvrange='<Nklambda'`
  to `gaincal`. Critical at A-config and X/K/Q bands.

### When to call this

During Phase 1 orientation (after `ms_field_list`), for every field whose
`calibrator_role` or `calibrator_match` suggests it is a phase calibrator.
Do this before recommending a calibration strategy — a W-quality source
at A-config changes the entire calibration approach.

---

## Identifying which calibrator to use for which step

In a standard VLA L-band/S-band reduction:
1. `setjy` → 3C286 or 3C48 (flux scale, Perley-Butler 2017)
2. `bandpass` → same as flux calibrator
3. `gaincal(calmode='p')` → phase calibrator (phase-only, short solint)
4. `gaincal(calmode='ap')` → flux calibrator (amplitude+phase, long solint)
5. `fluxscale` → transfer flux scale from flux cal to phase cal
6. `ms_setjy_polcal` → set full pol model (Stokes I + polindex + polangle) on angle cal
7. `polcal(poltype='Kcross')` → cross-hand delay from angle cal
8. `polcal(poltype='Df')` → D-terms from leakage cal with good PA coverage
9. `polcal(poltype='Xf')` → absolute PA from 3C286 or 3C138 (or 3C48 at S-band)
10. `applycal(parang=True)` → apply all tables with parallactic angle correction

Steps 6–10 are polarisation calibration and are covered in `09-polcal-execution.md`.
Steps 1–5 are documented here as context for flagging whether all required calibrators
are present during Phase 1/2 inspection.
