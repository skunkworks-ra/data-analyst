---
description: Polarization calibration sequence on a calibrated MS (Kcross → D-terms → Xf → applycal-with-parang). Follows skill 09-polcal-execution.md.
allowed-tools: ms_workflow_status, ms_pol_cal_conditions, ms_field_list,
               ms_parallactic_angle_vs_time, ms_setjy_polcal, ms_gaincal,
               ms_polcal, ms_applycal, ms_calsol_stats, ms_calsol_plot,
               Bash, Read, Write
---

Run polarization calibration on this MS: $ARGUMENTS

Load the `radio-interferometry` skill, then read its `09-polcal-execution.md`
supporting file (a sibling of that skill's `SKILL.md`) before
starting. Prerequisite: delay.K, bandpass.B, and gain.G (or gain.fluxscaled)
must already exist — run `/calibrate` first if not.

**Workflow:**

1. `ms_workflow_status(ms_path, workdir)` — confirm calibration is complete.

2. `ms_pol_cal_conditions(ms_path)` — measure the conditions, then decide.
   Apply 09-polcal-execution.md §Reading the conditions:
   - Angle standard (`pol_angle_calibrator.category == 'A'`) present
     → Steps 3–6, Xf included, whatever the PA coverage.
   - No angle standard → Steps 3–5 only; report that absolute EVPA is
     uncalibrated and which claims that rules out.
   - `variability_warning` set → proceed, and annotate EVPA results with it.
   - For the D-terms, take `recommended_df_poltype` with its basis, and read
     `pa_spread_deg` against the coverage table. Below ~30° state the coverage
     alongside every polarisation number; consider a better-covered field from
     `leakage_cal_candidates` and say so if you switch.

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

6. Position angle (only if an angle standard was observed): `ms_polcal(…, field=angle_cal,
   poltype='Xf', solint='inf', combine='scan',
   gaintable=[priorcals + K + B + G + kcross + dterms],
   caltable=workdir/xfcal.X, execute=False)` → run.

7. Applycal with `parang=True` (mandatory). Pass all 7 tables in order:
   priorcals → K → B → G → kcross → dterms → xfcal.

**Output:** summary of each polcal step, D-term amplitude distribution,
confirmation that CORRECTED_DATA contains polarization solutions for the
target fields, and the forward hand-off for `/image` with
stokes='IQUV'.
