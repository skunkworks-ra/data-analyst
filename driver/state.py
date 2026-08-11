"""
state.py — run.json, the driver's whole memory.

Everything here is derivable from the run directory. run.json is a cache, not
the truth: delete it and `driver.py rebuild` reconstructs it from steps/ and
decisions/. Nothing in the loop may depend on state that exists only in RAM,
because the driver exits between steps by design.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
from pathlib import Path
from typing import Any

STATE_NAME = "run.json"

# Terminal states. A tick that sees one of these does nothing and exits.
STATUS_IDLE = "IDLE"
STATUS_RUNNING = "RUNNING"
STATUS_DONE = "DONE"
STATUS_NEEDS_HUMAN = "NEEDS_HUMAN"
STATUS_STOPPED = "STOPPED"

TERMINAL = {STATUS_DONE, STATUS_NEEDS_HUMAN, STATUS_STOPPED}


@dataclasses.dataclass
class Pending:
    """A job the executor is running right now."""

    job_id: str
    step: int
    tool: str
    submitted_utc: str
    step_dir: str


@dataclasses.dataclass
class RunState:
    run_id: str
    goal: str
    recipe: str
    active_ms: str
    started_utc: str
    status: str = STATUS_IDLE
    step: int = 0
    pending: Pending | None = None
    refusals_this_step: int = 0
    # sha256 of (tool, params) per completed step — the cycle detector.
    call_digests: list[str] = dataclasses.field(default_factory=list)
    # Tools that have completed OK at least once. Feeds the step_done precondition.
    tools_done: list[str] = dataclasses.field(default_factory=list)
    park_reason: str = ""

    # -- serialisation ---------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        if self.pending is None:
            d["pending"] = None
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RunState:
        pending = d.get("pending")
        d = dict(d)
        d["pending"] = Pending(**pending) if pending else None
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


def state_path(run_dir: Path) -> Path:
    return run_dir / STATE_NAME


def load(run_dir: Path) -> RunState:
    return RunState.from_dict(json.loads(state_path(run_dir).read_text()))


def save(run_dir: Path, st: RunState) -> None:
    """Write run.json atomically.

    The driver can be killed at any moment — including between the executor
    submitting a job and the state recording its id. A partial run.json would
    orphan that job, so the write is rename-based and therefore all-or-nothing.
    """
    path = state_path(run_dir)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(st.to_dict(), indent=2) + "\n")
    os.replace(tmp, path)


# -- helpers ------------------------------------------------------------


def call_digest(tool: str, params: dict[str, Any]) -> str:
    """Stable hash of one tool call, for cycle detection."""
    blob = json.dumps({"tool": tool, "params": params}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def step_dir(run_dir: Path, step: int, tool: str = "") -> Path:
    name = f"{step:03d}" + (f"-{tool}" if tool else "")
    if not tool:
        # Locate an existing directory by its numeric prefix.
        matches = sorted((run_dir / "steps").glob(f"{step:03d}-*"))
        if matches:
            return matches[0]
    return run_dir / "steps" / name


def decision_path(run_dir: Path, step: int) -> Path:
    return run_dir / "decisions" / f"{step:03d}.json"
