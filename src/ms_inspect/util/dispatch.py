"""
util/dispatch.py — Shared MCP tool dispatch for all three servers.

Every `@mcp.tool` coroutine in ms_inspect, ms_modify, and ms_create funnels
through `run_tool()`. It provides three things that must not diverge between
the read, write, and ingest servers:

1. **Off-loop execution.** Tool functions are synchronous and can run for
   minutes (a bandpass solve, a FLAG column read). `asyncio.to_thread` keeps
   them off the event loop so the server stays responsive.
2. **Per-path serialization.** CASA table access is not thread-safe for
   concurrent opens of the same MS within one process.
3. **A uniform error envelope.** RadioMSError becomes the documented error
   dict (DESIGN.md §7.2); anything else propagates to FastMCP.

No CASA dependency.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading

from ms_inspect.exceptions import RadioMSError
from ms_inspect.util.formatting import compact_fields

# ---------------------------------------------------------------------------
# Per-resource locks
# ---------------------------------------------------------------------------

# CASA table access is not thread-safe for concurrent opens of the same MS
# within one process (observed: >=2 simultaneous opens can crash the server,
# with no in-session recovery). CASA's own locking is per-MS, so tools against
# *different* MSes may still run concurrently.
_PATH_LOCKS: dict[str, threading.Lock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


def path_lock(path: str) -> threading.Lock:
    """Return the process-wide lock for `path`, creating it on first use."""
    # realpath: the same MS may be referenced via symlinked aliases
    # (e.g. /users/... -> /lustre/...); those must share one lock.
    path = os.path.realpath(path)
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(path)
        if lock is None:
            lock = threading.Lock()
            _PATH_LOCKS[path] = lock
        return lock


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def run_tool_sync(tool_fn, *args, **kwargs) -> str:
    """
    Run `tool_fn` and JSON-encode its result. Called from a worker thread.

    RadioMSError is converted to the documented error envelope. Any other
    exception is re-raised for FastMCP to surface as a tool error.
    """
    try:
        result = tool_fn(*args, **kwargs)
        return json.dumps(compact_fields(result), separators=(",", ":"), default=str)
    except RadioMSError as e:
        return json.dumps(e.to_dict(), separators=(",", ":"), default=str)


async def run_tool(tool_fn, *args, _lock_path: str | None = None, **kwargs) -> str:
    """
    Execute a tool function off the event loop thread; return JSON-encoded result.

    Concurrent calls against the same resource path are serialized via a per-path
    lock. By default the resource is the first positional argument (MS, ASDM,
    image, or caltable), which is the convention every tool `run()` follows.
    Pass `_lock_path` explicitly for the few tools whose first argument is not
    the resource (e.g. `reduction_log.run(action, workdir, ...)`).

    Tools with neither a positional argument nor `_lock_path` run unserialized.
    """
    lock_key = _lock_path if _lock_path is not None else (str(args[0]) if args else None)

    def _locked() -> str:
        if lock_key is None:
            return run_tool_sync(tool_fn, *args, **kwargs)
        with path_lock(lock_key):
            return run_tool_sync(tool_fn, *args, **kwargs)

    return await asyncio.to_thread(_locked)
