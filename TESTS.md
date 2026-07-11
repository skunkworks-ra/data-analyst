# Testing Guide — radio-analyst Claude Code Plugin

The plugin (`radio-analyst@radio-analyst`) ships three MCP servers —
`ms-inspect`, `ms-modify`, `ms-create`. Run these in order. Steps 0–2 give fast
feedback with no side effects; step 3 is the real end-to-end test.

---

## 0. Python test suite (unit + integration)

```bash
# Unit tests — no CASA, no MS required; runs everywhere
pixi run test-unit

# Integration tests — auto-uses the 3C391 tarball if present, or point at an MS:
#   RADIO_MCP_TEST_MS_TGZ=/path/to/3c391.ms.tgz pixi run test-int
#   RADIO_MCP_TEST_MS=/path/to/your.ms          pixi run test-int
pixi run test-int

# Lint + format check (CI gate)
pixi run check
```

Unit tests live in `tests/unit/` (pure logic, no casatools); integration tests
in `tests/integration/` (require casatools, gated by the fixtures in
`conftest.py`).

---

## 1. Structural smoke test (no install required)

Confirm the plugin manifest and MCP config are valid JSON and point at real paths:

```bash
# From the repo root
python -c "import json; d=json.load(open('.claude-plugin/plugin.json')); print(d['mcpServers'], d['skills'], d['commands'])"
python -c "import json; d=json.load(open('.mcp.json')); print(d)"

# Verify the skill and command directories exist
ls .claude/skills/
ls .claude/commands/
```

Expected: no errors, both directories list files.

---

## 2. MCP server start (requires pixi)

Test the wrapper script directly — this simulates exactly what Claude Code does on first start:

```bash
bash bin/serve.sh
# First run: pixi install runs (~30s), casatools installs if missing (~2–5 min, ~500 MB)
# Second run: both checks pass instantly, server starts in <1s
# Ctrl-C to exit
```

Expected: server starts and waits for JSON-RPC input — no traceback.

> **Note:** First start may take 2–5 minutes while casatools downloads (~500 MB).
> Subsequent starts are fast — the import check short-circuits the pip step.

---

## 3. Plugin install from upstream URL (end-to-end)

This is the path any user would take. It requires two steps: register the repo
as a marketplace once, then install the plugin from it.

```bash
# Step 1 — register the repo as a marketplace (once per machine)
claude plugin marketplace add https://github.com/skunkworks-ra/radio-analyst

# Step 2 — install the plugin
claude plugin install radio-analyst@radio-analyst
```

Verify the MCP servers registered:

```bash
claude mcp list
# ms-inspect, ms-modify, and ms-create should all appear
```

Trigger a tool call to confirm the server actually starts (requires a real MS path):

```bash
# In a Claude Code session:
# ms_observation_info(ms_path="/path/to/your.ms")
```

To uninstall cleanly:

```bash
claude plugin uninstall radio-analyst@radio-analyst
claude plugin marketplace remove radio-analyst
```

---

## 4. pyproject.toml extras — pip install path (optional)

Test the `[casa]` extras group in a fresh venv:

```bash
python -m venv /tmp/test-ms-inspect
source /tmp/test-ms-inspect/bin/activate
pip install ".[casa]"
pip show casatools casatasks
ms-inspect  # should start the stdio server
deactivate
rm -rf /tmp/test-ms-inspect
```

Expected: `casatools` and `casatasks` resolve and install; `ms-inspect` starts.

> **Note:** casatools wheels are platform-specific (Linux x86_64, macOS arm64).
> If your platform is not supported, use the pixi path instead.

---

## Known open question

`${CLAUDE_PLUGIN_ROOT}` substitution behaviour when installing from a git URL
needs to be confirmed. If the variable does not expand, the server will fail with
a pixi "manifest not found" error. In that case, fall back to the pip-based
entry points (`ms-inspect` / `ms-modify` / `ms-create` from `pip install ".[casa]"`,
§4 above), which do not rely on `${CLAUDE_PLUGIN_ROOT}`.
