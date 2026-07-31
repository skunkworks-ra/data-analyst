#!/usr/bin/env bash
set -euo pipefail

LOG_PREFIX="[ms-create]"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SRC_MANIFEST="$REPO_ROOT/pixi.toml"
SRC_LOCK="$REPO_ROOT/pixi.lock"

# --- Step 0: pixi must be on PATH -------------------------------------------
if ! command -v pixi >/dev/null 2>&1; then
    {
        echo "$LOG_PREFIX pixi is not on PATH — cannot start the MCP server."
        echo "$LOG_PREFIX Install pixi from https://prefix.dev, then retry."
        echo "$LOG_PREFIX Alternatively, skip pixi entirely: run"
        echo "$LOG_PREFIX     pip install \".[casa]\""
        echo "$LOG_PREFIX from the repository root — this provides the 'ms-create' console"
        echo "$LOG_PREFIX script directly (see TESTS.md section 4 / pyproject.toml [project.scripts])."
    } >&2
    exit 1
fi

# --- Step 1: pick the manifest location -------------------------------------
# ${CLAUDE_PLUGIN_ROOT} (and therefore $REPO_ROOT when installed as a plugin)
# is an ephemeral, versioned cache that Claude Code garbage-collects on the
# next plugin update — see the plugin docs on CLAUDE_PLUGIN_ROOT. Building the
# ~500 MB casatools pixi environment there means every version bump re-pays
# the full download. ${CLAUDE_PLUGIN_DATA} is the persistent counterpart, so
# when it is set we build and keep the pixi environment there instead, shared
# across all three ms-inspect/ms-modify/ms-create servers.
#
# pixi has no flag/env var to relocate .pixi/ next to a *different* manifest
# path; it always resolves .pixi/ next to the (canonicalized) manifest file.
# The only mechanism that actually works is to keep a real (non-symlink) copy
# of pixi.toml + pixi.lock inside CLAUDE_PLUGIN_DATA, with the local editable
# `path = "."` dependency rewritten to an absolute path back to REPO_ROOT so
# the package still resolves to the real source tree. Verified empirically
# with `pixi info --manifest-path <copy>` showing "Prefix location" under the
# copy's directory once this rewrite is done.
if [[ -n "${CLAUDE_PLUGIN_DATA:-}" ]]; then
    DATA_DIR="$CLAUDE_PLUGIN_DATA/pixi-env"
    mkdir -p "$DATA_DIR"
    MANIFEST="$DATA_DIR/pixi.toml"
    DATA_LOCK="$DATA_DIR/pixi.lock"
    SNAPSHOT_MANIFEST="$DATA_DIR/.source-pixi.toml"
    SNAPSHOT_LOCK="$DATA_DIR/.source-pixi.lock"
    SNAPSHOT_ROOT="$DATA_DIR/.source-repo-root"
    LOCK_FILE="$DATA_DIR/.rebuild.lock"

    # Serialize the copy/rewrite step: ms-inspect, ms-modify, and ms-create
    # all share this DATA_DIR and may be launched concurrently.
    rebuild_copy() {
        cp "$SRC_MANIFEST" "$MANIFEST"
        cp "$SRC_LOCK" "$DATA_LOCK"
        # Rewrite the editable local-path dependency to an absolute path so
        # the copy (living under CLAUDE_PLUGIN_DATA) still finds the real
        # source tree (living under CLAUDE_PLUGIN_ROOT).
        sed -i "s#path = \"\\.\"#path = \"$REPO_ROOT\"#" "$MANIFEST"
        cp "$SRC_MANIFEST" "$SNAPSHOT_MANIFEST"
        cp "$SRC_LOCK" "$SNAPSHOT_LOCK"
        printf '%s' "$REPO_ROOT" > "$SNAPSHOT_ROOT"
    }

    if command -v flock >/dev/null 2>&1; then
        exec 9>"$LOCK_FILE"
        flock 9
    fi

    # Only rebuild the copy when pixi.toml or pixi.lock actually changed
    # since the last time we mirrored them (rebuild trigger, not on every
    # start).
    # REPO_ROOT must be part of the trigger, not just file content. On a plugin
    # update CLAUDE_PLUGIN_ROOT changes to a new versioned directory while
    # pixi.toml and pixi.lock stay byte-identical, so a content-only check would
    # pass and leave the rewritten `path = "<old root>"` in the copied manifest
    # pointing at a directory Claude Code garbage-collects. That is the exact
    # breakage this whole block exists to avoid.
    if [[ ! -f "$MANIFEST" ]] \
        || ! cmp -s "$SRC_MANIFEST" "$SNAPSHOT_MANIFEST" \
        || ! cmp -s "$SRC_LOCK" "$SNAPSHOT_LOCK" \
        || [[ ! -f "$SNAPSHOT_ROOT" ]] \
        || [[ "$(cat "$SNAPSHOT_ROOT")" != "$REPO_ROOT" ]]; then
        echo "$LOG_PREFIX manifest or plugin root changed — refreshing persistent environment copy..." >&2
        rebuild_copy
    fi

    if command -v flock >/dev/null 2>&1; then
        flock -u 9
    fi
else
    # Local development: behave exactly as before — environment lives next
    # to the manifest in the repo clone.
    MANIFEST="$SRC_MANIFEST"
fi

# --- Step 2: ensure pixi environment exists (idempotent, fast after first run)
pixi install --manifest-path "$MANIFEST" --quiet

# --- Step 3: ensure casatools is installed (import check avoids pip overhead on every start)
if ! pixi run --manifest-path "$MANIFEST" python -c "import casatools" 2>/dev/null; then
    echo "$LOG_PREFIX Installing CASA tools (first run only)..." >&2
    pixi run --manifest-path "$MANIFEST" python -m pip install casatools casatasks --quiet
fi

# --- Step 4: replace this shell process with the MCP server (clean process tree)
exec pixi run --manifest-path "$MANIFEST" serve-create
