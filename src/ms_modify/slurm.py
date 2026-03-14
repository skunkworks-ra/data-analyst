"""
slurm.py — SLURM batch submission utilities for ms_modify calibration scripts.

Wraps generated Python scripts in sbatch files and chains them with
afterok dependencies matching the calibration pipeline order.
Not an MCP tool — a utility callable by skills or user scripts.
"""

from __future__ import annotations

import dataclasses
import os
import shutil
import subprocess
from pathlib import Path

from ms_modify.exceptions import SlurmNotAvailableError

_SBATCH_TEMPLATE = """\
#!/bin/bash
#SBATCH --job-name={job_name}
#SBATCH --output={job_name}_%j.out
#SBATCH --error={job_name}_%j.err
#SBATCH --nodes={nodes}
#SBATCH --ntasks={ntasks}
#SBATCH --cpus-per-task={cpus_per_task}
#SBATCH --mem={mem}
#SBATCH --time={time}
{extra_sbatch}
{module_lines}
{container_cmd}python {script_path}
"""


@dataclasses.dataclass
class SlurmConfig:
    """Configuration for SLURM sbatch generation."""

    account: str = ""
    partition: str = ""
    nodes: int = 1
    ntasks: int = 1
    cpus_per_task: int = 1
    mem: str = "30G"
    time: str = "04:00:00"
    mail_type: str = ""
    mail_user: str = ""
    container_cmd: str = ""
    extra_sbatch_lines: list[str] = dataclasses.field(default_factory=list)
    modules: list[str] = dataclasses.field(default_factory=list)


def detect_account() -> str | None:
    """Detect the default SLURM account for the current user.

    Checks ``$SLURM_ACCOUNT`` first, then falls back to
    ``sacctmgr show user $USER format=DefaultAccount``.
    Returns ``None`` if both fail.
    """
    env_account = os.environ.get("SLURM_ACCOUNT", "").strip()
    if env_account:
        return env_account

    sacctmgr = shutil.which("sacctmgr")
    if sacctmgr is None:
        return None

    user = os.environ.get("USER", "").strip()
    if not user:
        return None

    try:
        result = subprocess.run(
            [sacctmgr, "-n", "-p", "show", "user", user, "format=DefaultAccount"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return None
        account = result.stdout.strip().rstrip("|").strip()
        return account if account else None
    except (subprocess.TimeoutExpired, OSError):
        return None


def build_sbatch(
    script_path: str | Path,
    workdir: str | Path,
    config: SlurmConfig,
    *,
    job_name: str | None = None,
) -> str:
    """Generate an sbatch file wrapping a Python script.

    Parameters
    ----------
    script_path : path to the Python script to execute
    workdir : directory where the .sbatch file will be written
    config : SLURM configuration
    job_name : optional job name (derived from script filename if omitted)

    Returns
    -------
    str : path to the written .sbatch file
    """
    script_path = Path(script_path)
    workdir = Path(workdir)

    if not script_path.exists():
        raise FileNotFoundError(f"Script not found: {script_path}")
    if not workdir.is_dir():
        raise NotADirectoryError(f"Work directory not found: {workdir}")

    if job_name is None:
        job_name = script_path.stem

    # Build optional #SBATCH lines
    extra_lines: list[str] = []
    if config.account:
        extra_lines.append(f"#SBATCH --account={config.account}")
    if config.partition:
        extra_lines.append(f"#SBATCH --partition={config.partition}")
    if config.mail_type:
        extra_lines.append(f"#SBATCH --mail-type={config.mail_type}")
    if config.mail_user:
        extra_lines.append(f"#SBATCH --mail-user={config.mail_user}")
    extra_lines.extend(config.extra_sbatch_lines)

    # Module load commands
    module_cmds: list[str] = []
    for mod in config.modules:
        module_cmds.append(f"module load {mod}")

    # Container command prefix
    container_prefix = f"{config.container_cmd} " if config.container_cmd else ""

    content = _SBATCH_TEMPLATE.format(
        job_name=job_name,
        nodes=config.nodes,
        ntasks=config.ntasks,
        cpus_per_task=config.cpus_per_task,
        mem=config.mem,
        time=config.time,
        extra_sbatch="\n".join(extra_lines),
        module_lines="\n".join(module_cmds),
        container_cmd=container_prefix,
        script_path=script_path,
    )

    sbatch_path = workdir / f"{job_name}.sbatch"
    sbatch_path.write_text(content)
    return str(sbatch_path)


def build_pipeline_submission(
    sbatch_paths: list[str | Path],
    workdir: str | Path,
) -> str:
    """Generate a submission script that chains sbatch jobs with afterok dependencies.

    Parameters
    ----------
    sbatch_paths : ordered list of .sbatch file paths (pipeline order)
    workdir : directory where submit_pipeline.sh will be written

    Returns
    -------
    str : path to the written submit_pipeline.sh
    """
    workdir = Path(workdir)
    if not workdir.is_dir():
        raise NotADirectoryError(f"Work directory not found: {workdir}")
    if not sbatch_paths:
        raise ValueError("sbatch_paths must not be empty")

    lines = [
        "#!/bin/bash",
        "# Auto-generated pipeline submission script",
        "# Review before running: bash submit_pipeline.sh",
        "set -euo pipefail",
        "",
    ]

    for i, sbatch_path in enumerate(sbatch_paths):
        job_var = f"JOB{i}"
        sbatch_path = Path(sbatch_path)
        if i == 0:
            lines.append(f'{job_var}=$(sbatch --parsable "{sbatch_path}")')
        else:
            prev_var = f"JOB{i - 1}"
            lines.append(
                f'{job_var}=$(sbatch --parsable --dependency=afterok:${prev_var} "{sbatch_path}")'
            )
        lines.append(f'echo "Submitted {sbatch_path.name} as job ${job_var}"')

    lines.append("")
    lines.append('echo "All jobs submitted."')
    lines.append("")

    submission_path = workdir / "submit_pipeline.sh"
    submission_path.write_text("\n".join(lines))
    submission_path.chmod(0o755)
    return str(submission_path)


def submit_pipeline(submission_script: str | Path) -> dict:
    """Execute the pipeline submission script.

    Requires SLURM (``sbatch``) to be available on PATH.

    Parameters
    ----------
    submission_script : path to submit_pipeline.sh

    Returns
    -------
    dict with keys: submitted, job_ids, stdout, stderr
    """
    if shutil.which("sbatch") is None:
        raise SlurmNotAvailableError(
            "sbatch not found on PATH — SLURM is not available on this system."
        )

    submission_script = Path(submission_script)
    if not submission_script.exists():
        raise FileNotFoundError(f"Submission script not found: {submission_script}")

    result = subprocess.run(
        ["bash", str(submission_script)],
        capture_output=True,
        text=True,
        timeout=60,
    )

    # Parse job IDs from "Submitted ... as job <id>" lines
    job_ids: list[str] = []
    for line in result.stdout.splitlines():
        if line.startswith("Submitted"):
            # "Submitted foo.sbatch as job 12345"
            parts = line.split()
            if parts:
                job_ids.append(parts[-1])

    return {
        "submitted": result.returncode == 0,
        "job_ids": job_ids,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }
