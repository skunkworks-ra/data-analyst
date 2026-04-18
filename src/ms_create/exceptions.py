"""
exceptions.py — Error types for ms_create utilities.
"""

from __future__ import annotations

from ms_inspect.exceptions import RadioMSError


class ASDMNotFoundError(RadioMSError):
    """Raised when the ASDM path does not exist or is not a directory."""

    error_type = "ASDM_NOT_FOUND"


class ImportFailedError(RadioMSError):
    """Raised when importasdm raises an exception during in-process execution."""

    error_type = "IMPORT_FAILED"
