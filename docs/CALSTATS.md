# CALSTATS.md — ms_calsol_stats Design Document

## Purpose

`ms_calsol_stats` is a read-only ms_inspect tool that inspects a CASA
calibration table and returns structured numerical diagnostics sufficient for
the skill to make go/no-go decisions on the CALIBRATION_SOLVE stage.

This is the autonomous feedback tool: after each solve job, the skill calls
`ms_calsol_stats`, reads the diagnostic arrays, and decides whether to advance
to CALIBRATION_APPLY or loop back to CALIBRATION_PREFLAG.

---

## Table types and what they contain

CASA caltable type is read from `tb.getkeyword("VisCal")` (e.g. `"B Jones"`,
`"G Jones"`, `"K Jones"`). Strip `" Jones"` suffix → `table_type` field.

| Type | Physical meaning | Solution shape per row | Column |
|---|---|---|---|
| `G` | Complex gain (phase + amplitude) as function of time | `[n_corr, 1]` per (ant, SPW, time) | `CPARAM` |
| `B` | Bandpass — complex gain as function of frequency | `[n_corr, n_chan]` per (ant, SPW) | `CPARAM` |
| `K` | Group delay — scalar delay in nanoseconds | `[n_corr, 1]` per (ant, SPW) | `FPARAM` |

Other table types (D, X, Kcross) exist but are out of scope for this tool.

---

## Memory strategy — tb.query() per SPW

Never load the entire `CPARAM`/`FPARAM` column at once. A wideband dense-time
G table (27 antennas × 1000 integrations × 16 SPWs) loaded in one `getcol()`
call would be ~27 million complex values — several hundred MB.

**Protocol:**

```python
spw_ids = sorted(set(tb.getcol("SPECTRAL_WINDOW_ID")))
for spw in spw_ids:
    sub = tb.query(f"SPECTRAL_WINDOW_ID == {spw}")
    try:
        param = sub.getcol("CPARAM")   # or FPARAM for K
        flag  = sub.getcol("FLAG")
        snr   = sub.getcol("SNR")
        ant1  = sub.getcol("ANTENNA1")
        # pivot into dense array, accumulate stats
    finally:
        sub.close()
```

Each SPW subtable holds at most `n_ant × n_time` rows. For VLA (~27 antennas,
~1000 integrations): ~27k rows per SPW, ~200KB complex64. Safe.

---

## Output shape — open questions (to resolve before implementation)

### Q1: Channel axis for B tables

Option A — **Summary stats only** (`amp_mean`, `amp_std` per antenna per SPW):
- `amp_mean[n_ant, n_spw]`, `amp_std[n_ant, n_spw]`
- Compact, skill-friendly for go/no-go decisions
- Insufficient for "is the bandpass shape weird?"

Option B — **Full channel array**:
- `amp[n_ant, n_spw, n_chan_max]` — padded to max channel count across SPWs
- VLA L-band: 27×16×64×2 corr ≈ 440KB as JSON nested lists — fits
- Enables shape diagnostics and future plot-grade tools
- Requires NaN padding when SPWs have different channel counts

**Current preference: TBD.** The heuristics (compression, outliers) only need
summary stats, but the full vector enables richer future diagnostics.

### Q2: Antenna axis indexing

Option A — **64-slot dense by antenna ID**:
- Array shape always `(64, ...)`, indexed by ANTENNA_ID from caltable ANTENNA subtable
- Absent antennas are NaN slices
- Cross-table comparison trivial (G and B from same observation share ID space)
- Wastes space for small arrays (uGMRT has 30 antennas)

Option B — **n_present dense with name lookup**:
- Array shape `(n_ant_present, ...)`, companion `ant_names[i]` maps index → name
- More compact; still allows cross-table comparison via name matching
- Slightly more work for the skill when correlating across tables

**Current preference: TBD.**

### Q3: Phase diagnostic — mean or RMS scatter?

For G tables: mean phase per antenna/SPW is ~0 by construction (refant is
the phase reference). **Phase RMS scatter** is the meaningful diagnostic for
ionospheric stability and solution quality.

