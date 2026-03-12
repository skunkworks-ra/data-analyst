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

### PKS1934-638 (MeerKAT)
Unpolarised to < 0.2%. Do NOT use for polarisation angle calibration.
For MeerKAT polarimetry: use a separate linearly polarised calibrator
observed at multiple parallactic angles.

### Identifying which calibrator to use for which step
In a standard VLA L-band reduction:
1. `setjy` → 3C286 (flux scale)
2. `bandpass` → 3C286 (bandpass calibrator, same scan)
3. `gaincal(calmode='p')` → phase calibrator (phase-only, short solint)
4. `gaincal(calmode='ap')` → flux calibrator (amplitude+phase, long solint)
5. `fluxscale` → transfer flux scale from flux cal to phase cal
6. `polcal(poltype='Df')` → D-terms from calibrator with good PA coverage
7. `polcal(poltype='Xf')` → absolute PA from 3C286 or 3C138

This sequence is out of scope for Phase 1/2 but is documented here so that
the Phase 1/2 analysis report can flag whether all necessary calibrators
are present.
