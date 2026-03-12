# data-analyst

This repository provides MCP tools and skills to interact with radio interferometric measurement set data. With the goal to enable complex radio interferometric workflows in an agentic fashion. We are currently relying on CASA for information gathering about the measurement set but this is likely to change in the future.

## Installation

### Prerequisites

Install [pixi](https://prefix.dev) and set up the environment:

```bash
pixi install
pixi run pip install casatools casatasks
```

---

### Option A — Claude Code plugin (recommended)

Installs the MCP server, skills, and slash commands. Register the repo as a
marketplace once, then install:

```bash
# Register the marketplace (once per machine)
claude plugin marketplace add https://github.com/skunkworks-ra/data-analyst --scope user

# Install the plugin
claude plugin install ms-inspect --scope user
```

After install, the `ms-inspect` MCP server is registered globally, and the
`/radio-interferometry`, `/inspect`, `/phase1`, `/phase2`, and `/simulate`
commands are available in all projects.

---

### Option B — Claude Code (manual MCP only, no skills)

Register only the MCP server (skills and commands not included):

```bash
pixi install && pixi run pip install casatools casatasks
claude mcp add --scope user --transport stdio ms-inspect -- \
  pixi run --manifest-path /path/to/ms-inspect/pixi.toml serve
```

Scope options:
- `--scope user` — persists in `~/.claude.json`, available in all projects
- `--scope project` — persists in `.claude/mcp.json`, shared via git
- `--scope local` — persists in `.claude/.mcp.local.json`, project-only, not committed

To remove:

```bash
claude mcp remove --scope user ms-inspect
```

---

### Option C — Claude Desktop and other MCP clients (HTTP transport)

Start the server in HTTP mode:

```bash
pixi install && pixi run pip install casatools casatasks
RADIO_MCP_TRANSPORT=http RADIO_MCP_PORT=8000 pixi run serve
```

Add to your Claude Desktop `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "ms-inspect": {
      "command": "pixi",
      "args": ["run", "--manifest-path", "/path/to/ms-inspect/pixi.toml", "serve-http"]
    }
  }
}
```

For any MCP-compatible client (LangGraph, AutoGen, etc.) — point at
`http://localhost:8000/sse` or `http://localhost:8000/mcp/v1` (streamable HTTP).

---

### Option D — pip install (no pixi)

```bash
pip install ms-inspect[casa]
ms-inspect  # starts the stdio MCP server
```

> **Note:** casatools wheels are platform-specific (Linux x86_64, macOS arm64).
> If your platform is not supported, use the pixi path above.