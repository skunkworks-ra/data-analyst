# 06 — Failure Modes and Recovery

## MS origin failure modes

### UVFITS-converted MS

Symptoms:
- `telescope_name` is blank or "UNKNOWN"
- Antenna names are purely numeric ("0", "1", "2", ...)
- Scan intents are absent (all `intents == []`)
- HISTORY subtable is empty

Root cause: UVFITS format does not carry all CASA MS metadata. Importers
(AIPS FITTP, importuvfits) often drop or mangle telescope name, antenna
names, and scan intents.

Recovery:
1. Repair `TELESCOPE_NAME` in OBSERVATION subtable:
   ```python
   tb.open('<ms>/OBSERVATION', nomodify=False)
   tb.putcell('TELESCOPE_NAME', 0, 'VLA')   # or 'MeerKAT', 'GMRT'
   tb.close()
   ```
2. Repair antenna names from observatory antenna table (e.g., VLA antenna
   positions are available from the VLA Calibration Manual).
3. Set intents manually if known, or use field-name-based heuristics
   (already done by `ms_field_list` in heuristic mode).

### GMRT LTA (Long Term Archive) data

Symptoms:
- Older GMRT data uses a non-standard MS format
- `SPECTRAL_WINDOW` may have duplicate SpW IDs
- Antenna names may be "C00", "C01", ..., "E05" (arm-antenna notation)
- Scan intents sometimes absent in pre-2016 observations

Notes:
- uGMRT GWB data (post-2017) is generally clean and CASA-compliant.
- For legacy GMRT data, use `ms.listobs` output as a sanity check.
- The arm-antenna names are valid — do not confuse them with numeric names.

### MeerKAT archive data

MeerKAT data from the SARAO archive is generally well-formed.
Known issues:
- Very early MeerKAT data (pre-2019) may lack scan intents.
- MeerKAT+ (80-antenna) data may have partial antenna tables during
  commissioning periods.
- CAM/CBF metadata sometimes places the correlator dump time in the
  EXPOSURE column rather than INTERVAL — `ms_correlator_config` handles
  this via `msmd.exposuretime()` but verify if dump time looks wrong.

---

## Phase 1 tool failure modes

### `ms_observation_info` — empty OBSERVATION table
Symptom: `n_observation_rows == 0`
Cause: Extremely rare but possible with partially-written or mock MSs.
Action: Cannot proceed. The MS may be from a simulator or corrupted write.

### `ms_field_list` — all fields at (0°, 0°)
Symptom: All `ra_j2000_deg.flag == "SUSPECT"`
Cause: UVFITS export with missing direction metadata, or a simulator
that set all directions to the reference position.
Action: Cannot compute elevation or PA for any field. Phase 1 can still
characterise intents and scan structure. Phase 2 geometry tools
(Steps 2.3, 2.4) will return all `UNAVAILABLE`.

### `ms_spectral_window_list` — SpW with negative channel widths
Symptom: Tool warns about non-uniform channel widths
Cause: CASA stores channel widths as signed (negative = decreasing freq order).
Some exporters preserve the sign. The tool takes absolute values — this is
handled internally. No action needed.

### `ms_scan_list` — missing scan numbers
Symptom: Scan number gaps warned (e.g., scan 3 → scan 9)
Cause 1: Scans were deleted (e.g., bad slew scan removed with `clearscan`).
Cause 2: MS is a time-selection of a larger dataset.
Cause 3: Online flags removed scans entirely.
Action: Note the gap. If this is a science MS, contact the PI or archive.
If it is expected (sub-selection), document it.

---

## Phase 2 tool failure modes

### `ms_baseline_lengths` — all baselines near zero
Symptom: `max_baseline_m < 10`
Cause: All antennas have placeholder ECEF positions (0, 0, 0) or the same
position (co-located). Common in simulated MSs.
Action: Report positions as `SUSPECT`. Baseline statistics are meaningless.
All derived angular scales (resolution, LAS) are `UNAVAILABLE`.

### `ms_elevation_vs_time` — astropy import failure
Symptom: Tool returns error or empty results
Cause: `astropy` not installed in the environment.
Action: `pixi install` or `pip install astropy>=6.0`.
Elevation and PA cannot be computed without astropy.

### `ms_antenna_flag_fraction` — slow or worker failure
Symptom: Tool takes > 5 minutes or returns partial results
Cause: Large MS (> 50 GB) with multiprocessing spawn overhead, or
casatools cannot be imported in worker processes.
Action:
- Always start with `n_workers=1` (serial). Only increase with explicit user approval.
- Single-process mode is the default; parallelisation is opt-in, not opt-out.
- For very large MSs (> 200 GB), consider running on the HPC node where
  the data lives using HTTP transport: `RADIO_MCP_TRANSPORT=http`.

### `ms_shadowing_report` — `msmd.shadowedAntennas()` unavailable
Symptom: `method.flag == "INFERRED"`, warning about unavailable method
Cause: CASA version < 6.4 or a casatools build that omitted this method.
Action: Only FLAG_CMD shadow entries are reported. Check manually by
running `flagcmd(vis=..., action='list', flagbackup=False)` in CASA and
filtering for 'shadow' reason codes.

---

## Unrecoverable failure modes (NO-GO)

These conditions mean the dataset cannot be calibrated with standard tools:

1. **Missing antenna positions** (all at origin) + no observatory lookup table
   available: UV coordinates cannot be computed. Visibilities are uncalibratable.

2. **Single-scan MS with no calibrators** and no cross-calibration MS available:
   No flux scale, no bandpass, no phase solutions. Images will be uncalibrated.

3. **Corrupt FLAG column** (tb.getcolslice fails on all rows): Cannot assess
   pre-existing flags. Risk of propagating corrupted flags through calibration.

4. **TELESCOPE_NAME blank** + antenna positions all at origin: Both identity-
   critical pieces of metadata are missing. Cannot identify the array or compute
   any geometry. Repair is required before any analysis.

For cases 1–4, provide the user with the exact error message from the tool,
the error_type code, and the repair path if one is known.
