---
description: Polarization calibration sequence on a calibrated MS (Kcross → D-terms → Xf → applycal-with-parang). Follows skill 09-polcal-execution.md.
allowed-tools: ms_workflow_status, ms_pol_cal_conditions, ms_field_list,
               ms_parallactic_angle_vs_time, ms_setjy_polcal, ms_gaincal,
               ms_polcal, ms_applycal, ms_calsol_stats, ms_calsol_plot,
               Bash, Read, Write
---

Run polarization calibration on this MS: $ARGUMENTS

Load the `radio-interferometry` skill, then read its
`09-polcal-execution.md` supporting file before starting (it sits beside that
skill's SKILL.md; do not look for it under the current working directory). Prerequisite: delay.K, bandpass.B, and gain.G (or gain.fluxscaled)
must already exist — run `/radio-analyst:calibrate` first if not.

**Workflow:**

1. `ms_workflow_status(ms_path, workdir)` — confirm calibration is complete.

2. `ms_pol_cal_conditions(ms_path)` — measured conditions, no verdict.
   Work through 09-polcal-execution.md §Conditions Steps A to C and decide:
   - Category A angle standard present → do Xf (Step 6), regardless of PA spread.
   - No angle standard → skip Steps 3 and 6; D-terms only, and state in the
     report that absolute EVPA is uncalibrated.
   - Df path comes from `recommended_df_poltype`; check it against
     `recommended_df_poltype_basis`. It does not depend on PA spread.
   - For the `Df+QU` path only, compare `pa_spread_deg` against the returned
     `pa_spread_reference_deg` and `pa_spread_practical_floor_deg`, and carry the
     consequence into the report rather than stopping on a threshold.
   - Choose the leakage field yourself from `leakage_cal_candidates` if the
     primary is unsuitable; the tool does not substitute one.

3. `ms_setjy_polcal(ms_path, field=angle_cal, reffreq_ghz=<band centre>,
   workdir, execute=False)` → run. Populates MODEL for the angle cal.

4. Cross-hand delay: `ms_gaincal(…, field=angle_cal, gaintype='KCROSS',
   solint='inf', combine='scan,spw', smodel=[1, 0, 1, 0],
   gaintable=[priorcals + K + B + G], caltable=workdir/kcross.K, execute=False)`
   → run. `ms_calsol_stats(kcross.K)` — delay must be stable.

5. D-term leakage: `ms_polcal(…, field=leakage_cal, poltype='Df+QU',
   solint='inf', combine='scan', gaintable=[priorcals + K + B + G + kcross],
   caltable=workdir/dterms.D, execute=False)` → run.
   `ms_calsol_stats(dterms.D)` — D-term amplitudes < 0.1 expected.

6. Position angle (skip if LEAKAGE_ONLY): `ms_polcal(…, field=angle_cal,
   poltype='Xf', solint='inf', combine='scan',
   gaintable=[priorcals + K + B + G + kcross + dterms],
   caltable=workdir/xfcal.X, execute=False)` → run.

7. Applycal with `parang=True` (mandatory). Pass all 7 tables in order:
   priorcals → K → B → G → kcross → dterms → xfcal.

**Output:** summary of each polcal step, D-term amplitude distribution,
confirmation that CORRECTED_DATA contains polarization solutions for the
target fields, and the forward hand-off for `/radio-analyst:image` with
stokes='IQUV'.
