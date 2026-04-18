# CLAUDE.md — ms-inspect / radio_ms_mcp

Project-level context for Claude Code and Claude Desktop.
Checked into the repository root — applies to every contributor.

---

## What this project is

`ms-inspect` is a **Model Context Protocol (MCP) server** that exposes a suite
of read-only inspection tools over CASA Measurement Sets (MS). It is the data
layer for an AI-assisted radio interferometric reduction pipeline targeting
VLA/JVLA/EVLA, MeerKAT, and uGMRT.

**Phase 1 scope (this codebase):** Layer 1 (Orientation) and Layer 2
(Instrument Sanity) — 12 tools total. Layers 3–5 are out of scope.

The design document is at `DESIGN.md` in this directory. Read it before making
any non-trivial change.

---

## Core contract — do not violate this

> **Tools measure. The Skill reasons.**

Every tool in `src/ms_inspect/tools/` must obey three rules:

1. **One question, one answer.** A tool returns numbers and completeness flags.
   It never interprets, never suggests a next step, never chains to another tool.
2. **Numbers, not narratives.** All returned text is structured data or a
   provenance annotation. Prose interpretation belongs in `skill/SKILL.md`.
3. **Explicit uncertainty.** Every field that could not be retrieved carries a
   `CompletionFlag` (`COMPLETE`, `INFERRED`, `PARTIAL`, `SUSPECT`, `UNAVAILABLE`).
   Silence is never used to indicate failure.

Violating the contract — adding interpretation, collapsing flags, adding
tool-chaining logic — will break the Skill's reasoning model and produce silent
scientific errors. If you are unsure whether something belongs in a tool or in
the Skill, it belongs in the Skill.

---

## Repository layout

```
ms-inspect/
├── CLAUDE.md                      ← this file
├── DESIGN.md                      ← architecture, failure modes, conventions
├── pixi.toml                      ← environment (conda-forge + casatools via PyPI)
├── pyproject.toml                 ← build metadata and tooling config
├── README.md
├── bin/
│   ├── serve.sh                   ← MCP plugin entry point (ms-inspect)
│   ├── serve-modify.sh            ← MCP plugin entry point (ms-modify)
│   └── serve-create.sh            ← MCP plugin entry point (ms-create)
├── src/
│   ├── ms_create/
│   │   ├── __init__.py            ← version string
│   │   ├── server.py              ← FastMCP entry point (ingestion utilities, port 8002)
│   │   ├── exceptions.py          ← ASDMNotFoundError, ImportFailedError
│   │   └── import_asdm.py         ← ms_import_asdm tool
│   ├── ms_modify/
│   │   ├── __init__.py            ← version string
│   │   ├── server.py              ← FastMCP entry point (write utilities, port 8001)
│   │   ├── exceptions.py          ← ms_modify error types
│   │   ├── intents.py             ← set_intents utility function
│   │   ├── preflag.py             ← ms_apply_preflag
│   │   ├── priorcals.py           ← ms_generate_priorcals
│   │   ├── setjy.py               ← ms_setjy
│   │   ├── setjy_polcal.py        ← ms_setjy_polcal
│   │   ├── initial_bandpass.py    ← ms_initial_bandpass
│   │   ├── initial_rflag.py       ← ms_apply_initial_rflag
│   │   ├── rflag.py               ← ms_apply_rflag
│   │   ├── gaincal.py             ← ms_gaincal
│   │   ├── bandpass.py            ← ms_bandpass
│   │   ├── fluxscale.py           ← ms_fluxscale
│   │   ├── applycal.py            ← ms_applycal
│   │   └── slurm.py               ← SLURM batch submission utility (not an MCP tool)
│   └── ms_inspect/
│       ├── __init__.py            ← version string
│       ├── server.py              ← FastMCP entry point (read-only, port 8000)
│       ├── exceptions.py          ← centralised error taxonomy
│       ├── tools/
│       │   ├── observation.py     ← ms_observation_info
│       │   ├── fields.py          ← ms_field_list
│       │   ├── scans.py           ← ms_scan_list, ms_scan_intent_summary
│       │   ├── spectral.py        ← ms_spectral_window_list, ms_correlator_config
│       │   ├── antennas.py        ← ms_antenna_list, ms_baseline_lengths
│       │   ├── geometry.py        ← ms_elevation_vs_time, ms_parallactic_angle_vs_time
│       │   ├── shadowing.py       ← ms_shadowing_report
│       │   ├── flags.py           ← ms_antenna_flag_fraction
│       │   ├── flag_summary.py    ← ms_flag_summary
│       │   ├── online_flags.py    ← ms_online_flag_stats
│       │   ├── verify_import.py   ← ms_verify_import
│       │   ├── priorcals_check.py ← ms_verify_priorcals
│       │   ├── caltables.py       ← ms_verify_caltables
│       │   ├── calsol_stats.py    ← ms_calsol_stats
│       │   ├── calsol_plot.py     ← ms_calsol_plot
│       │   ├── refant.py          ← ms_refant
│       │   ├── residual_stats.py  ← ms_residual_stats
│       │   ├── rfi.py             ← ms_rfi_channel_stats
│       │   └── pol_cal_feasibility.py ← ms_pol_cal_feasibility
│       └── util/
│           ├── casa_context.py    ← context managers: open_msmd, open_table, open_ms
│           ├── calibrators.py     ← bundled calibrator catalogue + resolved-source logic
│           ├── conversions.py     ← MJD→UTC, Hz→GHz, ECEF→geodetic, corr codes, etc.
│           └── formatting.py      ← response envelope, CompletionFlag, round_dict
├── tests/
│   ├── unit/                      ← no CASA required, runs everywhere
│   │   ├── test_conversions.py
│   │   ├── test_calibrators.py
│   │   ├── test_formatting.py
│   │   ├── test_set_intents.py
│   │   ├── test_import_asdm.py
│   │   └── test_verify_import.py
│   └── integration/               ← requires casatools; auto-uses 3C391 tarball if present
│       ├── conftest.py            ← 3C391 tarball extraction fixture
│       ├── test_tools.py
│       └── test_set_intents.py
└── skill/
    └── SKILL.md                   ← interferometrist reasoning document (separate)
```

