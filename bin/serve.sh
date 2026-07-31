#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
MANIFEST="$REPO_ROOT/pixi.toml"

# Step 0: pixi is required. Without this guard `set -euo pipefail` kills the
# server on the bare "pixi: command not found" and Claude Code shows only
# "server failed". stderr only — stdout is the JSON-RPC stream.
if ! command -v pixi >/dev/null 2>&1; then
    cat >&2 <<EOF
[ms-inspect] pixi is not on PATH, so this server cannot start.

Install pixi (https://prefix.dev), then restart the MCP server:
    curl -fsSL https://pixi.sh/install.sh | bash

Or run the server from a Python >=3.12 environment without pixi:
    pip install "$REPO_ROOT[casa]"
    ms-inspect
EOF
    exit 1
fi

# Step 1: Ensure pixi environment exists (idempotent, fast after first run)
pixi install --manifest-path "$MANIFEST" --quiet

# Step 2: Ensure casatools is installed (import check avoids pip overhead on every start)
if ! pixi run --manifest-path "$MANIFEST" python -c "import casatools" 2>/dev/null; then
    echo "[ms-inspect] Installing CASA tools (first run only)..." >&2
    pixi run --manifest-path "$MANIFEST" python -m pip install casatools casatasks --quiet
fi

# Step 3: Replace this shell process with the MCP server (clean process tree)
exec pixi run --manifest-path "$MANIFEST" serve
