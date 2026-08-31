"""Executor contract + local, slurm, htcondor adapters (PLAN.md steps 4 and 7).

``submit(script, job_dir) -> handle``, ``poll(handle) -> pending|running|done|
failed``, ``exit_code(handle) -> int|None``. A handle is a plain dict the
journal can store. Nothing here knows anything about CASA.

The driver stays alive and waits for its jobs (user decision, 2026-08-31 —
a dead driver is the exception, not the operating mode). The local executor
is therefore synchronous: ``submit`` runs the script to completion and the
handle already carries the exit code. SLURM submits and is waited on by the
loop polling ``sacct``; a driver restarted after a crash re-polls a recorded
job ID and adopts the job. A local job cannot outlive the driver, so a
crashed local turn polls as ``failed``.
"""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path
from typing import Any, Protocol

from analyst_driver.db import utcnow_iso


class Executor(Protocol):
    kind: str

    def submit(self, script: str | Path, job_dir: str | Path) -> dict[str, Any]: ...

    def poll(self, handle: dict[str, Any]) -> str: ...

    def exit_code(self, handle: dict[str, Any]) -> int | None: ...


class LocalExecutor:
    kind = "local"

    def __init__(self, runner: str = "python3", timeout: float | None = None):
        self.runner = runner
        self.timeout = timeout

    def submit(self, script: str | Path, job_dir: str | Path) -> dict[str, Any]:
        script = Path(script)
        job_dir = Path(job_dir)
        job_dir.mkdir(parents=True, exist_ok=True)
        log = job_dir / "job.log"
        submitted_at = utcnow_iso()
        with open(log, "w") as fh:
            proc = subprocess.run(
                [*shlex.split(self.runner), str(script)],
                stdout=fh,
                stderr=subprocess.STDOUT,
                cwd=script.parent,
                timeout=self.timeout,
            )
        return {
            "executor": self.kind,
            "handle": "local:sync",
            "exit_code": proc.returncode,
            "log_paths": [str(log)],
            "submitted_at": submitted_at,
            "finished_at": utcnow_iso(),
        }

    def poll(self, handle: dict[str, Any]) -> str:
        code = handle.get("exit_code")
        if code is None:
            # A recorded local job with no exit code was interrupted by a
            # driver crash; the job died with the driver.
            return "failed"
        return "done" if code == 0 else "failed"

    def exit_code(self, handle: dict[str, Any]) -> int | None:
        return handle.get("exit_code", -1)


#: sacct State -> executor state. COMPLETED alone counts as done.
_SLURM_STATES = {
    "PENDING": "pending",
    "REQUEUED": "pending",
    "RESIZING": "running",
    "RUNNING": "running",
    "COMPLETING": "running",
    "SUSPENDED": "running",
    "COMPLETED": "done",
}


class SlurmExecutor:
    kind = "slurm"

    def __init__(self, config: Any | None = None):
        if config is None:
            from ms_modify.slurm import SlurmConfig

            config = SlurmConfig()
        self.config = config

    def submit(self, script: str | Path, job_dir: str | Path) -> dict[str, Any]:
        from ms_modify.slurm import build_sbatch

        job_dir = Path(job_dir)
        job_dir.mkdir(parents=True, exist_ok=True)
        sbatch_path = build_sbatch(script, job_dir, self.config)
        out = subprocess.run(
            ["sbatch", "--parsable", sbatch_path],
            capture_output=True,
            text=True,
            check=True,
            cwd=job_dir,
        )
        job_id = out.stdout.strip().split(";")[0]
        job_name = Path(script).stem
        return {
            "executor": self.kind,
            "handle": f"slurm:{job_id}",
            "job_id": job_id,
            "sbatch": str(sbatch_path),
            "log_paths": [
                str(job_dir / f"{job_name}_{job_id}.out"),
                str(job_dir / f"{job_name}_{job_id}.err"),
            ],
            "submitted_at": utcnow_iso(),
        }

    def _sacct(self, job_id: str) -> tuple[str, str] | None:
        out = subprocess.run(
            ["sacct", "-j", str(job_id), "-n", "-P", "-X", "--format=State,ExitCode"],
            capture_output=True,
            text=True,
        )
        if out.returncode != 0:
            return None
        lines = out.stdout.strip().splitlines()
        if not lines:
            return None
        state, _, exit_code = lines[0].partition("|")
        return state.split()[0].rstrip("+"), exit_code

    def poll(self, handle: dict[str, Any]) -> str:
        row = self._sacct(handle["job_id"])
        if row is None:
            return "pending"  # sacct lag right after submission
        state, _ = row
        return _SLURM_STATES.get(state, "failed")

    def exit_code(self, handle: dict[str, Any]) -> int | None:
        row = self._sacct(handle["job_id"])
        if row is None:
            return None
        state, exit_code = row
        if _SLURM_STATES.get(state, "failed") in ("pending", "running"):
            return None
        try:
            return int(exit_code.partition(":")[0])
        except ValueError:
            return -1


class HTCondorExecutor:
    """Placeholder — needs a real submit node to write and test (PLAN.md step 7)."""

    kind = "htcondor"

    def submit(self, script: str | Path, job_dir: str | Path) -> dict[str, Any]:
        raise NotImplementedError("htcondor executor is not implemented yet")

    def poll(self, handle: dict[str, Any]) -> str:
        raise NotImplementedError("htcondor executor is not implemented yet")

    def exit_code(self, handle: dict[str, Any]) -> int | None:
        raise NotImplementedError("htcondor executor is not implemented yet")


def make_executor(kind: str, **kwargs: Any) -> Executor:
    if kind == "local":
        return LocalExecutor(**kwargs)
    if kind == "slurm":
        return SlurmExecutor(**kwargs)
    if kind == "htcondor":
        return HTCondorExecutor()
    raise ValueError(f"unknown executor kind {kind!r}")