---

## Environment setup

**Requires pixi.** Install from https://prefix.dev if not present.

```bash
# Install environment (conda-forge + casatools via pip)
pixi install

# Start the MCP server (stdio transport — for Claude Desktop)
pixi run serve

# Start the MCP server (HTTP transport — for HPC / remote)
pixi run serve-http

# Start the ms-modify server (stdio / HTTP)
pixi run serve-modify
pixi run serve-modify-http

# Start the ms-create server (stdio / HTTP)
pixi run serve-create
pixi run serve-create-http

# Run unit tests (no CASA, no MS required)
pixi run test-unit

# Run integration tests — auto-uses 3C391 tarball if present, or set manually:
# RADIO_MCP_TEST_MS_TGZ=/path/to/3c391.ms.tgz pixi run test-int
# RADIO_MCP_TEST_MS=/path/to/your.ms pixi run test-int

# Lint + format check (CI gate)
pixi run check
```

Python version: `>=3.12` (casatools 6.7.x ships `cp312` and `cp313` wheels).
`casatools` and `casatasks` are PyPI-only — pixi resolves them via pip into the
conda environment. Do not add them to `[dependencies]`; they live in
`[pypi-dependencies]` in `pixi.toml`.

Environment variable reference:

| Variable | Default | Effect |
|----------|---------|--------|
| `RADIO_MCP_TRANSPORT` | `stdio` | `stdio` for Claude Desktop; `http` for remote |
| `RADIO_MCP_PORT` | `8000` | HTTP port (ms-inspect); ms-modify uses 8001, ms-create uses 8002 |
| `RADIO_MCP_WORKERS` | `4` | Parallel worker count for FLAG column reads (cap 8) |
| `RADIO_MCP_TEST_MS` | — | Path to pre-extracted MS for integration tests |
| `RADIO_MCP_TEST_MS_TGZ` | — | Path to `.ms.tgz` tarball; auto-extracted by conftest.py |

---

## Tool inventory (Phase 1)

### Layer 1 — Orientation (6 tools)

| Tool | Module | Primary CASA call |
|------|--------|-------------------|
| `ms_observation_info` | `tools/observation.py` | `tb → OBSERVATION` |
| `ms_field_list` | `tools/fields.py` | `msmd.fieldnames()`, `msmd.phasecenter()`, `msmd.intentsforfield()` |
| `ms_scan_list` | `tools/scans.py` | `msmd.timesforscans()`, `msmd.intentsforscans()` |
| `ms_scan_intent_summary` | `tools/scans.py` | aggregated from scan list |
| `ms_spectral_window_list` | `tools/spectral.py` | `msmd.chanfreqs()`, `msmd.chanwidths()`, `tb → POLARIZATION` |
| `ms_correlator_config` | `tools/spectral.py` | `tb → POLARIZATION`, `msmd.exposuretime()` |

