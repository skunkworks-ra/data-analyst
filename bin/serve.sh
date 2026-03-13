#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MANIFEST="$SCRIPT_DIR/../pixi.toml"

# Step 1: Ensure pixi environment exists (idempotent, fast after first run)
pixi install --manifest-path "$MANIFEST" --quiet

# Step 2: Ensure casatools is installed (import check avoids pip overhead on every start)
if ! pixi run --manifest-path "$MANIFEST" python -c "import casatools" 2>/dev/null; then
    echo "[ms-inspect] Installing CASA tools (first run only)..." >&2
    pixi run --manifest-path "$MANIFEST" python -m pip install casatools casatasks --quiet
fi

# Step 3: Replace this shell process with the MCP server (clean process tree)
exec pixi run --manifest-path "$MANIFEST" serve
