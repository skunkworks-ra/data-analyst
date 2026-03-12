# 04 — Diagnostic Reasoning: Synthesising Tool Output

## How to structure your analysis report

After running Phase 1 and Phase 2 tools, produce a structured report
with the following sections. This report is your deliverable — it is
what an experienced interferometrist would hand to a PI before
starting CASA calibration.

---

### Report section 1: Dataset identity

State clearly:
- Telescope and array configuration
- Observation date(s) and total duration
- Frequency band and total bandwidth
- Polarization products recorded
- Number of antennas (vs. expected complement)

Example (synthesised from tool outputs):
> VLA B-configuration. Observed 2017-09-14, total duration 4h 32m.
> L-band (1–2 GHz), 1 GHz total bandwidth across 16 SpWs of 64 MHz each.
> Full Stokes (RR, RL, LR, LL). 27 antennas present (full complement).

---

### Report section 2: Field summary

List each field with its identified role:
- Flux/bandpass calibrator(s) — name, flux standard, resolved status
- Phase calibrator(s) — name, angular separation from each science target
- Science target(s) — name, coordinates
- Note any missing calibration roles

Example:
> Flux/bandpass: 3C286 (Perley-Butler 2017, unresolved at B-config).
> Phase calibrator: J1407+2827, 8.3° from target NGC 1234.
> Science target: NGC 1234 (HI 21 cm emission).
> WARNING: No secondary phase calibrator. Long target scans (>30 min)
> may suffer from uncorrected phase drift.

---

### Report section 3: Spectral configuration

State for each unique SpW group:
- Centre frequency and bandwidth
- Number of channels and channel width
- Whether per-channel bandpass calibration is feasible

Flag single-channel SpWs explicitly.

---

### Report section 4: Data quality summary

State pass/fail for each Phase 2 check:

| Check | Status | Notes |
|-------|--------|-------|
| Antenna complement | PASS / PARTIAL / FAIL | N antennas, N expected |
| Angular resolution | PASS | N arcsec at band |
| LAS vs source size | PASS / WARNING | Source size vs LAS |
| Elevation (all scans) | PASS / WARNING / FAIL | Minimum elevation seen |
| PA coverage | PASS / WARNING / FAIL | PA range for pol calibrator |
| Shadowing | PASS / DETECTED | Events and duration |
| Flag fraction | PASS / WARNING / FAIL | Overall fraction, outliers |

---

### Report section 5: Go / No-go recommendation

Based on sections 1–4, state one of:

**GO:** Dataset is suitable for standard calibration. Proceed to
calibration with the following notes: [list any cautions].

**GO WITH CONDITIONS:** Dataset has issues that must be addressed
before calibration. Required actions: [list actions]. Once complete,
re-assess.

**NO-GO:** Dataset has a disqualifying problem. Reason: [state reason].
Recommended path forward: [repair instructions or archive contact].

---

## Cross-cutting checks (run these mentally across all Phase 1 + 2 output)

### Consistency checks

After collecting all tool outputs, verify internal consistency:

1. **Duration vs scan count:** `total_duration_s` from `ms_observation_info`
   should approximately equal the sum of scan durations from `ms_scan_list`.
   Discrepancy > 10%: investigate for missing scans or large off-source gaps.

2. **Field count cross-check:** `n_fields` from `ms_field_list` should equal
   the number of unique `field_name` values in `ms_scan_list`.
   Mismatch: a field in the FIELD table with no scans (defined but not observed).

3. **SpW count cross-check:** `n_spw` from `ms_spectral_window_list` should
   match `n_spw` from `ms_correlator_config`.

4. **Antenna count cross-check:** `n_antennas` from `ms_antenna_list` should
   be consistent with baselines formed: n_baselines = n × (n−1) / 2.
   Compare with `n_baselines_cross`.

5. **Calibrator time vs role:** if `ms_scan_intent_summary` shows a known
   flux calibrator receiving > 30% of total time, something is wrong with
   the observation plan. Flag it.

### Flag chain — when one check fails

If Phase 1 Step 1.1 fails (no telescope name):
→ STOP. Steps 1.2–1.6 may still run but all telescope-specific
  interpretations (band names, configuration, primary beam, LAS) will
  carry `UNAVAILABLE` flags. Do not attempt Phase 2.

If Phase 2 Step 2.1 fails (numeric antenna names or orphaned IDs):
→ STOP Phase 2. Steps 2.2–2.6 require a valid antenna table.
  Steps 2.3 and 2.4 (elevation, PA) may still run using msmetadata field
  coordinates, but baseline-length-derived quantities cannot be trusted.

If any Phase 2 check produces `INSUFFICIENT_METADATA`:
→ Record the exact repair command from the error message.
  Do not attempt to infer or substitute. Present the repair path to the user.

---

## Writing the final report: tone and specificity

- State numbers with the precision returned by the tools (4 decimal places
  for coordinates, 2 for durations, etc.). Do not round further unless
  presenting to a non-technical audience.
- State completeness flags explicitly when they are not COMPLETE.
  Example: "Inferred intent: CALIBRATE_FLUX (INFERRED — matched field name
  '3C286' to catalogue)."
- Do not editorialize. "The phase calibrator separation is 8.3°" is correct.
  "The phase calibrator separation is adequate" is an interpretation that
  requires you to know the operating frequency and expected phase coherence
  time — state both the number and the threshold you are comparing it to.
- Warn, don't block, on non-fatal issues. A resolved calibrator warning,
  a low-elevation scan, or a 25% flag fraction are all warnings — they
  require action but do not preclude analysis.
