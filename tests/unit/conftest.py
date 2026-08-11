"""
conftest.py — unit test fixtures.

The driver is a standalone script directory, not an installed package: its
modules import each other by bare name (``from validate import ...``) so that
``driver.py`` runs from a copy anywhere on disk. Tests therefore need
``driver/`` on sys.path, exactly as ``driver.py`` puts it there itself.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

DRIVER_DIR = Path(__file__).resolve().parents[2] / "driver"

if str(DRIVER_DIR) not in sys.path:
    sys.path.insert(0, str(DRIVER_DIR))


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
