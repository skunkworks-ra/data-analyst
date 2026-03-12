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

## 2. MCP server start (requires pixi + casatools)

Confirm the server entry point works before testing the plugin machinery:

```bash
pixi install
pixi run pip install casatools casatasks

echo '{}' | pixi run serve
# Should block waiting for JSON-RPC input (Ctrl-C to exit)
# A crash here means casatools isn't installed or the entry point is broken
```

Expected: server starts and waits — no traceback.

---

## 3. Plugin install from upstream URL (end-to-end)

Install directly from GitHub — this is the path any user would take:

```bash
claude plugin install git@github.com:skunkworks-ra/data-analyst.git --scope local
```

Or via HTTPS if SSH keys are not configured:

```bash
claude plugin install https://github.com/skunkworks-ra/data-analyst.git --scope local
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
claude plugin uninstall ms-inspect --scope local
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
