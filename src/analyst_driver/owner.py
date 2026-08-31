"""Run ownership: who is driving a run, and whether that is still true.

``<run_root>/<run_key>/owner.json`` records the driver process that holds a
run, and the scheduler job it currently has in flight. It is written when a
driver takes a run and removed when the driver leaves it cleanly, so a file
left behind means the driver died there.

An advisory lock is deliberately NOT used. ``fcntl.flock`` releases itself
when the holder dies, which is the property we want, but it is unreliable
over NFS and the run root is expected on both local and NFS storage. A
recorded identity that the reader probes is portable; the cost is that the
probe must answer honestly when it cannot check.

Three probe answers, and the difference between the last two is load-bearing:

- ``alive``   the recorded process exists on this host, with the start time
              recorded for it. Proof. A caller must refuse.
- ``dead``    this host, and the process is gone or the pid was recycled.
- ``unknown`` the record names another host. A pid there is unverifiable from
              here, so we never call it dead.

The pid start time defeats pid reuse. Without it a recycled pid reports a
crashed driver as alive, and the run can never be resumed.

SLURM is reported separately, not folded into the driver answer. A job that
outlives its driver is normal for ``executor = "slurm"``, and ``sacct`` is
authoritative from any submit node where a pid is meaningless.
"""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path
from typing import Any

from analyst_driver.db import utcnow_iso

OWNER_FILENAME = "owner.json"


def _proc_start_ticks(pid: int) -> int | None:
    """Field 22 of ``/proc/<pid>/stat`` — process start time, in clock ticks.

    The comm field (field 2) may contain spaces and parentheses, so the line
    is cut after its last ``)`` before splitting. Returns None where /proc is
    absent (not Linux) or the process is gone; a None start time makes the
    probe fall back to pid existence alone.
    """
    try:
        with open(f"/proc/{pid}/stat") as fh:
            data = fh.read()
    except OSError:
        return None
    close = data.rfind(")")
    if close == -1:
        return None
    fields = data[close + 1 :].split()
    # fields[0] is state, which is field 3, so field 22 sits at index 19.
    try:
        return int(fields[19])
    except (IndexError, ValueError):
        return None


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by another user
    except OSError:
        return False
    return True


def owner_path(run_dir: str | os.PathLike) -> Path:
    return Path(run_dir) / OWNER_FILENAME


def write_owner(
    run_dir: str | os.PathLike,
    *,
    executor: str,
    job_id: str | None = None,
    pid: int | None = None,
    host: str | None = None,
) -> dict:
    """Claim a run for this process. Write-to-temp-then-rename, as the journal."""
    pid = os.getpid() if pid is None else pid
    record = {
        "host": host or socket.gethostname(),
        "pid": pid,
        "pid_start": _proc_start_ticks(pid),
        "executor": executor,
        "job_id": job_id,
        "updated_at": utcnow_iso(),
    }
    path = owner_path(run_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w") as fh:
        json.dump(record, fh, indent=1, sort_keys=True)
    os.replace(tmp, path)
    return record


def read_owner(run_dir: str | os.PathLike) -> dict | None:
    """The owner record, or None when absent or unreadable.

    A truncated file reads as None — absent and corrupt both mean the same
    thing to a caller, and neither is evidence that a driver is alive.
    """
    try:
        with open(owner_path(run_dir)) as fh:
            record = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    return record if isinstance(record, dict) else None


def set_owner_job(run_dir: str | os.PathLike, job_id: str | None) -> dict | None:
    """Record the job this driver now has in flight, keeping the same pid."""
    record = read_owner(run_dir)
    if record is None:
        return None
    record["job_id"] = job_id
    record["updated_at"] = utcnow_iso()
    path = owner_path(run_dir)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w") as fh:
        json.dump(record, fh, indent=1, sort_keys=True)
    os.replace(tmp, path)
    return record


def clear_owner(run_dir: str | os.PathLike) -> None:
    owner_path(run_dir).unlink(missing_ok=True)


def probe_owner(
    owner: dict | None,
    *,
    job_state: str | None = None,
    this_host: str | None = None,
    this_pid: int | None = None,
) -> dict[str, Any]:
    """Report whether the recorded driver and job are still alive.

    ``job_state`` is the executor's answer for the recorded job, or None when
    the caller did not or could not ask. It is reported beside the driver
    answer, never merged into it.
    """
    if owner is None:
        return {"driver": "free", "job": "none", "detail": "no owner file"}

    this_host = socket.gethostname() if this_host is None else this_host
    this_pid = os.getpid() if this_pid is None else this_pid

    host = owner.get("host")
    pid = owner.get("pid")
    recorded_start = owner.get("pid_start")

    if not isinstance(pid, int):
        driver, detail = "dead", "owner file records no pid"
    elif pid == this_pid and host == this_host:
        driver, detail = "self", f"owned by this process (pid {pid})"
    elif host != this_host:
        driver = "unknown"
        detail = (
            f"owned by pid {pid} on host {host!r}; this is {this_host!r}."
            " A pid on another host cannot be checked from here."
        )
    elif not _pid_exists(pid):
        driver, detail = "dead", f"pid {pid} is gone on {host}"
    else:
        live_start = _proc_start_ticks(pid)
        if recorded_start is not None and live_start is not None and live_start != recorded_start:
            driver = "dead"
            detail = f"pid {pid} was recycled (start time {live_start} != {recorded_start})"
        else:
            driver, detail = "alive", f"pid {pid} is running on {host}"

    if not owner.get("job_id"):
        job = "none"
    elif job_state is None:
        job = "unknown"
    elif job_state in ("pending", "running"):
        job = "alive"
    else:
        job = "dead"

    return {"driver": driver, "job": job, "detail": detail, "job_state": job_state}
