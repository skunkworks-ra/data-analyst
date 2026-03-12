# Testing Guide — ms-inspect Claude Code Plugin

Run these in order. Steps 1 and 2 give fast feedback with no side effects.
Step 3 is the real end-to-end test.

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
claude plugin marketplace add https://github.com/skunkworks-ra/data-analyst --scope user

# Step 2 — install the plugin
claude plugin install ms-inspect --scope user
```

Verify the MCP server registered:

```bash
claude mcp list
# ms-inspect should appear
```

Trigger a tool call to confirm the server actually starts (requires a real MS path):

```bash
# In a Claude Code session:
# ms_observation_info(ms_path="/path/to/your.ms")
```

To uninstall cleanly:

```bash
claude plugin uninstall ms-inspect --scope user
claude plugin marketplace remove ms-inspect --scope user
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
a pixi "manifest not found" error. In that case, fall back to Option D in README
(pip-based `ms-inspect` entry point, which does not rely on `${CLAUDE_PLUGIN_ROOT}`).
