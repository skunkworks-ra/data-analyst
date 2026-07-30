"""
Unit tests for the shared tool dispatch used by all three MCP servers.

Concurrent _run_tool calls against the same resource path must not overlap;
calls against different paths may run concurrently. The write servers
(ms_modify, ms_create) must get the same guarantee as ms_inspect — those are
the ones where a concurrent CASA open corrupts rather than merely crashes.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from ms_create.server import _run_tool as create_run_tool
from ms_inspect.server import _run_tool
from ms_inspect.util.dispatch import run_tool
from ms_modify.server import _run_tool as modify_run_tool


class OverlapRecorder:
    def __init__(self):
        self.active = 0
        self.max_active = 0

    def __call__(self, ms_path, duration=0.05):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        time.sleep(duration)
        self.active -= 1
        return {"ms_path": ms_path}


def test_same_path_serialized():
    rec = OverlapRecorder()

    async def go():
        await asyncio.gather(*[_run_tool(rec, "/data/a.ms") for _ in range(4)])

    asyncio.run(go())
    assert rec.max_active == 1


def test_different_paths_concurrent():
    rec = OverlapRecorder()

    async def go():
        await asyncio.gather(
            _run_tool(rec, "/data/a.ms"),
            _run_tool(rec, "/data/b.ms"),
        )

    asyncio.run(go())
    assert rec.max_active == 2


@pytest.mark.parametrize(
    "dispatch",
    [_run_tool, modify_run_tool, create_run_tool],
    ids=["inspect", "modify", "create"],
)
def test_all_servers_share_one_dispatch(dispatch):
    """All three servers must serialize identically — same object, same lock table."""
    assert dispatch is run_tool

    rec = OverlapRecorder()

    async def go():
        await asyncio.gather(*[dispatch(rec, "/data/shared.ms") for _ in range(3)])

    asyncio.run(go())
    assert rec.max_active == 1


def test_lock_shared_across_servers():
    """A read on an MS must not run while a write on the same MS is in flight."""
    rec = OverlapRecorder()

    async def go():
        await asyncio.gather(
            modify_run_tool(rec, "/data/same.ms"),
            _run_tool(rec, "/data/same.ms"),
            create_run_tool(rec, "/data/same.ms"),
        )

    asyncio.run(go())
    assert rec.max_active == 1


def test_explicit_lock_path_overrides_first_arg():
    """Tools whose first arg is not the resource (reduction_log) lock on _lock_path."""
    rec = OverlapRecorder()

    def tool(action, workdir, duration=0.05):
        return rec(workdir, duration=duration)

    async def go():
        # Different first args ('append' vs 'render'), same workdir → must serialize.
        await asyncio.gather(
            run_tool(tool, "append", "/work/run1", _lock_path="/work/run1"),
            run_tool(tool, "render", "/work/run1", _lock_path="/work/run1"),
        )

    asyncio.run(go())
    assert rec.max_active == 1


def test_does_not_block_event_loop():
    """A long tool call must not stall the event loop — other coroutines keep running."""
    ticks = 0

    async def ticker():
        nonlocal ticks
        for _ in range(20):
            await asyncio.sleep(0.01)
            ticks += 1

    def slow(ms_path):
        time.sleep(0.2)
        return {"ms_path": ms_path}

    async def go():
        await asyncio.gather(modify_run_tool(slow, "/data/slow.ms"), ticker())

    asyncio.run(go())
    assert ticks == 20