### Layer 2 — Instrument Sanity (6 tools)

| Tool | Module | Primary CASA call |
|------|--------|-------------------|
| `ms_antenna_list` | `tools/antennas.py` | `tb → ANTENNA` |
| `ms_baseline_lengths` | `tools/antennas.py` | computed from ECEF positions |
| `ms_elevation_vs_time` | `tools/geometry.py` | astropy AltAz (not CASA measures) |
| `ms_parallactic_angle_vs_time` | `tools/geometry.py` | astropy LST + atan2 |
| `ms_shadowing_report` | `tools/shadowing.py` | `msmd.shadowedAntennas()` |
| `ms_antenna_flag_fraction` | `tools/flags.py` | `tb.getcolslice(FLAG)` parallel reads |

### Calibration inspection (2 tools)

| Tool | Module | What it does |
|------|--------|-------------|
| `ms_calsol_stats` | `tools/calsol_stats.py` | Per-(antenna, SPW, field) stats from G/B/K caltables — flagged fraction, SNR, amplitude/phase arrays, delays |
| `ms_calsol_plot` | `tools/calsol_plot.py` | Bokeh HTML dashboard + NPZ from a caltable; calls `ms_calsol_stats` internally |

### Pre-calibration inspection (5 tools)

| Tool | Module | What it does |
|------|--------|-------------|
| `ms_verify_import` | `tools/verify_import.py` | Filesystem check: MS exists + table.info valid + .flagonline.txt non-empty |
| `ms_online_flag_stats` | `tools/online_flags.py` | Parse .flagonline.txt — n_commands, antennas flagged, reason breakdown, time range |
| `ms_flag_summary` | `tools/flag_summary.py` | Per-field/SPW flag fractions from flagdata summary mode |
| `ms_verify_priorcals` | `tools/priorcals_check.py` | Check prior caltables (gc, opac, rq, ap) exist and are non-empty |
| `ms_verify_caltables` | `tools/caltables.py` | Check init_gain.g + BP0.b from initial bandpass exist and have rows |

### Instrument and RFI inspection (3 tools)

| Tool | Module | What it does |
|------|--------|-------------|
| `ms_refant` | `tools/refant.py` | Ranked reference antenna list by geometry + flag fraction heuristics |
| `ms_rfi_channel_stats` | `tools/rfi.py` | Per-channel flag fractions; identifies persistent RFI bands |
| `ms_pol_cal_feasibility` | `tools/pol_cal_feasibility.py` | Parallactic angle spread + D-term feasibility gate |
| `ms_residual_stats` | `tools/residual_stats.py` | CORRECTED − MODEL amplitude distribution per SPW (pre-rflag threshold guide) |

---

## Ingestion utilities (ms_create)

The `ms_create` package converts raw ASDM data to Measurement Sets.
It has its own FastMCP server entry point (`ms_create.server`, port 8002).

| Tool | Module | What it does |
|------|--------|-------------|
| `ms_import_asdm` | `ms_create/import_asdm.py` | Convert ASDM → MS; `ocorr_mode='co'`, `savecmds=True`, `applyflags=False`; writes `import_asdm.py` + `.flagonline.txt` |

Fixed parameters (not exposed): `ocorr_mode='co'` (cross-correlations only),
`savecmds=True` (always write online flag file), `applyflags=False` (flagging
deferred to `ms_apply_preflag`). `with_pointing_correction` defaults to `False`
— expensive on large datasets; set `True` only when science requires it.

---

## Write utilities (ms_modify)

The `ms_modify` package contains tools and utilities that **write** to the MS.
It has its own FastMCP server entry point (`ms_modify.server`, port 8001).
Functions are also callable directly by skills and scripts.

