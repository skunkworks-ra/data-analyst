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
   dict (design_docs/DESIGN.md §7.2); anything else propagates to FastMCP.

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


def _is_plausible_lock_key(key: str) -> bool:
    """
    True if `key` can be a resource path — it exists, or it is path-shaped.

    Deliberately permissive about non-existence: tools validate their own paths
    and return a proper error envelope for a typo. What this rejects is a key
    that was never a path at all, e.g. an action verb passed as the first
    positional argument by a tool whose author did not know about `_lock_path`.
    """
    if os.path.exists(key):
        return True
    return os.sep in key or (os.altsep is not None and os.altsep in key)


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

    The convention is checked, not assumed: a key that is neither an existing
    path nor path-shaped raises, rather than silently locking on something
    meaningless. Failing open would drop serialization for that tool and surface
    later as an intermittent CASA crash under concurrency — far harder to
    diagnose than the error below. A path-shaped key that does not exist is
    passed through untouched, so a mistyped MS path still reaches the tool's own
    validation and returns the documented error envelope.
    """
    lock_key = _lock_path if _lock_path is not None else (str(args[0]) if args else None)

    if lock_key is not None and not _is_plausible_lock_key(lock_key):
        source = "_lock_path" if _lock_path is not None else "first positional argument"
        raise ValueError(
            f"run_tool: lock key from {source} is not a resource path: {lock_key!r} "
            f"(tool {getattr(tool_fn, '__module__', '?')}."
            f"{getattr(tool_fn, '__qualname__', tool_fn)}). Per-path serialization "
            "needs the MS/ASDM/image/caltable path; pass _lock_path explicitly for "
            "tools whose first argument is not the resource."
        )

    def _locked() -> str:
        if lock_key is None:
            return run_tool_sync(tool_fn, *args, **kwargs)
        with path_lock(lock_key):
            return run_tool_sync(tool_fn, *args, **kwargs)

    return await asyncio.to_thread(_locked)