For B tables: mean phase per channel captures the bandpass phase shape; RMS
scatter per antenna/SPW captures solution noise.

**Current preference: return both**, mean and RMS, let the skill pick.

---

## Proposed output fields (draft)

### Always present

| Field | Shape | Notes |
|---|---|---|
| `table_type` | scalar str | `"G"`, `"B"`, `"K"`, or `UNAVAILABLE` |
| `n_antennas` | int | number of antennas present in the table |
| `n_spw` | int | number of unique SPWs |
| `ant_names` | list[str] len n_ant | antenna name → index mapping |
| `spw_ids` | list[int] len n_spw | SPW ID → index mapping |
| `flagged_frac` | `[n_ant, n_spw]` | fraction of flagged solutions per antenna/SPW |
| `snr_mean` | `[n_ant, n_spw]` | mean SNR over unflagged solutions |
| `overall_flagged_frac` | scalar float | weighted across all antennas and SPWs |
| `n_antennas_lost` | scalar int | antennas with `flagged_frac == 1.0` across all SPWs |
| `antennas_lost` | list[str] | names of lost antennas |

### G and B tables (CPARAM — complex solutions)

| Field | Shape | Notes |
|---|---|---|
| `amp_mean` | `[n_ant, n_spw]` | mean amplitude over unflagged solutions |
| `amp_std` | `[n_ant, n_spw]` | std of amplitude — outlier and compression diagnostic |
| `phase_mean_deg` | `[n_ant, n_spw]` | mean phase (reference: refant = 0) |
| `phase_rms_deg` | `[n_ant, n_spw]` | RMS phase scatter — ionosphere/solution stability |
| `amp_array` | `[n_ant, n_spw, n_chan_or_time]` | full array (TBD on B channel axis) |

### K tables (FPARAM — real delays)

| Field | Shape | Notes |
|---|---|---|
| `delay_ns` | `[n_ant, n_spw, n_corr]` | delay value in nanoseconds |

---

## Heuristics — computed in the tool vs deferred to the skill

The tool contract (CLAUDE.md) says: **numbers, not narratives**. No
interpretation. However, some array-level computations are mechanical enough
to belong in the tool (they are measurements, not decisions):

| Diagnostic | Where it lives | Rationale |
|---|---|---|
| `overall_flagged_frac` | Tool | Pure aggregation of FLAG column |
| `n_antennas_lost` | Tool | Count of flagged_frac == 1.0 |
| `amp_outlier_antennas` | **Skill** | Requires threshold judgement (5σ vs 3σ?) |
| Gain compression diagnosis | **Skill** | Requires domain knowledge (what is "too low"?) |
| Phase stability assessment | **Skill** | Requires knowledge of ionospheric conditions |
| Go/no-go on bp_flagged_frac | **Skill** | Thresholds in 07-calibration-execution.md |

---

## Input parameters

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `caltable_path` | str | required | Path to caltable directory |
| `max_antennas` | int | 64 | Truncate antenna axis if array is larger |

No `ms_path` — a caltable is not an MS. `caltable_path` is used as the path
field in the response envelope.

---

## Registration

- Module: `src/ms_inspect/tools/calsol_stats.py`
- Server: `src/ms_inspect/server.py` with `readOnlyHint: True`
- Tool name: `ms_calsol_stats`

---

## Open questions (parking lot)

1. B table channel axis: summary stats only, or full channel array?
2. Antenna axis: 64-slot dense by ID, or n_present with name lookup?
3. How to handle multi-time B solutions (unusual but legal in CASA)?
4. Should `snr_mean` use unflagged solutions only, or include flagged SNR where FLAG=True but SNR > 0?
5. For K tables: delay RMS across SPWs is a useful diagnostic — include it?

---

## Out of scope

- D-term, Kcross, X tables — future polcal slice
- Plotting or visualisation — these are skill outputs, not tool outputs
- Writing to the caltable — this is ms_inspect (read-only)
