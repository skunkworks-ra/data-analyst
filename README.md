# data-analyst

This repository provides MCP tools and skills to interact with radio interferometric measurement set data. With the goal to enable complex radio interferometric workflows in an agentic fashion. We are currently relying on CASA for information gathering about the measurement set but this is likely to change in the future.

## Installation

### Prerequisites

Install [pixi](https://prefix.dev) and set up the environment:

```bash
pixi install
pixi run pip install casatools casatasks
```

### Claude Code plugin (global access)

Install as a Claude Code plugin to make the ms-inspect MCP tools available from any working directory:

```bash
claude plugin install /path/to/data-analyst --scope user
```

Then verify the tools are available in any Claude Code session.