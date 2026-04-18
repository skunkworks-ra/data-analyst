"""
conftest.py — Integration test fixtures.

Resolves the test MS from one of two sources (in priority order):

  1. RADIO_MCP_TEST_MS   — path to a pre-extracted MS directory
  2. RADIO_MCP_TEST_MS_TGZ — path to a .ms.tgz tarball; extracted once per
                             session to a tmp directory

If neither is set, the default fallback path
  ~/Data/measurement_sets/3c391_ctm_mosaic_10s_spw0.ms.tgz
is tried automatically so that local development works without any env vars.

The resolved MS path is written to os.environ['RADIO_MCP_TEST_MS'] so that
all test modules that read that variable pick it up.
"""

from __future__ import annotations

import os
import tarfile
from pathlib import Path

_DEFAULT_TGZ = Path.home() / "Data/measurement_sets/3c391_ctm_mosaic_10s_spw0.ms.tgz"

_EXTRACT_DIR: Path | None = None


def _find_ms_in_extracted(extract_dir: Path) -> str | None:
    """Return the first .ms directory found under extract_dir."""
    for candidate in sorted(extract_dir.rglob("*.ms")):
        if candidate.is_dir() and (candidate / "table.info").exists():
            return str(candidate)
    return None


def pytest_configure(config):
    """
    Runs before collection — sets RADIO_MCP_TEST_MS early enough that
    module-level _SKIP markers in test_tools.py see the correct value.
    """
    global _EXTRACT_DIR

    if os.environ.get("RADIO_MCP_TEST_MS"):
        return

    tgz_env = os.environ.get("RADIO_MCP_TEST_MS_TGZ", "")
    tgz = Path(tgz_env) if tgz_env else _DEFAULT_TGZ

    if not tgz.exists():
        return

    import tempfile

    _EXTRACT_DIR = Path(tempfile.mkdtemp(prefix="radio_test_ms_"))
    with tarfile.open(tgz, "r:gz") as tf:
        tf.extractall(_EXTRACT_DIR)

    ms_path = _find_ms_in_extracted(_EXTRACT_DIR)
    if ms_path:
        os.environ["RADIO_MCP_TEST_MS"] = ms_path


def pytest_unconfigure(config):
    """Clean up the extracted MS directory after the session."""
    import shutil

    if _EXTRACT_DIR and _EXTRACT_DIR.exists():
        shutil.rmtree(_EXTRACT_DIR, ignore_errors=True)
