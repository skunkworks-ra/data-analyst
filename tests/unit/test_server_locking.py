"""
Unit tests for per-MS serialization in the ms-inspect server.

Concurrent _run_tool calls against the same first-arg path must not overlap;
calls against different paths may run concurrently.
"""

from __future__ import annotations

import asyncio
import time

from ms_inspect.server import _run_tool


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
