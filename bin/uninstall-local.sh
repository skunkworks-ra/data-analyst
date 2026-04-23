#!/usr/bin/env bash
# Remove locally registered MCP servers (user scope).
# Run this before switching to the marketplace plugin install.
set -euo pipefail

SERVERS=(ms-inspect ms-modify ms-create)

for server in "${SERVERS[@]}"; do
    claude mcp remove --scope user "$server" 2>/dev/null && echo "Removed $server" || echo "$server not registered, skipping"
done
