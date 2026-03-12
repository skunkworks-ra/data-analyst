# data-analyst

This repository provides MCP tools and skills to interact with radio interferometric measurement set data. With the goal to enable complex radio interferometric workflows in an agentic fashion. We are currently relying on CASA for information gathering about the measurement set but this is likely to change in the future.

## Installation

### Prerequisites

Install [pixi](https://prefix.dev) and set up the environment:

```bash
pixi install
pixi run pip install casatools casatasks
```

### Claude Code MCP server

Register the ms-inspect MCP server with Claude Code to make the tools available across sessions:

```bash
# From the data-analyst directory:
claude mcp add --scope user --transport stdio ms-inspect -- pixi run serve
```

Scope options:
- `--scope user` — persists in `~/.claude.json`, available in all projects
- `--scope project` — persists in `.claude/mcp.json`, shared via git
- `--scope local` — persists in `.claude/.mcp.local.json`, project-only, not committed

To remove:

```bash
claude mcp remove --scope user ms-inspect
```