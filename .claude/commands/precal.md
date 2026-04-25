---
description: Pre-calibration workflow for a CASA Measurement Set (online flags → preflag → priorcals → setjy → refant → initial bandpass → residual rflag). Follows skill 10-precal-workflow.md.
allowed-tools: ms_observation_info, ms_field_list, ms_scan_list, ms_scan_intent_summary,
               ms_spectral_window_list, ms_correlator_config, ms_antenna_list,
               ms_verify_import, ms_set_intents, ms_online_flag_stats,
               ms_apply_preflag, ms_flag_summary, ms_generate_priorcals,
               ms_verify_priorcals, ms_setjy, ms_refant, ms_initial_bandpass,
               ms_verify_caltables, ms_residual_stats, ms_apply_initial_rflag,
               ms_rfi_channel_stats, ms_workflow_status, Bash, Read, Write
---

Run the pre-calibration workflow on this MS: $ARGUMENTS

Read `.claude/skills/radio-interferometry/10-precal-workflow.md` before starting.
Execute the stages below in order; STOP and report if any stage fails its
decision gate.

**Workflow:**

1. `ms_workflow_status(ms_path, workdir)` — where are we starting from?
   If `next_recommended_step` is past pre-calibration, STOP and tell the user.

2. `ms_verify_import(ms_path, online_flag_file)` — structural gate. If
   `ready_for_preflag` is False, STOP.

3. If intents are missing from Phase-1 orientation: `ms_set_intents(ms_path)`.

4. `ms_online_flag_stats(online_flag_file)` — assess online flags.
   Apply 10-precal-workflow.md §Step 1 decision table.

5. `ms_apply_preflag(ms_path, workdir, cal_fields, online_flag_file, execute=False)`
   → run the generated script as a background job, wait for completion.

6. `ms_flag_summary(calibrators.ms)` — baseline flag fraction.
   Apply §Step 2 decision table.

7. `ms_generate_priorcals(calibrators.ms, workdir, execute=False)` → run.
   Then `ms_verify_priorcals(workdir)` — all required tables present?

8. `ms_setjy(calibrators.ms, workdir, execute=False)` → run.
   Check warnings field for resolved calibrators (3C84 uvrange gate).

9. `ms_refant(calibrators.ms, field=bp_field)` — choose refant.
   If top-ranked refant is > 30% flagged, use rank-2.

10. `ms_initial_bandpass(calibrators.ms, bp_field, refant, workdir,
    priorcals=<from step 7>, uvrange=<if 3C84>, execute=False)` → run.
    Then `ms_verify_caltables` — both tables exist with rows?

11. `ms_residual_stats(calibrators.ms, bp_field_id)` — inspect tail ratio.
    Apply §Step 7 decision table.

12. `ms_apply_initial_rflag(calibrators.ms, workdir, execute=False)` → run.

13. `ms_flag_summary(calibrators.ms)` — post-rflag. Compare to step 6 delta.
    Apply §Step 8 decision table.

14. Final decision gate (§Decision gate): go/no-go for full calibration solve.

**Output:** a structured report of each stage's outcome, flagged warnings,
and the forward hand-off values (`refant`, `priorcals`, `bp_field`, `workdir`)
ready for `/project:calibrate`.
