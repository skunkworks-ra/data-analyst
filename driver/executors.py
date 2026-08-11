"""
executors.py — where a generated script actually runs.

One interface, three implementations. The driver never learns which is active,
so moving a run from corrino to SLURM is a one-line config change.

    submit(script, step_dir, name) -> job_id
    poll(job_id, step_dir)         -> RUNNING | DONE | FAILED
    exit_code(step_dir)            -> int | None

Job state lives in FILES, not in process memory. The driver exits between
ticks, so a pid held in RAM would be lost. Every executor writes `rc` into the
step directory when the job ends, and `poll` reads it back. That also means a
driver restart picks a running job back up with no special case.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

RUNNING = "RUNNING"
DONE = "DONE"
FAILED = "FAILED"

RC_NAME = "rc"
STDOUT_NAME = "stdout"
STDERR_NAME = "stderr"


def _read_rc(step_dir: Path) -> int | None:
    rc_file = step_dir / RC_NAME
    if not rc_file.exists():
        return None
    text = rc_file.read_text().strip()
    return int(text) if text.lstrip("-").isdigit() else None


class LocalExecutor:
    """Run the script on this machine, detached from the driver.

    start_new_session puts the job in its own session, so it survives the
    driver exiting between ticks. That is the whole point of the loop: nothing
    long-lived holds the job open. Do not reintroduce a `setsid` prefix here —
    Popen already does the same thing, and setsid does not exist on macOS.
    """

    kind = "local"

    def __init__(self, python: str = "", env: dict[str, str] | None = None) -> None:
        self.python = python or shutil.which("python") or "python"
        self.env = env or {}

    def submit(self, script: Path, step_dir: Path, name: str) -> str:
        rc = step_dir / RC_NAME
        if rc.exists():
            rc.unlink()
        wrapper = step_dir / "wrapper.sh"
        wrapper.write_text(
            "#!/bin/sh\n"
            f"cd {step_dir!s}\n"
            f"{self.python} {script.name} > {STDOUT_NAME} 2> {STDERR_NAME}\n"
            f"echo $? > {RC_NAME}\n"
        )
        wrapper.chmod(0o755)

        env = {**os.environ, **self.env}
        proc = subprocess.Popen(  # noqa: S603 - script path is driver-generated
            ["sh", str(wrapper)],
            cwd=str(step_dir),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return f"local:{proc.pid}"

    def poll(self, job_id: str, step_dir: Path) -> str:
        rc = _read_rc(step_dir)
        if rc is None:
            return RUNNING
        return DONE if rc == 0 else FAILED

    def exit_code(self, step_dir: Path) -> int | None:
        return _read_rc(step_dir)


class SlurmExecutor:
    """sbatch the script and poll sacct.

    Reuses ms_modify.slurm for the sbatch body so the driver and the existing
    skill path generate the same batch files.
    """

    kind = "slurm"

    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg

    def submit(self, script: Path, step_dir: Path, name: str) -> str:
        rc = step_dir / RC_NAME
        if rc.exists():
            rc.unlink()

        c = self.cfg
        account = c.get("account") or ""
        if not account:
            try:
                from ms_modify.slurm import detect_account

                account = detect_account() or ""
            except ImportError:
                account = ""

        lines = [
            "#!/bin/bash",
            f"#SBATCH --job-name={name}",
            f"#SBATCH --output={step_dir}/{STDOUT_NAME}",
            f"#SBATCH --error={step_dir}/{STDERR_NAME}",
            f"#SBATCH --nodes={c.get('nodes', 1)}",
            f"#SBATCH --ntasks={c.get('ntasks', 1)}",
            f"#SBATCH --cpus-per-task={c.get('cpus_per_task', 8)}",
            f"#SBATCH --mem={c.get('mem', '60G')}",
            f"#SBATCH --time={c.get('time', '08:00:00')}",
        ]
        if account:
            lines.append(f"#SBATCH --account={account}")
        if c.get("partition"):
            lines.append(f"#SBATCH --partition={c['partition']}")
        lines += [f"module load {m}" for m in c.get("modules", [])]
        # The rc file is what poll() trusts. Write it inside the job, so a job
        # killed by the scheduler still leaves a non-zero code behind.
        lines += [
            f"cd {step_dir!s}",
            f"{c.get('container_cmd', '')} python {script.name}",
            f"echo $? > {RC_NAME}",
        ]

        sbatch_file = step_dir / "job.sbatch"
        sbatch_file.write_text("\n".join(lines) + "\n")

        out = subprocess.run(  # noqa: S603
            ["sbatch", "--parsable", str(sbatch_file)],
            capture_output=True,
            text=True,
            check=True,
        )
        return f"slurm:{out.stdout.strip().split(';')[0]}"

    def poll(self, job_id: str, step_dir: Path) -> str:
        rc = _read_rc(step_dir)
        if rc is not None:
            return DONE if rc == 0 else FAILED

        jid = job_id.split(":", 1)[1]
        out = subprocess.run(  # noqa: S603
            ["sacct", "-n", "-X", "-j", jid, "-o", "State"],
            capture_output=True,
            text=True,
        )
        state = out.stdout.strip().split("\n")[0].strip() if out.stdout.strip() else ""
        if state.startswith(("PENDING", "RUNNING", "CONFIGURING", "COMPLETING")):
            return RUNNING
        if state.startswith("COMPLETED"):
            # sacct says done but rc never appeared — the job died before its
            # last line. Treat that as a failure rather than a silent success.
            return FAILED
        if state == "":
            return RUNNING  # too early for sacct to know the job
        return FAILED

    def exit_code(self, step_dir: Path) -> int | None:
        return _read_rc(step_dir)


class DryExecutor:
    """Pretend the job ran and succeeded, at once.

    For exercising the loop, the brief, the validator and the ledger without
    burning hours of CASA. It writes the script but never runs it.
    """

    kind = "dry"

    def submit(self, script: Path, step_dir: Path, name: str) -> str:
        (step_dir / STDOUT_NAME).write_text(f"DRY RUN — {script.name} was not executed.\n")
        (step_dir / STDERR_NAME).write_text("")
        (step_dir / RC_NAME).write_text("0\n")
        return "dry:0"

    def poll(self, job_id: str, step_dir: Path) -> str:
        return DONE

    def exit_code(self, step_dir: Path) -> int | None:
        return 0


def build(cfg: dict):
    """Return the executor named by config.toml [executor].kind."""
    kind = cfg.get("kind", "local")
    if kind == "local":
        return LocalExecutor()
    if kind == "slurm":
        return SlurmExecutor(cfg.get("slurm", {}))
    if kind == "dry":
        return DryExecutor()
    raise ValueError(f"unknown executor kind: {kind!r} (want local, slurm or dry)")
