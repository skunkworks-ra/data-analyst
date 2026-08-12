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

# What the run was pointed at.
KIND_MS = "ms"
KIND_ASDM = "asdm"

# The MS roles a tool may declare in whitelist.yaml, and what each falls back
# to when it does not exist yet.
ROLE_RAW = "raw"
ROLE_CALIBRATORS = "calibrators"
ROLE_TARGET = "target"

_ROLE_FALLBACK: dict[str, tuple[str, ...]] = {
    # The raw MS is the one thing always present once an import has happened.
    ROLE_RAW: (ROLE_RAW,),
    # Calibration wants calibrators.ms, but before preflag has split it the
    # calibrator fields are still in the raw MS.
    ROLE_CALIBRATORS: (ROLE_CALIBRATORS, ROLE_RAW),
    # Imaging wants the target MS. Without a target split the target fields
    # live in the raw MS, with their corrected data already applied.
    ROLE_TARGET: (ROLE_TARGET, ROLE_RAW),
}


@dataclasses.dataclass
class Pending:
    """A job the executor is running right now."""

    job_id: str
    step: int
    tool: str
    submitted_utc: str
    step_dir: str
    # What the tool said its script would create, captured at generation time.
    # Checked at harvest: an output that never appeared is a failed step.
    planned_outputs: list[dict[str, Any]] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class RunState:
    run_id: str
    goal: str
    recipe: str
    started_utc: str
    # What the run was pointed at, and which kind it turned out to be.
    # Fixed at init and never rewritten.
    input_path: str = ""
    input_kind: str = KIND_MS  # KIND_MS or KIND_ASDM
    # Every MS this run knows about, keyed by role. Filled in as steps declare
    # and then produce them. There is no single "the MS": calibration works on
    # calibrators, applycal writes the target fields, imaging reads the target.
    ms_registry: dict[str, str] = dataclasses.field(default_factory=dict)
    status: str = STATUS_IDLE
    step: int = 0
    pending: Pending | None = None
    refusals_this_step: int = 0
    # sha256 of (tool, params) per completed step — the cycle detector.
    call_digests: list[str] = dataclasses.field(default_factory=list)
    # Tools that have completed OK at least once. Feeds the step_done precondition.
    tools_done: list[str] = dataclasses.field(default_factory=list)
    park_reason: str = ""

    # -- the MS registry -------------------------------------------------

    def ms_for(self, role: str) -> str:
        """Resolve a tool's declared ms_role to a path.

        The roles are a chain, not independent slots. A step that wants the
        target MS before any split has happened legitimately gets the raw MS,
        because that is where the target fields still live. Falling back that
        way is what lets one recipe serve both a split and an unsplit run.
        """
        for candidate in _ROLE_FALLBACK.get(role, (role,)):
            if self.ms_registry.get(candidate):
                return self.ms_registry[candidate]
        return ""

    def record_ms(self, role: str, path: str) -> None:
        self.ms_registry[role] = path

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


def processed_dir(run_dir: Path) -> Path:
    """The single workdir every tool is given.

    Every data product lands here: calibrators.ms, the caltables, the images.
    Per-step workdirs were tried and are wrong — ms_apply_preflag splits
    calibrators at step one, and ms_workflow_status reads a single flat
    workdir by name, so scattering products across step directories separates
    an MS from its own caltables and breaks that tool. Step directories hold
    the script and the logs, which is the provenance trail, and nothing else.
    """
    return run_dir / "processed"


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
