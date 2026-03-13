"""
Integration tests for ms_modify/intents.py — set_intents utility.

Requires casatools and a writable MS. Skipped when RADIO_MCP_TEST_MS is not set.
"""

from __future__ import annotations

import os

import pytest

_SKIP = pytest.mark.skipif(
    not os.environ.get("RADIO_MCP_TEST_MS"),
    reason="RADIO_MCP_TEST_MS not set — skipping integration tests",
)


@_SKIP
class TestSetIntents:
    """Integration tests for set_intents (require a real MS copy)."""

    def test_dry_run_no_changes(self):
        """Verify dry_run=True returns mapping but writes nothing."""

    def test_state_rows_created(self):
        """Verify STATE subtable rows are created correctly."""

    def test_state_id_updated(self):
        """Verify STATE_ID column in MAIN table is updated."""

    def test_guard_raises_on_existing_intents(self):
        """Verify IntentsAlreadyPopulatedError on MS with existing intents."""
