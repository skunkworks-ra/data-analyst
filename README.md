# data-analyst

MCP servers, skills, and slash commands for AI-assisted radio interferometric
data reduction. Targets VLA/JVLA/EVLA, MeerKAT, and uGMRT observations stored
as CASA Measurement Sets.

Two MCP servers expose the full tool suite:

- **ms-inspect** — read-only inspection and diagnostics (19 tools)
- **ms-modify** — calibration, flagging, and MS modification (7 tools)

Built on [casatools](https://casa.nrao.edu/) and the
[Model Context Protocol](https://modelcontextprotocol.io/).

---

## Installation

### Prerequisites

Install [pixi](https://prefix.dev) and set up the environment:

```bash
pixi install
pixi run pip install casatools casatasks
```

### Option A — Claude Code plugin (recommended)

Installs both MCP servers, skills, and slash commands:

```bash
# Register the marketplace (once per machine)
claude plugin marketplace add https://github.com/skunkworks-ra/data-analyst --scope user

# Install the plugin
claude plugin install ms-inspect@data-analyst --scope user
```

After install, the `ms-inspect` and `ms-modify` MCP servers are registered
globally, and the `/inspect` and `/simulate` commands are available in all
projects.

### Option B — Claude Code (manual MCP only, no skills)

Register only the MCP servers (skills and commands not included):

```bash
pixi install && pixi run pip install casatools casatasks

# Read-only inspection server
claude mcp add --scope user --transport stdio ms-inspect -- \
  pixi run --manifest-path /path/to/data-analyst/pixi.toml serve

# Modification server (optional)
claude mcp add --scope user --transport stdio ms-modify -- \
  pixi run --manifest-path /path/to/data-analyst/pixi.toml serve-modify
```

Scope options:
- `--scope user` — persists in `~/.claude.json`, available in all projects
- `--scope project` — persists in `.claude/mcp.json`, shared via git
- `--scope local` — persists in `.claude/.mcp.local.json`, project-only, not committed

To remove:

```bash
claude mcp remove --scope user ms-inspect
claude mcp remove --scope user ms-modify
```

### Option C — Claude Desktop and other MCP clients (HTTP transport)

Start the servers in HTTP mode:

```bash
pixi install && pixi run pip install casatools casatasks

# Inspection server (port 8000)
RADIO_MCP_TRANSPORT=http RADIO_MCP_PORT=8000 pixi run serve

# Modification server (port 8001)
RADIO_MCP_TRANSPORT=http RADIO_MCP_PORT=8001 pixi run serve-modify
```

Add to your Claude Desktop `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "ms-inspect": {
      "command": "pixi",
      "args": ["run", "--manifest-path", "/path/to/data-analyst/pixi.toml", "serve-http"]
    },
    "ms-modify": {
      "command": "pixi",
      "args": ["run", "--manifest-path", "/path/to/data-analyst/pixi.toml", "serve-modify-http"]
    }
  }
}
```

For any MCP-compatible client (LangGraph, AutoGen, etc.) — point at
`http://localhost:8000/sse` or `http://localhost:8000/mcp/v1` (streamable HTTP).

### Option D — pip install (no pixi)

```bash
pip install ms-inspect[casa]
ms-inspect  # starts the stdio inspection server
ms-modify   # starts the stdio modification server
```

> **Note:** casatools wheels are platform-specific (Linux x86_64, macOS arm64).
> If your platform is not supported, use the pixi path above.

---

## Tool inventory

### ms-inspect — read-only inspection (19 tools)

#### Layer 1 — Orientation

| Tool | What it returns |
|------|----------------|
| `ms_observation_info` | Telescope, observer, project code, time range, duration |
| `ms_field_list` | Fields with J2000 coordinates, calibrator cross-match, inferred intents |
| `ms_scan_list` | Time-ordered scans with intents, durations, SpW assignments |
| `ms_scan_intent_summary` | Observing time distribution across intents |
| `ms_spectral_window_list` | Per-SpW frequency structure, channel counts, band names |
| `ms_correlator_config` | Dump time, polarisation basis, full-Stokes check |

#### Layer 2 — Instrument sanity

| Tool | What it returns |
|------|----------------|
| `ms_antenna_list` | Antenna positions (ECEF), dish diameters, mount types, array centre |
| `ms_baseline_lengths` | Min/max/median baselines, per-SpW angular resolution and LAS |
| `ms_elevation_vs_time` | Per-scan elevation statistics, low-elevation warnings |
| `ms_parallactic_angle_vs_time` | PA range per field (sky-frame and feed-frame) |
| `ms_shadowing_report` | Shadowed antenna events and pre-existing shadow flags |
| `ms_antenna_flag_fraction` | Per-antenna flag fractions from the FLAG column |

#### Calibration diagnostics

| Tool | What it returns |
|------|----------------|
| `ms_refant` | Ranked reference antenna list (geometry + flagging scores) |
| `ms_verify_caltables` | Structural validation of init_gain.g and BP0.b caltables |
| `ms_rfi_channel_stats` | Per-SpW bad-channel ranges with RFI source annotations |
| `ms_flag_summary` | Per-field/scan/SpW/antenna flag fractions via `flagdata(mode='summary')` |
| `ms_pol_cal_feasibility` | Polarisation calibration feasibility verdict (FULL/LEAKAGE_ONLY/DEGRADED/NOT_FEASIBLE) |
| `ms_online_flag_stats` | Parse `.flagonline.txt` — command counts, antennas, reason breakdown |
| `ms_verify_priorcals` | Check prior caltables (gc, opac, rq, antpos) exist and are non-empty |
| `ms_residual_stats` | Per-SpW amplitude statistics of CORRECTED - MODEL residuals |

### ms-modify — calibration and flagging (7 tools)

| Tool | What it does |
|------|-------------|
| `ms_set_intents` | Populate STATE subtable from calibrator catalogue matching |
| `ms_apply_preflag` | Online flags + shadow + zero-clip + tfcrop, then split calibrators |
| `ms_generate_priorcals` | Generate gain curves, opacities, requantiser, antenna position tables |
| `ms_setjy` | Set flux density models for standard calibrators |
| `ms_initial_bandpass` | Coarse bandpass solve (gaincal + bandpass + applycal) |
| `ms_apply_rflag` | rflag RFI excision on the CORRECTED column |
| `ms_apply_initial_rflag` | Combined rflag + tfcrop on residuals (CORRECTED - MODEL) |

All modify tools support `execute=False` (default) to generate a reviewable
Python script without touching the MS, and `execute=True` to run in-process.

---

## Skills

Skills provide domain reasoning on top of tool outputs. They are loaded
automatically when the plugin is installed.

| Skill | Purpose |
|-------|---------|
| `radio-interferometry` | Interferometrist reasoning for Phase 1 + Phase 2 analysis — band tables, intent vocabulary, elevation/PA/flag thresholds, diagnostic report structure, calibrator science, failure modes |
| `ms-simulator` | Simulate synthetic Measurement Sets from natural-language descriptions using `casatools.simulator` |

## Slash commands

| Command | What it does |
|---------|-------------|
| `/project:inspect <ms_path>` | Full Phase 1 + Phase 2 analysis with go/no-go report |
| `/project:simulate <description>` | Generate a synthetic MS from a conversational description |

---

## Environment variables

| Variable | Default | Effect |
|----------|---------|--------|
| `RADIO_MCP_TRANSPORT` | `stdio` | `stdio` for Claude Code; `http` for remote |
| `RADIO_MCP_PORT` | `8000` / `8001` | HTTP port (inspect / modify) |
| `RADIO_MCP_WORKERS` | `4` | Parallel workers for FLAG column reads (cap 8) |
| `RADIO_MCP_TEST_MS` | — | Path to MS for integration tests |

---

## Development

```bash
# Unit tests (no CASA, no MS required)
pixi run test-unit

# Integration tests (requires a real MS)
RADIO_MCP_TEST_MS=/path/to/your.ms pixi run test-int

# Lint + format check
pixi run check
```

Python `>=3.12`. `casatools` and `casatasks` are PyPI-only — pixi resolves
them via pip into the conda environment.

---

## License

GPL-3.0