| Tool | Module | What it does |
|------|--------|-------------|
| `ms_set_intents` | `ms_modify/intents.py` | Populate STATE subtable and STATE_ID from calibrator catalogue matching |
| `ms_apply_preflag` | `ms_modify/preflag.py` | Deterministic pre-cal flagging (online + shadow + clip + tfcrop) + calibrator split |
| `ms_generate_priorcals` | `ms_modify/priorcals.py` | Generate gc/opac/rq/ap prior caltables via gencal |
| `ms_setjy` | `ms_modify/setjy.py` | Set Perley-Butler 2017 flux models for standard calibrators |
| `ms_setjy_polcal` | `ms_modify/setjy_polcal.py` | Set polarisation angle models for pol calibrators |
| `ms_initial_bandpass` | `ms_modify/initial_bandpass.py` | gaincal → bandpass → applycal; populates CORRECTED |
| `ms_apply_initial_rflag` | `ms_modify/initial_rflag.py` | rflag + tfcrop on CORRECTED−MODEL residuals in one list-mode pass |
| `ms_apply_rflag` | `ms_modify/rflag.py` | General-purpose rflag pass |
| `ms_gaincal` | `ms_modify/gaincal.py` | Phase/amplitude gain calibration |
| `ms_bandpass` | `ms_modify/bandpass.py` | Bandpass calibration |
| `ms_fluxscale` | `ms_modify/fluxscale.py` | Bootstrap flux scale from flux standard |
| `ms_applycal` | `ms_modify/applycal.py` | Apply caltables; write CORRECTED_DATA |
| *(utility)* | `ms_modify/slurm.py` | SLURM batch submission: wrap scripts in sbatch files, chain with afterok dependencies |

`set_intents` logic:
1. Read fields + positions via `open_msmd`
2. Guard: raise `IntentsAlreadyPopulatedError` if ≥50% of fields have intents
3. Match fields against primary catalogue (`calibrators.lookup`) and VLA cone search
4. Write STATE rows (OBS_MODE, CAL, SIG, SUB_SCAN, FLAG_ROW, REF)
5. Bulk-update STATE_ID in MAIN table
6. Supports `dry_run=True` to preview mapping without writing

---

## Response envelope

Every tool returns this structure:

```json
{
  "tool": "ms_antenna_list",
  "ms_path": "/data/obs/target.ms",
  "status": "ok",
  "completeness_summary": "COMPLETE",
  "data": { "...": "..." },
  "warnings": [],
  "provenance": {
    "casa_calls": ["tb.open('ANTENNA')", "tb.getcol(...)"],
    "casatools_version": "6.7.3.21"
  }
}
```

On hard failure:

```json
{
  "tool": "ms_observation_info",
  "ms_path": "/data/obs/target.ms",
  "status": "error",
  "error_type": "INSUFFICIENT_METADATA",
  "message": "TELESCOPE_NAME is '' — ...",
  "data": null
}
```

`CompletionFlag` values and their meaning:

| Flag | Meaning |
|------|---------|
| `COMPLETE` | Retrieved directly from the MS, no ambiguity |
| `INFERRED` | Derived by heuristic (e.g. intent from calibrator name match); confidence annotated |
| `PARTIAL` | Some rows/channels/antennas present, others missing |
| `SUSPECT` | Value present but likely wrong (e.g. coordinates at exactly (0,0)) |
| `UNAVAILABLE` | Could not be computed; reason in `note` field |

`completeness_summary` in the envelope is the worst-case flag across all fields
in `data`. Computed automatically by `util/formatting.response_envelope()`.

---

## Error taxonomy

| Code | When raised | Always includes |
|------|-------------|-----------------|
| `MS_NOT_FOUND` | Path does not exist | — |
| `NOT_A_MEASUREMENT_SET` | No `table.info` | — |
| `SUBTABLE_MISSING` | Expected subtable absent | subtable name |
| `INSUFFICIENT_METADATA` | Telescope name blank/unknown, or antenna table incomplete/numeric-only | exact `tb.putcell` repair command |
| `CASA_NOT_AVAILABLE` | casatools not installed | install instructions |
| `CASA_OPEN_FAILED` | casatools exception on open | original exception text |
| `COMPUTATION_ERROR` | Internal derived-quantity error | — |
| `INTENTS_ALREADY_POPULATED` | ≥50% of fields already have intents (ms_modify) | field count, coverage % |

`INSUFFICIENT_METADATA` is the most important. It is raised — never silently
degraded — when missing metadata would make all telescope-specific quantities
wrong. The message always contains a copy-pasteable repair command.

---

## Critical conventions

### Parallactic angle (VALIDATION PENDING)

`ms_parallactic_angle_vs_time` returns **two values**:

- `pa_sky_deg`: astropy sky-frame PA (North through East)
- `pa_feed_deg`: feed-frame PA = `pa_sky - 90°` for ALT-AZ mounts (CASA convention)

Both are always returned. All PA output carries `"validation_status": "PENDING"` until
cross-validated against `casatools.measures` on a known VLA reference observation.
Do not use `pa_feed` for D-term calibration solutions until this is cleared.

Per-telescope PA offset table:

