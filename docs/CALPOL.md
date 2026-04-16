# CALPOL — Polarization Calibration: Design Document

## Context

When a user asks "help me calibrate polarization", we need to:
1. Gate on feasibility before doing any work
2. Drive a VLA-specific workflow (MeerKAT is a separate design, later)

This document covers the data layer and the feasibility MCP tool. The
calibration skills (`calibrate` and `calibrate-pol-vla`) are a separate design
session — they require careful treatment of solution interval optimisation,
pre-cal flagging strategy, and CASA task orchestration.

---

## Architecture

```
util/pol_calibrators.py          ← bundled static VLA pol reference data (committed)
tools/pol_cal_feasibility.py     ← MCP tool: ms_pol_cal_feasibility
server.py                        ← register the new tool
tests/unit/test_pol_calibrators.py  ← unit tests (no CASA required)
tests/integration/test_tools.py  ← integration stub (@_SKIP)
```

Skills are out of scope for this implementation slice. See open questions below.

---

## Step 1: `util/pol_calibrators.py`

Pure Python. No CASA. Mirrors the structure of `util/calibrators.py`.
Committed static data — no live web fetch. This is the determinism guarantee.

**Source:** NRAO VLA Observing Guide Tables 8.2.1–8.2.7 and evlapolcal/index.html
(scraped March 2026, committed verbatim).

### Data model

```python
@dataclass
class PolFreqEntry:
    freq_ghz: float
    frac_pol_pct: float | None       # None = upper limit only
    frac_pol_upper_limit: bool       # True when frac_pol_pct is an upper bound
    pol_angle_deg: float | None      # None = unmeasurable / unstable at this freq

@dataclass
class PolCalEntry:
    j2000_name: str                  # "J1331+3030"
    b1950_name: str                  # "3C286"
    category: str                    # "A", "B", "C", "D"
    role: list[str]                  # ["angle"], ["leakage"], ["angle", "leakage"]
    stable_pa: bool                  # True = PA reliable across bands (only 3C286)
    variability_note: str | None     # e.g. "in flare Jan 2025 at K/Ka/Q"
    epochs: dict[str, list[PolFreqEntry]]   # {"2010": [...], "2019": [...]}
    aka: list[str]                   # alternative names for field matching
```

### Key sources encoded

| Source | J2000 | Category | Role | Notes |
|--------|-------|----------|------|-------|
| 3C286 | J1331+3030 | A | angle + leakage | PA stable ~33° from 1–48 GHz. Gold standard. |
| 3C138 | J0521+1638 | A | angle | PA varies by band. In flare Jan 2025 at K/Ka/Q. |
| 3C48  | J0137+3309 | A | angle (>4 GHz only) | PA rotates wildly <4 GHz. Encode explicitly. |
| 3C147 | J0542+4951 | C | leakage | <0.05% pol below 10 GHz. Rises ~5% above 10 GHz. |
| 3C84  | J0319+4130 | C | leakage | Low pol, bright, monitored. |
| NRAO150 | J0359+5057 | B | angle (secondary) | Variable. Monitored on request. |
| BL Lac | J2202+4216 | B | angle (secondary) | Variable. Monitored on request. |
| 3C454.3 | J2253+1608 | B | angle (secondary) | Variable. Monitored on request. |

### Public API

```python
def lookup_pol(field_name: str) -> PolCalEntry | None
def is_angle_calibrator(field_name: str) -> bool
def is_leakage_calibrator(field_name: str) -> bool
def pol_properties_at_freq(
    entry: PolCalEntry,
    freq_ghz: float,
    epoch: str = "2019",
) -> PolFreqEntry | None          # interpolates between tabulated frequencies
```

---

## Step 2: `tools/pol_cal_feasibility.py` — `ms_pol_cal_feasibility`

**One question:** Is full VLA polarization calibration possible for this dataset?

**Inputs:** `ms_path: str`, `pa_spread_threshold_deg: float = 60.0`

### What the tool does (no tool chaining)

