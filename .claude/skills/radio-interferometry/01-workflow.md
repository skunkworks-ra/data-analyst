# 01 — Analysis Workflow

## The two-phase model

Phase 1 answers: **"What is in this dataset?"**
Phase 2 answers: **"Is this dataset trustworthy enough to calibrate?"**

Run Phase 1 completely before starting Phase 2. Never skip ahead.
A dataset that fails Phase 1 checks may not be safe to characterise further.

---

## Phase 1 — Orientation (Layer 1 tools)

Run these tools in order. Each one's output informs whether the next
tool's results make sense.

### Step 1.1 — `ms_observation_info`

**You are asking:** Who observed this, when, with which telescope?

**Decision gate — STOP and raise `INSUFFICIENT_METADATA` if:**
- `telescope_name.flag == "UNAVAILABLE"` or value is blank/unknown.
  No telescope identity → no band inference, no primary beam, no
  baseline-configuration classification. Repair first.

**What to note:**
- Total duration. For a typical VLA/MeerKAT science observation:
  - < 1 hour total → likely a snapshot or calibrator-only run
  - 4–12 hours → standard science track
  - > 12 hours → concatenated or very deep observation; check for
    non-contiguous time ranges in warnings
- `history_entries` count. Zero history entries in an MS that claims
  to be calibrated is a red flag — CASA writes history on every task.

### Step 1.2 — `ms_field_list`

**You are asking:** What targets and calibrators were observed?

**Decision gate — CHECK completeness:**
- If `heuristic_intents == true`: intents were inferred from field names.
  Cross-check inferred intents against scan time fractions in Step 1.4.
  A field inferred as flux calibrator that received 90% of the time is
  probably wrong — re-examine.

**What to note:**
- Number of fields and their roles. Minimum viable calibration requires:
  - 1 flux/bandpass calibrator (for absolute flux scale + bandpass shape)
  - 1 phase calibrator per science target (within ~15° on sky for VLA)
  - 1 science target
- Mosaics: multiple fields with the same source_id → mosaic observation.
  Flag this — imaging strategy will differ from single-pointing.
- `resolved_source.value == true` for any calibrator → resolved source warning
  needed before proceeding. See `05-calibrator-science.md`.
- `ra_j2000_deg.flag == "SUSPECT"` → broken UVFITS export. Elevation and
  PA cannot be computed for this field. Note which fields are affected.

### Step 1.3 — `ms_scan_list`

**You are asking:** What is the temporal structure of the observation?

**What to note:**
- Alternation pattern of calibrator and target scans. Standard pattern:
  flux_cal → (phase_cal → target → phase_cal) × N → flux_cal
  Missing bookend flux calibrators means no absolute flux scale unless
  one is embedded mid-observation.
- Integration time (`integration_s`). Typical values:
  - VLA: 1–10 s standard; 50 ms fast-cadence (solar/transients)
  - MeerKAT: 2–8 s standard
  - uGMRT: 2–8 s standard
  Very long integrations (> 60 s) on a phase calibrator risk decorrelation
  at long baselines in poor ionospheric conditions.
- Scan number gaps → possible missing data. Warn the user explicitly.

### Step 1.4 — `ms_scan_intent_summary`

**You are asking:** How was observing time distributed?

**What to note:**
- Fraction of time on target vs calibrators. For a typical science track:
  - Flux/bandpass calibrator: 5–15% of total time
  - Phase calibrator: 10–20% of total time
  - Science target: 65–80% of total time
  Deviations from these ranges warrant a comment. A track where 80% of
  time is on the flux calibrator is almost certainly a calibrator-only
  or test observation, not a science dataset.
- If `intent_completeness == "UNAVAILABLE"`: breakdown is by field name.
  Cross-check against `ms_field_list` calibrator identifications.

### Step 1.5 — `ms_spectral_window_list`

**You are asking:** What is the frequency coverage and spectral resolution?

**What to note:**
- Band identification. Confirm band name matches the science goal.
  L-band (1–2 GHz): HI, OH, continuum. C-band (4–8 GHz): continuum, masers.
- Channel width. For spectral line work:
  - Line of interest must be resolved by at least 3–5 channels
  - Channels narrower than ~1 kHz are unusual for standard observations
- Single-channel SpWs (1 channel): frequency-averaged. Per-channel
  bandpass calibration is impossible — warn that bandpass solutions
  will be applied as a single scalar per SpW.
- Number of SpWs. VLA wideband: typically 16–64 SpWs of 64 MHz each.
  MeerKAT: 1 SpW of 4096 channels. uGMRT GWB: 1 SpW of 2048–8192 channels.

### Step 1.6 — `ms_correlator_config`

**You are asking:** What polarization products were recorded?

**What to note:**
- `polarization_basis`: circular (VLA, default) or linear (MeerKAT, uGMRT).
- `full_stokes == false`: only parallel hands (RR+LL or XX+YY) recorded.
  Full polarimetric imaging is impossible. Total intensity (Stokes I) and
  circular polarization (Stokes V from RR-LL or XX-YY) may still be feasible.
- `dump_time_s`: confirm this matches expected integration time from scan list.

---

## Phase 2 — Instrument Sanity (Layer 2 tools)

Run these after Phase 1 is clean. These tools check whether the hardware
behaved correctly and the geometry is consistent.

### Step 2.1 — `ms_antenna_list`

**Decision gate — STOP and raise `INSUFFICIENT_METADATA` if:**
- Antenna names are purely numeric → broken UVFITS export. Cannot proceed.
- Orphaned antenna IDs → incomplete antenna table. Cannot proceed.

**What to note:**
