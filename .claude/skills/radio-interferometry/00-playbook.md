# 00 — Playbook (stage → next action)

Find your current state in the left column. Run the right-column tool
(or read the named skill file for decision tables).

## Stage transitions

| Current state | Next action |
|---------------|-------------|
| Starting from raw ASDM | `ms_sdm_summary(sdm_path)` first (free triage) → `ms_import_asdm(..., execute=False)` → run script → `ms_verify_import` |
| MS imported, no intents | `ms_set_intents(ms_path)` → proceed |
| MS imported, intents present | Run `/inspect` or read `01-workflow.md` |
| Phase 1 + 2 inspection done, go decision | Run `/precal` or read `10-precal-workflow.md` |
| Pre-cal complete (rflag done, CORRECTED populated) | Run `/calibrate` or read `07-calibration-execution.md` |
| Calibration solve done (G/B/K/fluxscale) | For pol: `/polcal` or read `09-polcal-execution.md`; else `/image` |
| Polcal done | `/image` with stokes='IQUV' or read `11-imaging.md` |
| First-pass image done | Read `12-selfcal.md` — one-pass phase selfcal with before/after assessment |
| Final applycal done, RFI on target/phase cal | Read `13-postcal-rfi-flagging.md` — SpW severity triage + post-cal flagging |

## Unknown state? Use ms_workflow_status(ms_path, workdir) — it returns a next_recommended_step label.

## Reduction ledger
After each validated step above (script generated, run, and its verify tool
passed), call `ms_reduction_log(action='append', workdir=..., tool=..., params=...,
outputs=..., rationale=..., skill_rule=...)`. Only shuttle calls that worked —
skip failed attempts and dead ends. Use `action='render'` for the replay
script, `action='list'` for a quick step summary.

## Load skill files on demand
- 01-workflow.md / 01b-workflow-phase2.md — orientation + instrument sanity
- 02-orientation.md — band tables, intents, mosaics
- 03-instrument-sanity.md — array configs, elevation/PA/flag thresholds
- 04-diagnostic-reasoning.md — report template, go/no-go
- 05-calibrator-science.md — flux standards, resolved sources
- 06-failure-modes.md — recovery paths
- 07-calibration-execution.md — solve sequence (read only when you reach calibration)
- 07b-gaincal-recovery.md — gaincal recovery trees + escalation (read only when a Step 4b post-flight check fails)
- 08-pband-specifics.md — VLA P-band
- 09-polcal-execution.md — polarization
- 10-precal-workflow.md — pre-calibration pipeline
- 11-imaging.md — first-pass imaging
- 12-selfcal.md — single-pass phase selfcal with before/after DR comparison
- 13-postcal-rfi-flagging.md — SpW severity triage (drop vs salvage) + post-cal flagging on target/phase cal

Read each file with the Read tool when you reach that stage — do not load everything up front.
