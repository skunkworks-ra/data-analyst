# CLAUDE.md — ms-inspect / radio_ms_mcp

Project-level context for Claude Code and Claude Desktop.
Checked into the repository root — applies to every contributor.

---

## What this project is

This repository ships **three Model Context Protocol (MCP) servers** for an
AI-assisted radio interferometric reduction pipeline targeting VLA/JVLA/EVLA,
MeerKAT, and uGMRT:

- **ms-inspect** — read-only inspection and diagnostics (33 tools, port 8000)
- **ms-modify** — calibration, flagging, and MS modification (16 tools, port 8001)
- **ms-create** — ASDM ingestion and reduction logging (3 tools, port 8002)

`ms-inspect` began as Phase 1 only — Layer 1 (Orientation) and Layer 2
(Instrument Sanity), 13 tools — and has since grown to cover pre-calibration,
calibration, and imaging inspection as those phases were implemented. The
companion `ms-modify` / `ms-create` servers cover the write and ingestion paths.

The design document is at `DESIGN.md` in this directory (§8 has the full,
per-server tool inventory). Read it before making any non-trivial change.

---

## Core contract — do not violate this

> **Tools measure. The Skill reasons.**

Every tool in `src/ms_inspect/tools/` must obey three rules:

1. **One question, one answer.** A tool returns measurements and completeness
   flags. It never suggests a next step and never chains to another tool.
2. **No gates.** Tools may return derived values, rankings, and descriptive
   labels, with their inputs included. Tools may **not** return gates, meaning
   any field whose semantic is "you may or may not proceed". Gating requires
   knowing the science goal and the risk tolerance, and only the skill has
   those.
3. **Explicit uncertainty.** Every field that could not be retrieved carries a
   `CompletionFlag` (`COMPLETE`, `INFERRED`, `PARTIAL`, `SUSPECT`, `UNAVAILABLE`).
   Silence is never used to indicate failure.

### What rule 2 permits and forbids

| Permitted | Forbidden |
|-----------|-----------|
| A derived scalar (`dynamic_range`, `severity`, `pa_spread_deg`) | A boolean verdict (`detection_pass`, `xf_feasible`, `meets_threshold`) |
| A descriptive label with its constants surfaced (`detection` / `marginal` / `undetected`) | A `blocker` or `verdict` field naming what stops you |
| A ranking with the ranked quantity attached (`ms_refant`, `leakage_cal_candidates`) | Selecting one entry from a ranking and substituting it for the user's choice |
| A suggested parameter value, labelled as such (`recommended_minblperant`, `suggested.*`) | Withholding a value because a threshold was not met |

**Inputs travel with outputs.** A derived value must ship the quantities and
constants it was computed from, so the skill can recompute it under a different
tolerance. A ratio whose numerator and denominator are absent is a verdict
wearing a number's clothes.

### Why gates specifically

A gate that fails *loudly* costs a CASA error and a retry. A gate that fails
*silently* costs science that was never attempted, with nothing in the output
recording what was forgone — and the threshold behind it was one constant chosen
for a typical case. The motivating case is in `docs/session_context.md:65`:
D-terms on J1454 at 24 degrees of parallactic coverage, "marginal, solved per
user direction". The right answer there was "proceed, and limit
fractional-polarization claims to the few percent level". No boolean can express
that.

This also states a preference for **posterior verification over prior
gating**: measure the result and report it, rather than refusing to compute.

**One named exception:** `ms_workflow_status.next_recommended_step`. It gates on
filesystem state, not on a scientific claim, and it fails visibly — an
unreadable probe returns `probe_failed_*` and `UNAVAILABLE` rather than
inferring. Do not add a second exception without the same two properties.

Violating the contract — adding a gate, collapsing flags, adding tool-chaining
logic — will break the Skill's reasoning model and produce silent scientific
errors. If you are unsure whether something belongs in a tool or in the Skill,
it belongs in the Skill.

---

## Repository layout

