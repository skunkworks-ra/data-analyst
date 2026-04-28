# 01 — Phase 1: Orientation Tool Sequence

Run tools in order. Each step's output is used to validate the next.

## Tool sequence and routing rules

### 1.1 `ms_observation_info`

| Check | Pass | next_stage |
|-------|------|-----------|
| `telescope_name` known and non-blank | Proceed | `STOP — repair OBSERVATION subtable first` |
| `history_entries > 0` | Note | 0 history in a claimed-calibrated MS: flag suspect |

Note total duration:
- < 1 hr → likely snapshot or calibrator-only
- 4–12 hr → standard science track
- > 12 hr → concatenated; check for non-contiguous time ranges

### 1.2 `ms_field_list`

| Check | Pass | Action |
|-------|------|--------|
| At least 1 flux cal + 1 phase cal + 1 target | All present | Missing role → flag in summary |
| `heuristic_intents == false` | Intents explicit | true → verify inferred roles against scan time fractions |
| Any `resolved_source == true` | None | Use component model; see `00-core.md` resolved calibrator table |
| Any `ra_j2000_deg.flag == "SUSPECT"` | None | Elevation + PA UNAVAILABLE for that field |
| Multiple fields with same `source_id` | None | Mosaic — flag; imaging strategy differs |

Typical phase cal separation: ≤ 15° from science target (VLA). Beyond 15°: note in summary.

### 1.3 `ms_scan_list`

| Check | Action |
|-------|--------|
| Standard pattern: flux_cal → (phase_cal → target × N) → flux_cal | Note if bookend flux cal missing |
| `integration_s`: VLA 1–10 s; MeerKAT/uGMRT 2–8 s | > 60 s on phase cal → decorrelation risk at long baselines |
| Scan number gaps | Note explicitly; do not ignore |

### 1.4 `ms_scan_intent_summary`

Expected time fractions:
- Flux/bandpass cal: 5–15%
- Phase cal: 10–20%
- Science target: 65–80%

| Deviation | Action |
|-----------|--------|
| Flux cal > 30% | Flag — likely calibrator-only or test obs |
| Target < 40% | Note poor sensitivity |
| `intent_completeness == "UNAVAILABLE"` | Cross-check against `ms_field_list` roles |

### 1.5 `ms_spectral_window_list`

| Check | Action |
|-------|--------|
| Band identification matches science goal | Note mismatch |
| Single-channel SpWs (`n_channels == 1`) | Per-channel bandpass impossible — note |
| VLA wideband: 16–64 SpWs of 64 MHz each | Note if fewer |
| MeerKAT: 1 SpW of 4096 channels | Expected |

### 1.6 `ms_correlator_config`

| Check | Action |
|-------|--------|
| `full_stokes == false` | Full polarimetric imaging impossible; Stokes I and V may still be feasible |
| `polarization_basis`: circular (VLA) or linear (MeerKAT, uGMRT) | Expected per telescope |
| `dump_time_s` matches `integration_s` from scan list | Mismatch: flag |

## Phase 1 failure routing

| Symptom | next_stage |
|---------|-----------|
| `telescope_name` blank | `stop — repair metadata` |
| Only 1 field | `stop — confirm this is a science dataset` |
| All `intents == []` AND no catalogue match | Note; proceed with field-name-only guidance |
| `n_spw == 1` AND `n_channels == 1` | Note; no per-channel bandpass |