1. Opens `FIELD` subtable → field names + phase centres
2. Opens `SPECTRAL_WINDOW` subtable → band centre frequency (median of first SPW)
3. Matches each field against `util/pol_calibrators.py` (pure Python, fast)
4. For each matching pol cal field: reads timestamps from MAIN table for those
   FIELD_ID rows, computes PA spread using astropy (telescope position from
   `ANTENNA` subtable → observer location, then LST → hour angle → PA per
   timestamp). Returns only Δ (max − min), not the full time series.
5. Interpolates pol properties at the observed frequency from the 2019 epoch table
6. Emits structured verdict

### PA convention note (encoded in the output)

Astropy computes sky-frame PA (North through East). CASA uses feed-frame PA
(= sky PA − 90° for ALT-AZ mounts). The absolute values differ, but
Δ(PA) is identical in both conventions. The tool reports the delta only and
annotates this in `pa_spread_note`.

### Output structure

```json
{
  "tool": "ms_pol_cal_feasibility",
  "ms_path": "...",
  "status": "ok",
  "completeness_summary": "COMPLETE",
  "data": {
    "band_centre_ghz": { "value": 1.45, "flag": "COMPLETE" },
    "pol_angle_calibrator": {
      "available": true,
      "source": "3C286",
      "category": "A",
      "frac_pol_pct": { "value": 9.8, "flag": "COMPLETE" },
      "pol_angle_deg": { "value": 33.0, "flag": "COMPLETE" },
      "stable_pa": true,
      "variability_warning": null
    },
    "leakage_calibrator": {
      "available": true,
      "source": "3C286",
      "pa_spread_deg": { "value": 74.3, "flag": "COMPLETE" },
      "pa_spread_note": "Delta computed via astropy sky-frame PA; CASA feed-frame differs by -90deg but delta is identical",
      "n_calibrator_scans": 4,
      "meets_threshold": true,
      "threshold_deg": 60.0
    },
    "verdict": "FULL",
    "blocker": null,
    "pol_cal_data_epoch": "2019",
    "pol_cal_data_source": "NRAO VLA Observing Guide Table 8.2.7 + evlapolcal/index.html"
  },
  "warnings": [],
  "provenance": { "casa_calls": ["..."], "casatools_version": "..." }
}
```

### Verdict logic

| Verdict | Condition |
|---------|-----------|
| `FULL` | Angle cal available AND leakage cal meets PA threshold |
| `LEAKAGE_ONLY` | No angle cal, but leakage cal meets threshold OR low-pol source present |
| `DEGRADED` | Angle cal present but flagged as variable/flaring; proceed with warning |
| `NOT_FEASIBLE` | No pol cal sources found, or PA spread < threshold with no low-pol alternative |

---

## Step 3: Register in `server.py`

Add `@mcp.tool(name="ms_pol_cal_feasibility")` — same pattern as all other tools.

---

## Step 4: Tests

**Unit (`tests/unit/test_pol_calibrators.py`):**
- `lookup_pol("3C286")` returns Category A entry with stable_pa=True
- `lookup_pol("J1331+3030")` returns same entry (alias matching)
- `lookup_pol("unknown")` returns None
- `pol_properties_at_freq(entry, 1.45)` interpolates correctly between table rows
- `is_angle_calibrator("3C147")` returns False
- `is_leakage_calibrator("3C147")` returns True
- 3C48 below 4 GHz: `frac_pol_pct` is low, `pol_angle_deg` is None or unstable

**Integration stub (`tests/integration/test_tools.py`):**
- `@_SKIP` decorated stub for `ms_pol_cal_feasibility` — same pattern as others

---

## Open questions (deferred to tomorrow — calibrate skill design)

- How to compute optimal `solint` from scan length, source flux, and expected SNR?
- Pre-cal flagging strategy: tfcrop vs rflag, recommended thresholds per band?
- Should `calibrate` skill drive CASA tasks directly or generate a calibration script?
- Quack interval: is 2s always right, or does it depend on correlator mode?
- How does the skill handle missing bandpass calibrator (uses phase cal as BP)?

---

## Out of scope for this slice

- `calibrate` skill (standard cal workflow)
- `calibrate-pol-vla` skill (uses `ms_pol_cal_feasibility` as feasibility gate)
- `calibrate-pol-meerkat` skill (different feeds, different reference sources)
- Auto-refresh / scraping tooling for pol cal data
- Circular polarization / Stokes V
