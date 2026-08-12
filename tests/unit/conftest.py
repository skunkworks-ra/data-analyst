"""
conftest.py — unit test fixtures.

analyst_driver is an installed package like the others, so nothing here has to
manipulate sys.path. The packaged defaults are located through the package
itself, which means these tests exercise the same files a real run freezes
into its run directory.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import analyst_driver

DRIVER_DIR = Path(analyst_driver.__file__).resolve().parent


@pytest.fixture
def driver_dir() -> Path:
    return DRIVER_DIR


@pytest.fixture
def whitelist() -> dict:
    """The real whitelist, so a drift between it and the tools fails a test."""
    return yaml.safe_load((DRIVER_DIR / "whitelist.yaml").read_text())


@pytest.fixture
def rules() -> list[dict]:
    return yaml.safe_load((DRIVER_DIR / "verifier.yaml").read_text())["rules"]


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    """A minimal run directory: the layout the driver creates at init."""
    for sub in ("steps", "decisions", "cache"):
        (tmp_path / sub).mkdir(parents=True)
    return tmp_path


@pytest.fixture
def fake_ms(tmp_path: Path) -> Path:
    """A directory that passes an existence check without being a real MS."""
    ms = tmp_path / "fake.ms"
    ms.mkdir()
    return ms
