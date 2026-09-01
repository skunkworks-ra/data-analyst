# radio-analyst

MCP servers, skills, and slash commands for AI-assisted radio interferometric
data reduction. Targets VLA/JVLA/EVLA, MeerKAT, and uGMRT observations stored
as CASA Measurement Sets.

Three MCP servers expose the full tool suite:

- **ms-inspect** — read-only inspection and diagnostics (33 tools, port 8000)
- **ms-modify** — calibration, flagging, and MS modification (16 tools, port 8001)
- **ms-create** — ASDM ingestion and reduction logging (3 tools, port 8002)

Built on [casatools](https://casa.nrao.edu/) and the
[Model Context Protocol](https://modelcontextprotocol.io/).

---

## Installation

### Claude Code plugin (recommended)

Installs both MCP servers, skills, and slash commands in two commands:

```bash
# Register the marketplace (once per machine)
claude plugin marketplace add https://github.com/skunkworks-ra/radio-analyst

# Install the plugin
claude plugin install radio-analyst@radio-analyst
```

After install, the `ms-inspect` and `ms-modify` MCP servers are registered
globally, and the `/inspect` and `/simulate` commands are available in all
projects. CASA tools are installed automatically on first use (~500 MB,
Linux x86_64 and macOS arm64 only).

To remove:

```bash
claude plugin uninstall radio-analyst@radio-analyst
```

### Local development

Use this when actively working on the plugin itself. Registers the MCP servers
directly against the local pixi environment — no plugin system involved.

```bash
git clone https://github.com/skunkworks-ra/radio-analyst.git
cd radio-analyst
pixi install
pixi run pip install casatools casatasks   # first time only; ~500 MB
pixi run install-mcp
```

`install-mcp` calls `bin/install-local.sh`, which registers `ms-inspect`,
`ms-modify`, and `ms-create` via `claude mcp add --scope user` pointing
directly at `.pixi/envs/default/bin/`. Re-run after any `pixi install` that
rebuilds the environment. The script detects and removes a plugin-managed
install automatically before registering.

To switch back to the plugin install:

```bash
pixi run uninstall-mcp
# then follow the Claude Code plugin instructions above
```

### Claude Desktop and other MCP clients (HTTP transport)

Clone the repo, install the environment, then start the servers in HTTP mode:

```bash
git clone https://github.com/skunkworks-ra/radio-analyst.git
cd radio-analyst
pixi install && pixi run pip install casatools casatasks

# Inspection server (port 8000)
RADIO_MCP_TRANSPORT=http RADIO_MCP_PORT=8000 pixi run serve

# Modification server (port 8001)
RADIO_MCP_TRANSPORT=http RADIO_MCP_PORT=8001 pixi run serve-modify

# Ingestion server (port 8002)
RADIO_MCP_TRANSPORT=http RADIO_MCP_PORT=8002 pixi run serve-create
```

Add to your Claude Desktop `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "ms-inspect": {
      "command": "pixi",
      "args": ["run", "--manifest-path", "/path/to/radio-analyst/pixi.toml", "serve-http"]
    },
    "ms-modify": {
      "command": "pixi",
      "args": ["run", "--manifest-path", "/path/to/radio-analyst/pixi.toml", "serve-modify-http"]
    }
  }
}
```

For any MCP-compatible client — point at `http://localhost:8000/mcp` (streamable HTTP).

---

## Tool inventory

The full per-tool inventory with descriptions lives in
[`design_docs/DESIGN.md`](design_docs/DESIGN.md) (§8 ms-inspect, §8b ms-modify, §8c ms-create). A
summary by category:

### ms-inspect — read-only inspection (33 tools)

- **Layer 1 — Orientation** (6): observation info, field list, scan list, scan
  intent summary, spectral window list, correlator config.
- **Layer 2 — Instrument sanity** (7): antenna list, baseline lengths, elevation
  vs time, parallactic angle vs time, shadowing report, flag preflight, antenna
  flag fraction.
- **Calibration inspection** (6): caltable solution stats + detail reader,
  single/library caltable plots, gaincal SNR prediction, caltable structural checks.
- **Pre-calibration inspection** (5): import/model/priorcal verification, online
  flag stats, flag summary.
- **Instrument & RFI inspection** (7): reference-antenna ranking, per-channel RFI
  stats, SpW amplitude severity, pol-cal feasibility, residual/corrected-data
  stats, phase-calibrator catalogue lookup.
- **Imaging inspection** (1): robust image RMS / peak / dynamic-range / beam.
- **Pipeline / workflow** (1): workflow state probe.

### ms-modify — calibration and flagging (16 tools)

Intent population, preflagging, prior caltables, flux models (setjy / setjy
polcal), bandpass, gaincal, polcal, fluxscale, applycal, residual and post-cal
RFI flagging, caltable autoflag, and tclean imaging. All modify tools support
`execute=False` (default) to generate a reviewable Python script without
touching the MS, and `execute=True` to run in-process.

### ms-create — ingestion (3 tools)

Pre-conversion ASDM summary, ASDM → MS import, and a per-reduction working-calls
ledger.

---

## Skills

Skills provide domain reasoning on top of tool outputs. They are loaded
automatically when the plugin is installed.

| Skill | Purpose |
|-------|---------|
| `radio-interferometry` | Interferometrist reasoning for Phase 1 + Phase 2 analysis — band tables, intent vocabulary, elevation/PA/flag thresholds, diagnostic report structure, calibrator science, failure modes |
| `ms-simulator` | Simulate synthetic Measurement Sets from natural-language descriptions using `casatools.simulator` |

## Slash commands

Working in a clone these are invoked as `/<name>`; installed from the
marketplace they are namespaced by the plugin, `/radio-analyst:<name>`.

| Command | What it does |
|---------|-------------|
| `/inspect <ms_path>` | Full Phase 1 + Phase 2 analysis with go/no-go report |
| `/precal <ms_path>` | Pre-calibration workflow (online flags → preflag → priorcals → setjy → refant → initial BP → rflag) |
| `/calibrate <ms_path>` | Full calibration solve (initial phase → delay → bandpass → gain → fluxscale → applycal) |
| `/polcal <ms_path>` | Polarisation calibration (Kcross → D-terms → Xf → applycal with parang) |
| `/image <ms_path>` | First-pass continuum/cube imaging with derived tclean parameters |
| `/simulate <description>` | Generate a synthetic MS from a conversational description |

---

## Environment variables

| Variable | Default | Effect |
|----------|---------|--------|
| `RADIO_MCP_TRANSPORT` | `stdio` | `stdio` for Claude Code; `http` for remote |
| `RADIO_MCP_HOST` | `127.0.0.1` | HTTP bind address. **The HTTP transport has no authentication — do not bind beyond localhost on shared or untrusted networks** |
| `RADIO_MCP_PORT` | `8000` / `8001` / `8002` | HTTP port (inspect / modify / create) |
| `RADIO_MCP_WORKERS` | `4` | Parallel workers for FLAG column reads (cap 8) |
| `RADIO_MCP_TEST_MS` | — | Path to MS for integration tests |
| `RADIO_MCP_TEST_MS_TGZ` | — | Path to `.ms.tgz` tarball; auto-extracted by conftest.py |

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