```
radio-analyst/
├── CLAUDE.md                      ← this file
├── DESIGN.md                      ← architecture, failure modes, conventions
├── README.md
├── pixi.toml                      ← environment (conda-forge + casatools via PyPI)
├── pyproject.toml                 ← build metadata and tooling config
├── .mcp.json                      ← MCP server definitions (all three servers)
├── .claude-plugin/
│   ├── plugin.json                ← plugin manifest
│   └── marketplace.json           ← marketplace catalogue entry
├── .claude/
│   ├── skills/
│   │   ├── radio-interferometry/  ← 18 files: SKILL.md + 00..13 knowledge files
│   │   │                             (plus wildcat/, unreachable from SKILL.md)
│   │   └── ms-simulator/          ← SKILL.md + 01..05 knowledge files
│   └── commands/                  ← inspect, precal, calibrate, polcal, image, simulate
├── docs/                          ← session context, tool survey, fix plan, handoff
├── bin/
│   ├── serve.sh                   ← MCP plugin entry point (ms-inspect)
│   ├── serve-modify.sh            ← MCP plugin entry point (ms-modify)
│   └── serve-create.sh            ← MCP plugin entry point (ms-create)
├── src/
│   ├── ms_create/
│   │   ├── __init__.py            ← version string
│   │   ├── server.py              ← FastMCP entry point (ingestion, port 8002)
│   │   ├── exceptions.py          ← ASDMNotFoundError, ImportFailedError
│   │   ├── sdm_summary.py         ← ms_sdm_summary (pre-conversion ASDM inspection)
│   │   ├── import_asdm.py         ← ms_import_asdm
│   │   └── reduction_log.py       ← ms_reduction_log
│   ├── ms_modify/
│   │   ├── __init__.py            ← version string
│   │   ├── server.py              ← FastMCP entry point (write, port 8001)
│   │   ├── exceptions.py          ← ms_modify error types
│   │   ├── intents.py             ← ms_set_intents
│   │   ├── preflag.py             ← ms_apply_preflag
│   │   ├── priorcals.py           ← ms_generate_priorcals
│   │   ├── setjy.py               ← ms_setjy
│   │   ├── setjy_polcal.py        ← ms_setjy_polcal
│   │   ├── initial_bandpass.py    ← ms_initial_bandpass
│   │   ├── initial_rflag.py       ← ms_apply_initial_rflag
│   │   ├── postcal_flag.py        ← ms_postcal_flag
│   │   ├── flag_caltable.py       ← ms_flag_caltable
│   │   ├── rflag.py               ← ms_apply_rflag
│   │   ├── gaincal.py             ← ms_gaincal
│   │   ├── polcal.py              ← ms_polcal
│   │   ├── bandpass.py            ← ms_bandpass
│   │   ├── fluxscale.py           ← ms_fluxscale
│   │   ├── applycal.py            ← ms_applycal
│   │   ├── tclean.py              ← ms_tclean
│   │   ├── pathguard.py           ← output-caltable path validation + safe-delete guard
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
│       │   ├── flags.py           ← ms_flag_preflight, ms_antenna_flag_fraction
│       │   ├── flag_summary.py    ← ms_flag_summary
│       │   ├── online_flags.py    ← ms_online_flag_stats
│       │   ├── verify_import.py   ← ms_verify_import
│       │   ├── verify_model.py    ← ms_verify_model
│       │   ├── workflow_status.py ← ms_workflow_status
│       │   ├── priorcals_check.py ← ms_verify_priorcals
│       │   ├── caltables.py       ← ms_verify_caltables
│       │   ├── calsol_stats.py    ← ms_calsol_stats
│       │   ├── calsol_stats_detail.py ← ms_calsol_stats_detail
│       │   ├── calsol_plot.py     ← ms_calsol_plot
│       │   ├── calsol_plot_library.py ← ms_plot_caltable_library
│       │   ├── gaincal_snr_predict.py ← ms_gaincal_snr_predict
│       │   ├── refant.py          ← ms_refant
│       │   ├── residual_stats.py  ← ms_residual_stats
│       │   ├── corrected_stats.py ← ms_corrected_stats
│       │   ├── rfi.py             ← ms_rfi_channel_stats
│       │   ├── spw_amp_severity.py ← ms_spw_amp_severity
│       │   ├── pol_cal_conditions.py ← ms_pol_cal_conditions
│       │   └── image_stats.py     ← ms_image_stats
│       └── util/
│           ├── casa_context.py    ← context managers: open_msmd, open_table, open_ms, open_image
│           ├── dispatch.py        ← shared tool dispatch used by all three servers
│           ├── formatting.py      ← response envelope, CompletionFlag, offload_detail
│           ├── conversions.py     ← MJD→UTC, Hz→GHz, ECEF→geodetic, corr codes, etc.
│           ├── telescope.py       ← TelescopeProfile: per-telescope constants
│           ├── calibrators.py     ← bundled flux/BP calibrator catalogue
│           ├── vla_calibrators.py ← VLA calibrator cone search
│           ├── pol_calibrators.py ← polarisation calibrator catalogue
│           ├── polcal_setjy_fit.py ← polarised model fitting for ms_setjy_polcal
│           ├── phase_cal_catalog.py ← ms_phase_cal_lookup (reads PhaseCalList.txt)
│           ├── PhaseCalList.txt   ← NRAO VLA phase-calibrator catalogue (data file)
│           └── spw_coverage.py    ← SpW frequency-coverage helpers
└── tests/
    ├── unit/                      ← no CASA required, runs everywhere (39 modules)
    └── integration/               ← requires casatools; auto-uses 3C391 tarball if present
        ├── conftest.py            ← 3C391 tarball extraction fixture
        ├── test_tools.py
        └── test_set_intents.py
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
| `RADIO_MCP_HOST` | `127.0.0.1` | HTTP bind address. No authentication on the HTTP transport — keep it on localhost unless the network is trusted |
| `RADIO_MCP_PORT` | `8000` | HTTP port (ms-inspect); ms-modify uses 8001, ms-create uses 8002 |
| `RADIO_MCP_WORKERS` | `4` | Parallel worker count for FLAG column reads (cap 8) |
| `RADIO_MCP_TEST_MS` | — | Path to pre-extracted MS for integration tests |
| `RADIO_MCP_TEST_MS_TGZ` | — | Path to `.ms.tgz` tarball; auto-extracted by conftest.py |
| `RADIO_MCP_TEST_CALTABLE` | — | Path to a G or B caltable; the caltable integration tests in `tests/integration/test_tools.py` skip without it |

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

### Layer 2 — Instrument Sanity (7 tools)

| Tool | Module | Primary CASA call |
|------|--------|-------------------|
| `ms_antenna_list` | `tools/antennas.py` | `tb → ANTENNA` |
| `ms_baseline_lengths` | `tools/antennas.py` | computed from ECEF positions |
| `ms_elevation_vs_time` | `tools/geometry.py` | astropy AltAz (not CASA measures) |
| `ms_parallactic_angle_vs_time` | `tools/geometry.py` | astropy LST + atan2 |
| `ms_shadowing_report` | `tools/shadowing.py` | `casatasks.flagdata(mode='list', action='calculate')` with [summary, shadow, summary]; the shadow contribution is the difference |
| `ms_flag_preflight` | `tools/flags.py` | Fast probe: row count, FLAG shape, data volume, runtime estimate, recommended workers |
| `ms_antenna_flag_fraction` | `tools/flags.py` | `tb.getcolslice(FLAG)` adaptive parallel reads; accepts `n_workers` override |

### Calibration inspection (5 tools)

| Tool | Module | What it does |
|------|--------|-------------|
| `ms_calsol_stats` | `tools/calsol_stats.py` | Per-(antenna, SPW, field) stats from G/B/K caltables — flagged fraction, SNR, amplitude/phase arrays, delays |
| `ms_calsol_stats_detail` | `tools/calsol_stats.py` | Deep-dive reader over the `.calsol_stats.npz` sidecar written by `ms_calsol_stats`; full per-(antenna, SPW, field) detail (`kind='low_snr'|'amp_outliers'|'antenna'`) beyond the bounded summary |
| `ms_calsol_plot` | `tools/calsol_plot.py` | Bokeh HTML dashboard from a single caltable, read directly from the caltable columns (does not call `ms_calsol_stats`); view routed by VisCal type |
| `ms_plot_caltable_library` | `tools/calsol_plot_library.py` | Batch plot an explicit list of caltables in one call; partial-success — a bad table records an error entry rather than aborting |
| `ms_gaincal_snr_predict` | `tools/gaincal_snr_predict.py` | Predict per-(antenna, SPW) SNR for a candidate solint; uses SEFD table + MS metadata; requires `flux_jy` from `ms_setjy` |

### Pre-calibration inspection (7 tools)

| Tool | Module | What it does |
|------|--------|-------------|
| `ms_verify_import` | `tools/verify_import.py` | Filesystem check: MS exists + table.info valid + .flagonline.txt non-empty |
| `ms_workflow_status` | `tools/workflow_status.py` | State probe over MS + workdir: ms_valid, intents_populated, calibrators_ms/priorcals/initial_bandpass present, corrected_populated, final_caltables/first_image present, and a categorical `next_recommended_step` |
| `ms_verify_model` | `tools/verify_model.py` | Per-field MODEL_DATA sanity probe after setjy/setjy_polcal: flags default-pinned (MODEL=1 Jy → flux-scale trap), out-of-band amplitude, and — for `polcal_fields` — missing polarization (zero cross-hands = Stokes-I clobber). Requires usescratch=True |
| `ms_online_flag_stats` | `tools/online_flags.py` | Parse .flagonline.txt — n_commands, antennas flagged, reason breakdown, time range |
| `ms_flag_summary` | `tools/flag_summary.py` | Per-field/SPW flag fractions from flagdata summary mode |
| `ms_verify_priorcals` | `tools/priorcals_check.py` | Check prior caltables (gc, opac, rq, ap) exist and are non-empty |
| `ms_verify_caltables` | `tools/caltables.py` | Check init_gain.g + BP0.b from initial bandpass exist and have rows |

### Instrument and RFI inspection (7 tools)

| Tool | Module | What it does |
|------|--------|-------------|
| `ms_refant` | `tools/refant.py` | Ranked reference antenna list by geometry + flag fraction heuristics |
| `ms_phase_cal_lookup` | `util/phase_cal_catalog.py` | Cross-match a sky position against the NRAO VLA phase-calibrator catalog; nearest source within `max_sep_deg` with flux, UV limits, and per-config quality codes (P/S/W/C/X) |
| `ms_rfi_channel_stats` | `tools/rfi.py` | Per-channel flag fractions; identifies persistent RFI bands |
| `ms_spw_amp_severity` | `tools/spw_amp_severity.py` | Robust per-channel amplitude stats (median/MAD/min/max) of any data column, aggregated per SpW. Severity = band_floor vs a clean-SpW anchor (RFI-dominated drop signal) + estimated_discardable_frac (localized-RFI magnitude). Memory-bounded reservoir sampling. |
| `ms_pol_cal_conditions` | `tools/pol_cal_conditions.py` | Pol calibrator identification, catalogue properties at the observed band, and per-field parallactic-angle spread ranked; no verdict |
| `ms_residual_stats` | `tools/residual_stats.py` | CORRECTED − MODEL amplitude distribution per SPW (pre-rflag threshold guide) |
| `ms_corrected_stats` | `tools/corrected_stats.py` | Per-field parallel-hand amplitude (median/robust-std/p95) + phase RMS of a data column, **vector-averaged over the channel range** (so faint sources are not noise-biased). Post-applycal calibration sanity check. |

### Phase 3 — Imaging inspection (1 tool)

| Tool | Module | What it does |
|------|--------|-------------|
| `ms_image_stats` | `tools/image_stats.py` | Robust RMS (MAD-based), peak flux, dynamic range, restoring beam from a CASA image. For a multi-plane image (frequency cube / multi-Stokes, e.g. IQUV pol cube) also returns `n_planes` + a per-(Stokes, channel) `planes` array |

---

## Ingestion utilities (ms_create)

The `ms_create` package converts raw ASDM data to Measurement Sets.
It has its own FastMCP server entry point (`ms_create.server`, port 8002).

| Tool | Module | What it does |
|------|--------|-------------|
| `ms_sdm_summary` | `ms_create/sdm_summary.py` | Pre-conversion ASDM inspection (read-only, no casatools): telescope, config, band, per-SPW continuum-vs-line classification, HI-21cm coverage, correlation products, sources+intents, scan balance, max target elevation. Decide *what* a dataset is before importing it. |
| `ms_import_asdm` | `ms_create/import_asdm.py` | Convert ASDM → MS; `ocorr_mode='co'`, `savecmds=True`, `applyflags=False`; writes `import_asdm.py` + `.flagonline.txt` |
| `ms_reduction_log` | `ms_create/reduction_log.py` | Working-calls ledger: shuttle known-good calls into a per-reduction JSONL recipe. `action='append'` records one validated call; `'render'` emits the ordered recipe + replay script; `'list'` gives a compact step summary |

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
| `ms_set_intents` | `ms_modify/intents.py` | Populate STATE subtable and STATE_ID from calibrator catalogue matching, including `CALIBRATE_POL_ANGLE` / `CALIBRATE_POL_LEAKAGE` from pol-catalogue identity. `pol_leakage_fields` nominates a field the catalogue does not know (the tool never nominates one itself); `pol_sources_available` reports what the MS contains |
| `ms_apply_preflag` | `ms_modify/preflag.py` | Deterministic pre-cal flagging (online + shadow + clip + tfcrop) + calibrator split |
| `ms_generate_priorcals` | `ms_modify/priorcals.py` | Generate gc/opac/rq/ap prior caltables via gencal |
| `ms_setjy` | `ms_modify/setjy.py` | Set Perley-Butler 2017 flux models for standard calibrators. `exclude_fields` omits a field from the Stokes-I pass (use for a pol-angle cal that overlaps a flux/BP cal — its polarized model is set by `ms_setjy_polcal`, and a plain setjy would clobber it) |
| `ms_setjy_polcal` | `ms_modify/setjy_polcal.py` | Set polarisation angle models for pol calibrators |
| `ms_initial_bandpass` | `ms_modify/initial_bandpass.py` | gaincal → bandpass → applycal; populates CORRECTED |
| `ms_apply_initial_rflag` | `ms_modify/initial_rflag.py` | rflag + tfcrop on CORRECTED−MODEL residuals in one list-mode pass; **requires** explicit `field` (only the field with valid CORRECTED) |
| `ms_postcal_flag` | `ms_modify/postcal_flag.py` | Post-cal RFI flagging on phase cal + target CORRECTED in one list-mode pass: per-SpW robust clip (median + `clip_sigma`·1.4826·MAD, default 5σ; `uvrange`-scopable for extended sources) → tfcrop + rflag on kept SpWs → manual flag of drop-tier SpWs. Consumes `ms_spw_amp_severity` triage (skill 13); **requires** explicit `field` |
| `ms_flag_caltable` | `ms_modify/flag_caltable.py` | Autoflag a caltable's solutions (mode auto-routed from VisCal: B→tfcrop, G/T/D→rflag, K refused) at a gentle sigma; reports flagged fraction before/after |
| `ms_apply_rflag` | `ms_modify/rflag.py` | General-purpose rflag pass |
| `ms_gaincal` | `ms_modify/gaincal.py` | Phase/amplitude/cross-hand delay gain calibration (supports gaintype='KCROSS' with smodel) |
| `ms_polcal` | `ms_modify/polcal.py` | Polarisation calibration: D-term leakage (Df/Df+QU) or position angle (Xf) |
| `ms_bandpass` | `ms_modify/bandpass.py` | Bandpass calibration |
| `ms_fluxscale` | `ms_modify/fluxscale.py` | Bootstrap flux scale from flux standard |
| `ms_applycal` | `ms_modify/applycal.py` | Apply caltables; write CORRECTED_DATA |
| `ms_tclean` | `ms_modify/tclean.py` | Generate (and optionally execute) a tclean imaging script; validates CORRECTED_DATA; pbcor=True hardcoded. Cube args (`nchan`/`start`/`width`/`outframe`) for frequency cubes incl. IQUV polarization cubes (specmode='cube'); ignored otherwise |
| *(utility)* | `ms_modify/slurm.py` | SLURM batch submission: wrap scripts in sbatch files, chain with afterok dependencies |

`set_intents` logic:
1. Read fields + positions via `open_msmd`
2. Guard: raise `IntentsAlreadyPopulatedError` if ≥50% of fields have intents
3. Match fields against primary catalogue (`calibrators.lookup`) and VLA cone search
4. Add polarisation intents from pol-catalogue identity: a Category A angle
   standard gets `CALIBRATE_POL_ANGLE`, a dedicated leakage cal (role is
   leakage and not angle) gets `CALIBRATE_POL_LEAKAGE`. These are additive, not
   a replacement: 3C286 is both a flux standard and the angle standard.
   Nominating an uncatalogued field as the leakage cal is a strategy decision
   and requires `pol_leakage_fields`
5. Write STATE rows (OBS_MODE, CAL, SIG, SUB_SCAN, FLAG_ROW, REF)
6. Bulk-update STATE_ID in MAIN table
7. Supports `dry_run=True` to preview mapping without writing

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
| `00-playbook.md` | Stage-to-action lookup table; load-on-demand index for all skill files |
| `01-workflow.md` | Phase 1 orientation protocol (6 tools) |
| `01b-workflow-phase2.md` | Phase 2 instrument sanity protocol (6 tools) |
| `02-orientation.md` | Band tables, intent vocabulary, mosaic handling |
| `03-instrument-sanity.md` | Array configs, elevation/PA/flag thresholds |
| `04-diagnostic-reasoning.md` | Report structure, consistency checks, go/no-go |
| `05-calibrator-science.md` | Flux standards, resolved sources, polarisation calibrators |
| `06-failure-modes.md` | Known failure modes and recovery procedures |
| `07-calibration-execution.md` | Full calibration solve sequence (initial phase → delay → BP → gain → fluxscale → applycal) |
| `08-pband-specifics.md` | VLA P-band specifics (flux scale, ionosphere, RFI, bandpass ripples) |
| `09-polcal-execution.md` | Polarisation calibration (Kcross → D-terms → Xf → applycal with parang) |
| `10-precal-workflow.md` | Pre-calibration pipeline (online flags → preflag → priorcals → setjy → refant → initial BP → rflag) |
| `11-imaging.md` | First-pass continuum/cube imaging with derived tclean parameters and ms_image_stats gate |
| `12-selfcal.md` | Single-pass phase selfcal with before/after DR comparison and stop-and-recommend gate |
| `13-postcal-rfi-flagging.md` | Post-cal RFI flagging on target/phase cal + SpW severity triage (drop vs salvage), thresholds read off the dataset's own distribution |

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

Commands live in `.claude/commands/` and are checked into the repo. Working in a
clone they are invoked as `/<name>`; installed from the marketplace they are
namespaced by the plugin, `/radio-analyst:<name>`. There is no `/project:`
prefix in either context.

| Command | What it does |
|---------|-------------|
| `/inspect <ms_path>` | Full Phase 1 + Phase 2 analysis + go/no-go report |
| `/precal <ms_path>` | Pre-calibration workflow (online flags → preflag → priorcals → setjy → refant → initial BP → rflag) |
| `/calibrate <ms_path>` | Full calibration solve (initial phase → delay → bandpass → gain → fluxscale → applycal) |
| `/polcal <ms_path>` | Polarisation calibration (Kcross → D-terms → Xf → applycal with parang) |
| `/image <ms_path>` | First-pass continuum/cube imaging with derived tclean parameters |
| `/simulate <description>` | Simulate an MS from a natural-language description |

## What is out of scope for this file

This `CLAUDE.md` describes the **implementation** of the MCP server. Scientific
reasoning about what the tool outputs mean — when to flag a dataset bad, what
elevation threshold to use, how to assess calibrator suitability — lives
exclusively in the skill files under `.claude/skills/radio-interferometry/`.
Do not merge implementation context into the skill files or vice versa.

---

## `analyst_driver` — the external loop (2026-08-11)

`src/analyst_driver/`, entry point `analyst-driver` (a `[project.scripts]`
console script, so `pixi install` builds a real binary in
`.pixi/envs/default/bin/` that runs from any directory).

Runs a reduction as a sequence of long jobs and calls a model only between
them. The model reads `BRIEF.md`, writes one JSON decision, and exits; the
driver generates the script from a whitelisted `ms_modify` tool, submits it,
polls it, harvests the result, and calls the model again. **No model process
ever waits on a job** — that is the whole point, and it is why an eight-hour
`tclean` costs two model calls rather than eight hours of held context.

Durable properties:

- **The model emits a tool call, never code.** Four actions only: `run`,
  `redo`, `done`, `ask`. `validate.py` checks parameters against the real
  `run()` signature with `inspect.signature`, so `whitelist.yaml` deliberately
  declares no parameter types and cannot drift from the tools.
- **Evidence is checked, not trusted.** Every number the model cites must
  exist in the file it cites, within 2 percent. A rationale in prose cannot be
  verified, which is why the two are separate fields.
- **`verifier.yaml` holds the numeric limits** and is their single source of
  truth; the skill prose quotes those numbers. The verifier reports into the
  brief as evidence and decides nothing — posterior verification, not gating.
  It always reports how many checks ran, never a bare verdict.
- **`init` freezes `config.toml`, `whitelist.yaml`, `recipe.yaml`,
  `verifier.yaml` and `PROMPT.md` into the run directory.** A later edit to
  the package cannot change a run already in flight. `--root` and `--config`
  override the packaged defaults without editing the installed package.
- **The run directory is its own git repo**, committed every turn by
  `commit.commit_turn()` — the single function that writes the decision
  provenance, the `ms_reduction_log` ledger line, the replay script and the
  commit. Split across call sites, those four drift.
- **The driver enforces every limit and never tells the model**, because a
  model that knows it is short of budget trades away science to finish.
