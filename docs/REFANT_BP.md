# REFANT_BP — Reference Antenna Selection + Initial Bandpass: Design Document

## Context

This document covers two tools that together enable pre-calibration RFI flagging
on the calibrator-only MS:

1. `ms_refant` — selects the best reference antenna from the MS using geometry
   and flagging heuristics (read-only, ms_inspect).
2. `ms_initial_bandpass` — solves a coarse bandpass on the calibrator MS to
   produce a CORRECTED column for rflag (write, ms_modify).

These tools slot into the pre-cal flagging workflow after `ms_split_field` and
before `ms_rfi_channel_stats` + `ms_apply_flags`:

```
ms_split_field(field=bp_field)         → cal_only.ms         [ms_inspect]
ms_flag_summary(cal_only.ms)           → baseline state       [ms_inspect]
ms_refant(cal_only.ms, field=bp_field) → ranked refant list   [ms_inspect]  ← NEW
ms_initial_bandpass(cal_only.ms, ...)  → init_gain.g, BP0.b   [ms_modify]   ← NEW
  (CORRECTED column now populated)
ms_rfi_channel_stats(cal_only.ms)      → bad channels         [ms_inspect]
ms_apply_flags(cal_only.ms, cmds)      → flags applied        [ms_inspect]
ms_flag_summary(cal_only.ms)           → after state + delta  [ms_inspect]
```

Source: algorithm adapted from `evla_pipe.utils.RefAntHeuristics` /
`RefAntGeometry` / `RefAntFlagging` in the evla_scripted_pipeline repository.

---

## Architecture

```
src/
├── ms_inspect/
│   ├── server.py                     ← register ms_refant
│   └── tools/
│       └── refant.py                 ← ms_refant (read-only)
└── ms_modify/
    ├── server.py                     ← register ms_initial_bandpass
    ├── exceptions.py                 ← add InitialBandpassFailedError
    └── tools/
        └── initial_bandpass.py       ← ms_initial_bandpass
tests/
├── unit/
│   └── test_refant.py                ← pure-Python geometry score tests
└── integration/
    └── test_tools.py                 ← @_SKIP stubs for both tools
```

---

## Step 1: `tools/refant.py` — `ms_refant`

**One question:** Which antenna should be used as the phase reference for this MS?

**Inputs:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `ms_path` | str | required | Path to Measurement Set (usually cal_only.ms) |
| `field` | str | `""` | Field selection for flagging heuristic (e.g. bandpass field name). Empty = all fields |
| `use_geometry` | bool | `True` | Score by distance from array centre |
| `use_flagging` | bool | `True` | Score by unflagged data fraction |

### Algorithm

Two independent scores, computed in pure Python / numpy + one CASA call.
Scores are normalised to [0, n_antennas] so they contribute equally regardless
of array size. The combined score is their sum; higher = better refant.

**Geometry score** (no CASA required after ANTENNA table read):

1. Read `POSITION` (ECEF XYZ, metres) and `NAME` from `ANTENNA` subtable via
   `open_table`.
2. Array centre = component-wise median of all non-flagged antenna positions.
3. Distance from centre = Euclidean 3D norm (valid for arrays ≤ ~100 km).
4. `geo_score[ant] = (1 − distance / max_distance) × n_antennas`
   Antennas closest to centre score highest.

**Flagging score** (one `flagdata(mode='summary')` call):

1. Call `casatasks.flagdata(vis=ms_path, mode='summary', field=field,
   flagbackup=False, savepars=False)`.
2. Per-antenna: `good[ant] = total − flagged` from the `antenna` sub-dict.
3. `flag_score[ant] = (good[ant] / max_good) × n_antennas`
   Antennas with most unflagged data score highest.

**Combined:**

```
score[ant] = geo_score[ant] + flag_score[ant]   (if both enabled)
```

Sort descending. Return full ranked list (not just top-1) so the skill can
fall back to refant[1] if refant[0] is later found to be bad.

### Output structure

