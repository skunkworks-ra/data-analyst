#!/usr/bin/env bash
# Register MCP servers directly against the local pixi environment (user scope).
# Detects and removes a plugin-managed install first to avoid duplicate server names.
# Run after: pixi install && pixi run pip install casatools casatasks
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PIXI_ENV="$REPO_ROOT/.pixi/envs/default/bin"
SERVERS=(ms-inspect ms-modify ms-create)

if [[ ! -x "$PIXI_ENV/ms-inspect" ]]; then
    echo "error: pixi environment not found." >&2
    echo "Run: pixi install && pixi run pip install casatools casatasks" >&2
    exit 1
fi

# Detect plugin install by reading installed_plugins.json
plugin_ids=$(python3 -c "
import json, pathlib, sys
p = pathlib.Path.home() / '.claude/plugins/installed_plugins.json'
if not p.exists():
    sys.exit()
data = json.loads(p.read_text())
for pid in data.get('plugins', {}):
    if pid.startswith('radio-analyst@') or pid.startswith('ms-inspect@'):
        print(pid)
" 2>/dev/null || true)

if [[ -n "$plugin_ids" ]]; then
    echo "Detected plugin install: $plugin_ids"
    echo "Uninstalling before registering local dev servers..."
    while IFS= read -r pid; do
        claude plugin uninstall "$pid" --scope user
    done <<< "$plugin_ids"
fi

for server in "${SERVERS[@]}"; do
    claude mcp remove --scope user "$server" 2>/dev/null || true
    claude mcp add --scope user "$server" "$PIXI_ENV/$server" -e RADIO_MCP_TRANSPORT=stdio
done

echo "Registered ${SERVERS[*]} (user scope). Restart Claude Code."
