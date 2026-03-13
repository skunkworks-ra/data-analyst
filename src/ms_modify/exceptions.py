"""
exceptions.py — Error types for ms_modify utilities.
"""

from __future__ import annotations

from ms_inspect.exceptions import RadioMSError


class IntentsAlreadyPopulatedError(RadioMSError):
    """Raised when ≥50% of fields already have non-empty scan intents."""

    error_type = "INTENTS_ALREADY_POPULATED"
