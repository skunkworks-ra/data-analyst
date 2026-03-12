# 01b — Phase 2 Workflow: Instrument Sanity Steps

Run these after Phase 1 is clean. See `01-workflow.md` for Phase 1 steps.

- Any antenna with position `(0, 0, 0)` → placeholder position.
  Baselines involving this antenna are geometrically wrong.
- Cross-check `n_antennas` against expected array complement:
  - VLA: 27 antennas (sometimes fewer for maintenance)
  - MeerKAT: 64 antennas (MeerKAT+: 80)
  - uGMRT: 30 antennas (6 arms × 5 antennas)
  Fewer antennas than expected → note the shortfall; will affect
  UV coverage and sensitivity.

### Step 2.2 — `ms_baseline_lengths`

**You are asking:** What angular resolution and largest angular scale can
this observation achieve?

**What to note:**
- `resolution_arcsec`: this is θ ≈ λ/B_max. It is an approximation —
  actual synthesised beam depends on weighting (natural/uniform/robust)
  and actual UV coverage.
- `las_arcsec`: largest recoverable angular scale (λ/B_min). Sources
  larger than this will have flux resolved out. This is critical for:
  - Extended emission (HI disks, SNRs, diffuse radio sources)
  - MeerKAT imaging where the short baseline complement is often
    the scientific bottleneck
- Array configuration classification for VLA (B_max in km):
  - D: ≤ 1 km  | C: ≤ 3.4 km  | B: ≤ 11.1 km  | A: ≤ 36.4 km
  State the inferred configuration in your report.

### Step 2.3 — `ms_elevation_vs_time`

**You are asking:** Was the source ever dangerously low on the horizon?

**Thresholds:**
- < 10°: data almost certainly unusable (atmospheric emission, gain errors)
- 10°–20°: use with caution; flag for inspection
- 20°–30°: acceptable but increased atmospheric contribution
- > 30°: normal operating range

**What to note:**
- Low-elevation scans should be flagged before calibration, not after.
  Note any scan below 20° and recommend flagcmd or manual flagging.
- Elevation at start vs end of target scans → rising vs setting source.
  For long tracks, a source that transits during the observation gives
  the best UV coverage.

### Step 2.4 — `ms_parallactic_angle_vs_time`

**You are asking:** Is the parallactic angle coverage sufficient for
instrumental polarisation calibration (D-term solutions)?

**IMPORTANT — VALIDATION PENDING:**
All output from this tool carries `validation_status: "PENDING"`.
Do NOT use `pa_feed_deg` values for actual D-term solutions until
cross-validation against `casatools.measures` is complete.
Use `pa_sky_deg` range for coverage assessment only.

**Coverage thresholds (ALT-AZ arrays — VLA, MeerKAT, uGMRT):**
- `pa_sky_range_deg < 30°`: insufficient for D-term solutions.
  Full polarimetric calibration requires ≥ 60° of PA coverage.
  Recommend observing a calibrator at different hour angles or
  using a known-polarisation calibrator (3C286, 3C138 at VLA).
- `pa_sky_range_deg ≥ 60°`: adequate for standard D-term solutions.
- Equatorial mount detected: PA is constant. D-term coverage criterion
  does not apply — use a different polarisation calibration strategy.

**What to note:**
- Report `pa_sky_range_deg` per field, not just per calibrator.
  Science target PA coverage is not relevant for calibration, but
  calibrator PA coverage is.

### Step 2.5 — `ms_shadowing_report`

**You are asking:** Were any antennas physically blocked during the observation?

**What to note:**
- Any shadowing on the flux or bandpass calibrator scans is serious —
  it corrupts the amplitude scale that everything else is referenced to.
  Recommend excising the shadowed antennas from those scans explicitly.
- Shadowing at low elevation is expected for compact array configurations
  (VLA D-config, MeerKAT inner core). Note which antennas and which scans.
- If `method.flag == "INFERRED"`: msmd.shadowedAntennas() was unavailable.
  Check FLAG_CMD entries only. Do not treat absence of events as confirmation
  of no shadowing.

### Step 2.6 — `ms_antenna_flag_fraction`

**You are asking:** Are any antennas pre-flagged at an anomalously high rate?

**Thresholds:**
- `flag_fraction > 0.80`: antenna is effectively dead for this observation.
  Exclude it from calibration solutions to avoid corrupting the solution.
- `flag_fraction > 0.30`: significant data loss. Investigate before calibrating.
  May indicate a receiver failure, RFI environment, or known maintenance period.
- `flag_fraction < 0.05`: normal for most arrays in benign RFI environments.

**What to note:**
- Cross-check `n_flag_commands_online` (from FLAG_CMD). High online flag
  counts on an antenna that shows low flag fraction in the data means the
  online flags were not applied — run `flagcmd(action='apply')` before proceeding.
- Highly-flagged antennas contribute short or intermediate baselines
  preferentially (they tend to be core antennas taken offline for maintenance).
  This can artificially compress the effective minimum baseline and inflate
  the apparent LAS.