| Telescope | Mount | `pa_feed = pa_sky + offset` |
|-----------|-------|-----------------------------|
| VLA, MeerKAT, uGMRT | ALT-AZ | `−90°` |
| WSRT | Equatorial | `0°` (constant; no coverage criterion) |

### Baseline lengths vs UV lengths

`ms_baseline_lengths` returns **physical** baseline lengths from ECEF antenna
positions — these are maximum possible baselines, independent of source position.
UV coverage (projected baselines as a function of HA and declination) is a
Layer 3 tool. Do not conflate the two.

### Calibrator catalogue

`util/calibrators.py` contains **primary flux and bandpass calibrators only**.
Phase calibrators are field-specific and are not catalogued. Attempting to look
up a phase calibrator will return `None` — this is correct behaviour.

Resolved calibrators (CasA, CygA, TauA, VirA) trigger `CALIBRATOR_RESOLVED_WARNING`
if the array's maximum baseline exceeds the catalogued safe UV range for the
observed band. The warning includes the CASA `setjy` command with the correct
component model name.

### CASA table locks

Every CASA table open **must** use the context managers in `util/casa_context.py`:
`open_msmd()`, `open_table()`, `open_ms()`. These guarantee `close()` on
exception. A missing `close()` leaves a persistent lock that corrupts subsequent
opens across processes. Never call `tb.open()` / `tb.close()` directly in tool
code.

---

## Adding a new tool

1. Add a `run()` function in the appropriate `src/ms_inspect/tools/*.py` module
   (or create a new module).
2. All CASA access through `util/casa_context.py` context managers only.
3. All fields in the return dict wrapped with `util/formatting.field()`.
4. Return via `util/formatting.response_envelope()` — never return a bare dict.
5. Register the tool in `server.py` with `@mcp.tool(name="ms_<name>")`.
6. Add unit tests that exercise the logic without CASA (mock or pure-logic paths).
7. Add an integration test stub in `tests/integration/test_tools.py` with the
   `@_SKIP` decorator.
8. Update the tool inventory table in this file and in `DESIGN.md`.

---

## Skills

### Radio interferometry analysis

The interferometrist reasoning document is a Claude Code skill checked into
the repo. It is automatically loaded when working with `.ms` files or the
ms_inspect tools.

@.claude/skills/radio-interferometry/SKILL.md

The skill is split into focused files to stay under the 200-line context limit:

| File | Content |
|------|---------|
| `01-workflow.md` | Step-by-step Phase 1 + Phase 2 analysis protocol |
| `02-orientation.md` | Band tables, intent vocabulary, mosaic handling |
| `03-instrument-sanity.md` | Array configs, elevation/PA/flag thresholds |
| `04-diagnostic-reasoning.md` | Report structure, consistency checks, go/no-go |
| `05-calibrator-science.md` | Flux standards, resolved sources, polarisation calibrators |
| `06-failure-modes.md` | Known failure modes and recovery procedures |

### MS simulator

Generates synthetic CASA Measurement Sets from conversational descriptions
using `casatools.simulator`. Auto-invoked when users ask to simulate, generate,
or create visibility data.

@.claude/skills/ms-simulator/SKILL.md

| File | Content |
|------|---------|
| `01-conversation-protocol.md` | Parameter elicitation, defaults, confirmation flow |
| `02-antenna-configs.md` | Shipped configs, VLA/MeerKAT/uGMRT band tables, custom arrays |
| `03-spectral-source.md` | SPW setup, polarization, component lists, image models |
| `04-corruption-noise.md` | Noise models, gain/bandpass/leakage/troposphere, presets |
| `05-execution.md` | Script generation template, validation, common pitfalls |

## Slash commands

Project-scoped commands live in `.claude/commands/` and are checked into the repo.
Available in Claude Code as `/project:<name>`:

| Command | What it does |
|---------|-------------|
| `/project:inspect <ms_path>` | Full Phase 1 + Phase 2 analysis + go/no-go report |
| `/project:phase1 <ms_path>` | Phase 1 orientation only (6 tools) |
| `/project:phase2 <ms_path>` | Phase 2 instrument sanity only (6 tools) |
| `/project:simulate <description>` | Simulate an MS from a natural-language description |

## What is out of scope for this file

This `CLAUDE.md` describes the **implementation** of the MCP server. Scientific
reasoning about what the tool outputs mean — when to flag a dataset bad, what
elevation threshold to use, how to assess calibrator suitability — lives
exclusively in the skill files under `.claude/skills/radio-interferometry/`.
Do not merge implementation context into the skill files or vice versa.