```json
{
  "tool": "ms_refant",
  "ms_path": "...",
  "status": "ok",
  "completeness_summary": "COMPLETE",
  "data": {
    "refant":        { "value": "ea05", "flag": "COMPLETE" },
    "refant_list":   { "value": ["ea05", "ea11", "ea23", ...], "flag": "COMPLETE" },
    "n_antennas":    27,
    "use_geometry":  true,
    "use_flagging":  true,
    "field_selection": "3C147",
    "ranked": [
      {
        "antenna": "ea05",
        "geo_score":      24.1,
        "flag_score":     26.8,
        "combined_score": 50.9,
        "rank": 1
      },
      ...
    ]
  },
  "warnings": [],
  "provenance": {
    "casa_calls": [
      "tb.open('ANTENNA') → getcol(POSITION, NAME, FLAG_ROW)",
      "casatasks.flagdata(vis=..., mode='summary', field='3C147')"
    ],
    "casatools_version": "6.7.3.21"
  }
}
```

If `use_flagging=False`, geometry score only; `flag_score` is 0 for all antennas
and provenance omits the flagdata call. `refant` field carries `INFERRED` flag
when only one heuristic is used.

### Completeness flags

| Condition | Flag |
|-----------|------|
| Both heuristics used, all antennas present | `COMPLETE` |
| Only one heuristic used | `INFERRED` |
| flagdata summary failed (fallback to geometry-only) | `PARTIAL` + warning |
| ANTENNA table has FLAG_ROW set for some antennas | `PARTIAL` + warning listing excluded antennas |

---

## Step 2: `ms_modify/tools/initial_bandpass.py` — `ms_initial_bandpass`

**One question:** Given a calibrator MS, a reference antenna, and a work
directory, produce the initial coarse bandpass calibration tables and populate
the CORRECTED column.

**Inputs:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `ms_path` | str | required | Path to cal_only.ms |
| `bp_field` | str | required | CASA field selection string for bandpass calibrator |
| `bp_scan` | str | `""` | CASA scan selection string (empty = all scans) |
| `all_spw` | str | `""` | CASA SpW selection string (empty = all SpWs) |
| `ref_ant` | str | required | Reference antenna name (from ms_refant output) |
| `workdir` | str | required | Directory to write caltables into (must exist) |
| `priorcals` | list[str] | `[]` | Prior calibration tables to pre-apply (e.g. requantiser, Tsys) |
| `min_bl_per_ant` | int | `4` | `minblperant` for gaincal and bandpass |
| `uvrange` | str | `""` | UV range restriction (set for 3C84 to exclude extended emission) |

### CASA call sequence

Adapted directly from `evla_pipe/stages/initial_bp.py`. No changes to logic.

```
Step 1 — gaincal (phase, per-integration)
  gaincal(vis=ms_path, caltable=<workdir>/init_gain.g,
          field=bp_field, spw=all_spw, scan=bp_scan,
          solint="int", refant=ref_ant,
          minblperant=min_bl_per_ant, minsnr=3.0,
          gaintype="G", calmode="p",
          gaintable=priorcals)
  → hard fail if init_gain.g not produced

Step 2 — bandpass (solint=inf, all data)
  bandpass(vis=ms_path, caltable=<workdir>/BP0.b,
           field=bp_field, spw=all_spw, scan=bp_scan,
           solint="inf", combine="scan",
           refant=ref_ant,
           minblperant=min_bl_per_ant, minsnr=3.0,
           bandtype="B", fillgaps=62,
           gaintable=priorcals + [init_gain.g])
  → hard fail if BP0.b not produced

Step 3 — applycal (all fields)
  applycal(vis=ms_path, field="", spw=all_spw,
           gaintable=priorcals + [init_gain.g, BP0.b],
           calwt=[False] * n_tables,
           flagbackup=False)
  → CORRECTED column now populated; rflag can proceed
```

`fillgaps=62` matches the evla_pipe default — fills flagged channels up to 62
channels wide by interpolation so that rflag sees a smooth continuum rather
than holes.

### Output structure

