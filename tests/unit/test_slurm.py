"""
Unit tests for ms_modify/slurm.py — SLURM batch submission utilities.

No SLURM installation required.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from ms_modify.exceptions import SlurmNotAvailableError
from ms_modify.slurm import (
    SlurmConfig,
    build_pipeline_submission,
    build_sbatch,
    detect_account,
    submit_pipeline,
)


class TestSlurmConfig:
    """SlurmConfig dataclass defaults."""

    def test_defaults(self):
        cfg = SlurmConfig()
        assert cfg.account == ""
        assert cfg.partition == ""
        assert cfg.nodes == 1
        assert cfg.ntasks == 1
        assert cfg.cpus_per_task == 1
        assert cfg.mem == "30G"
        assert cfg.time == "04:00:00"
        assert cfg.mail_type == ""
        assert cfg.mail_user == ""
        assert cfg.container_cmd == ""
        assert cfg.extra_sbatch_lines == []
        assert cfg.modules == []

    def test_custom_values(self):
        cfg = SlurmConfig(
            account="astro",
            partition="gpu",
            nodes=2,
            mem="64G",
            modules=["casa/6.7"],
        )
        assert cfg.account == "astro"
        assert cfg.partition == "gpu"
        assert cfg.nodes == 2
        assert cfg.mem == "64G"
        assert cfg.modules == ["casa/6.7"]

    def test_mutable_default_isolation(self):
        """Ensure list defaults are not shared between instances."""
        a = SlurmConfig()
        b = SlurmConfig()
        a.extra_sbatch_lines.append("foo")
        assert b.extra_sbatch_lines == []


class TestDetectAccount:
    """detect_account() with mocked environment."""

    def test_env_var(self):
        with patch.dict("os.environ", {"SLURM_ACCOUNT": "myaccount"}):
            assert detect_account() == "myaccount"

    def test_env_var_empty_falls_through(self):
        with (
            patch.dict("os.environ", {"SLURM_ACCOUNT": "", "USER": "testuser"}),
            patch("shutil.which", return_value=None),
        ):
            assert detect_account() is None

    def test_sacctmgr_fallback(self):
        with (
            patch.dict("os.environ", {"SLURM_ACCOUNT": "", "USER": "testuser"}, clear=False),
            patch("shutil.which", return_value="/usr/bin/sacctmgr"),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "defaultacct|"
            assert detect_account() == "defaultacct"
            mock_run.assert_called_once()

    def test_sacctmgr_not_found(self):
        with (
            patch.dict("os.environ", {"SLURM_ACCOUNT": ""}, clear=False),
            patch("shutil.which", return_value=None),
        ):
            assert detect_account() is None

    def test_sacctmgr_failure(self):
        with (
            patch.dict("os.environ", {"SLURM_ACCOUNT": "", "USER": "testuser"}, clear=False),
            patch("shutil.which", return_value="/usr/bin/sacctmgr"),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value.returncode = 1
            mock_run.return_value.stdout = ""
            assert detect_account() is None


class TestBuildSbatch:
    """build_sbatch() template output."""

    def test_basic_sbatch(self, tmp_path):
        script = tmp_path / "preflag.py"
        script.write_text("# preflag script")

        cfg = SlurmConfig()
        result = build_sbatch(script, tmp_path, cfg)

        content = Path(result).read_text()
        assert result.endswith("preflag.sbatch")
        assert "#SBATCH --job-name=preflag" in content
        assert "#SBATCH --mem=30G" in content
        assert "#SBATCH --time=04:00:00" in content
        assert f"python {script}" in content
        # No account line when empty
        assert "--account" not in content

    def test_custom_job_name(self, tmp_path):
        script = tmp_path / "my_script.py"
        script.write_text("# script")

        result = build_sbatch(script, tmp_path, SlurmConfig(), job_name="step1")
        content = Path(result).read_text()
        assert result.endswith("step1.sbatch")
        assert "#SBATCH --job-name=step1" in content

    def test_account_and_partition(self, tmp_path):
        script = tmp_path / "test.py"
        script.write_text("# test")

        cfg = SlurmConfig(account="astro-001", partition="compute")
        content = Path(build_sbatch(script, tmp_path, cfg)).read_text()
        assert "#SBATCH --account=astro-001" in content
        assert "#SBATCH --partition=compute" in content

    def test_mail_options(self, tmp_path):
        script = tmp_path / "test.py"
        script.write_text("# test")

        cfg = SlurmConfig(mail_type="END,FAIL", mail_user="user@example.com")
        content = Path(build_sbatch(script, tmp_path, cfg)).read_text()
        assert "#SBATCH --mail-type=END,FAIL" in content
        assert "#SBATCH --mail-user=user@example.com" in content

    def test_container_cmd(self, tmp_path):
        script = tmp_path / "test.py"
        script.write_text("# test")

        cfg = SlurmConfig(container_cmd="singularity exec /path/to/casa.sif")
        content = Path(build_sbatch(script, tmp_path, cfg)).read_text()
        assert f"singularity exec /path/to/casa.sif python {script}" in content

    def test_modules(self, tmp_path):
        script = tmp_path / "test.py"
        script.write_text("# test")

        cfg = SlurmConfig(modules=["casa/6.7", "python/3.12"])
        content = Path(build_sbatch(script, tmp_path, cfg)).read_text()
        assert "module load casa/6.7" in content
        assert "module load python/3.12" in content

    def test_extra_sbatch_lines(self, tmp_path):
        script = tmp_path / "test.py"
        script.write_text("# test")

        cfg = SlurmConfig(extra_sbatch_lines=["#SBATCH --gres=gpu:1"])
        content = Path(build_sbatch(script, tmp_path, cfg)).read_text()
        assert "#SBATCH --gres=gpu:1" in content

    def test_script_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="Script not found"):
            build_sbatch(tmp_path / "nonexistent.py", tmp_path, SlurmConfig())

    def test_workdir_not_found(self, tmp_path):
        script = tmp_path / "test.py"
        script.write_text("# test")
        with pytest.raises(NotADirectoryError, match="Work directory not found"):
            build_sbatch(script, tmp_path / "nodir", SlurmConfig())


class TestBuildPipelineSubmission:
    """build_pipeline_submission() dependency chaining."""

    def test_single_job(self, tmp_path):
        sbatch = tmp_path / "preflag.sbatch"
        sbatch.write_text("#!/bin/bash\n")

        result = build_pipeline_submission([sbatch], tmp_path)
        content = Path(result).read_text()

        assert result.endswith("submit_pipeline.sh")
        assert "sbatch --parsable" in content
        assert "--dependency" not in content

    def test_chained_jobs(self, tmp_path):
        names = ["preflag", "priorcals", "setjy"]
        sbatch_paths = []
        for name in names:
            p = tmp_path / f"{name}.sbatch"
            p.write_text("#!/bin/bash\n")
            sbatch_paths.append(p)

        result = build_pipeline_submission(sbatch_paths, tmp_path)
        content = Path(result).read_text()

        # First job: no dependency
        assert "JOB0=$(sbatch --parsable" in content
        assert (
            "--dependency"
            not in content.split("\n")[
                next(i for i, line in enumerate(content.split("\n")) if "JOB0=" in line)
            ]
        )

        # Subsequent jobs: afterok dependency on previous
        assert "--dependency=afterok:$JOB0" in content
        assert "--dependency=afterok:$JOB1" in content

    def test_executable_permission(self, tmp_path):
        sbatch = tmp_path / "test.sbatch"
        sbatch.write_text("#!/bin/bash\n")

        result = build_pipeline_submission([sbatch], tmp_path)
        assert os.access(result, os.X_OK)

    def test_set_e_pipefail(self, tmp_path):
        sbatch = tmp_path / "test.sbatch"
        sbatch.write_text("#!/bin/bash\n")

        content = Path(build_pipeline_submission([sbatch], tmp_path)).read_text()
        assert "set -euo pipefail" in content

    def test_empty_list_raises(self, tmp_path):
        with pytest.raises(ValueError, match="must not be empty"):
            build_pipeline_submission([], tmp_path)

    def test_workdir_not_found(self, tmp_path):
        with pytest.raises(NotADirectoryError):
            build_pipeline_submission([tmp_path / "test.sbatch"], tmp_path / "nodir")


class TestSubmitPipeline:
    """submit_pipeline() — sbatch availability check."""

    def test_sbatch_not_on_path(self, tmp_path):
        script = tmp_path / "submit_pipeline.sh"
        script.write_text("#!/bin/bash\necho test")

        with (
            patch("shutil.which", return_value=None),
            pytest.raises(SlurmNotAvailableError, match="sbatch not found"),
        ):
            submit_pipeline(script)

    def test_script_not_found(self, tmp_path):
        with (
            patch("shutil.which", return_value="/usr/bin/sbatch"),
            pytest.raises(FileNotFoundError, match="Submission script not found"),
        ):
            submit_pipeline(tmp_path / "nonexistent.sh")