```json
{
  "tool": "ms_initial_bandpass",
  "ms_path": "...",
  "status": "ok",
  "completeness_summary": "COMPLETE",
  "data": {
    "init_gain_table":  { "value": "/work/init_gain.g", "flag": "COMPLETE" },
    "bp_table":         { "value": "/work/BP0.b",       "flag": "COMPLETE" },
    "corrected_written": { "value": true,               "flag": "COMPLETE" },
    "n_prior_tables":   0,
    "ref_ant":          "ea05",
    "bp_field":         "3C147",
    "solint_phase":     "int",
    "solint_bp":        "inf",
    "fillgaps":         62
  },
  "warnings": [],
  "provenance": {
    "casa_calls": [
      "casatasks.gaincal(..., solint='int', calmode='p') → init_gain.g",
      "casatasks.bandpass(..., solint='inf', combine='scan') → BP0.b",
      "casatasks.applycal(...)"
    ],
    "casatools_version": "6.7.3.21"
  }
}
```

### Error conditions

| Condition | Error type |
|-----------|-----------|
| `ms_path` not found | `MS_NOT_FOUND` |
| `workdir` does not exist | `COMPUTATION_ERROR` |
| gaincal did not produce `init_gain.g` | `InitialBandpassFailedError` |
| bandpass did not produce `BP0.b` | `InitialBandpassFailedError` |
| Any `priorcals` path does not exist | `COMPUTATION_ERROR` |

`InitialBandpassFailedError` is added to `ms_modify/exceptions.py`.
Message always includes the CASA command attempted.

No `dry_run` — this tool has no meaningful dry-run mode. The output is new
files on a derived MS; the original MS is never touched.

---

## Step 3: Register in `server.py` and `ms_modify/server.py`

- `ms_inspect/server.py`: add `refant` to imports; add `RefAntInput` model;
  register `@mcp.tool(name="ms_refant")` with `readOnlyHint: True`.
- `ms_modify/server.py`: add `initial_bandpass` import; add
  `InitialBandpassInput` model; register `@mcp.tool(name="ms_initial_bandpass")`
  with `readOnlyHint: False, destructiveHint: False, idempotentHint: False`.

---

## Step 4: Tests

**Unit (`tests/unit/test_refant.py`):**
- `_geo_score()` on a synthetic 3-antenna array returns highest score for the
  centre antenna
- `_geo_score()` with all equidistant antennas returns equal scores
- `_flag_score()` with a known good-data dict returns correct normalised scores
- Combined score ranks correctly when geometry and flagging agree
- Combined score ranks correctly when geometry and flagging disagree (flagging
  should tip the balance for a heavily flagged central antenna)

**Integration stubs (`tests/integration/test_tools.py`):**
- `@_SKIP` stub `TestRefAntReal` — calls `ms_refant` on test MS, asserts
  `status == "ok"` and `refant_list` is non-empty
- `@_SKIP` stub `TestInitialBandpassReal` — calls `ms_initial_bandpass`,
  asserts both caltables exist on disk

---

## Open questions (resolved for implementation)

- `fillgaps=62`: matches evla_pipe default. Not exposed as a parameter — it is
  an internal implementation detail for the initial BP only.
- `minsnr=3.0`: fixed, same as evla_pipe.
- `solnorm=False`: fixed — we do not normalise; absolute amplitudes matter for
  the subsequent rflag pass.
- 3C84 uvrange: passed in as `uvrange` parameter; caller (skill) is responsible
  for setting it based on `ms_pol_cal_feasibility` output or field name match.
  Tool does not auto-detect 3C84.

---

## Out of scope for this slice

- Delay calibration (`gaincal(gaintype="K")`) — applied in semiFinalBP after
  initial flagging; not needed here
- `semiFinalBPdcals` — the clean-data bandpass; separate design session
- Full gain calibration (phase + amplitude) — separate design session
- solint2 computation — `ms_initial_bandpass` always uses `solint="inf"`;
  solint2 is only needed for subsequent gaincal passes
