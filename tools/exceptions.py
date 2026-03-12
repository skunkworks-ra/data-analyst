"""
exceptions.py — Centralised error taxonomy for ms_inspect.

All raise-able types are defined here. Tool modules import from this file
and never define their own exception classes.

Error codes map directly to the error_type field in the JSON response
envelope defined in DESIGN.md §7.2.
"""

from __future__ import annotations


class RadioMSError(Exception):
    """Base class for all ms_inspect errors."""

    error_type: str = "RADIO_MS_ERROR"

    def __init__(self, message: str, *, ms_path: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.ms_path = ms_path

    def to_dict(self) -> dict:
        return {
            "status": "error",
            "error_type": self.error_type,
            "message": self.message,
            "ms_path": self.ms_path,
            "data": None,
        }


class MSNotFoundError(RadioMSError):
    """Path does not exist."""

    error_type = "MS_NOT_FOUND"


class NotAMeasurementSetError(RadioMSError):
    """Path exists but is not a CASA Measurement Set (missing table.info)."""

    error_type = "NOT_A_MEASUREMENT_SET"


class SubtableMissingError(RadioMSError):
    """Expected subtable (e.g. ANTENNA) is absent from the MS."""

    error_type = "SUBTABLE_MISSING"


class InsufficientMetadataError(RadioMSError):
    """
    Identity-critical metadata is absent — the tool cannot proceed safely.

    Raised when:
    - TELESCOPE_NAME is missing, empty, or "UNKNOWN"
    - Antenna table is incomplete or contains only numeric names
      (common in UVFITS-converted data)

    The message always includes a specific repair path.
    """

    error_type = "INSUFFICIENT_METADATA"


class CASANotAvailableError(RadioMSError):
    """casatools is not installed or cannot be imported."""

    error_type = "CASA_NOT_AVAILABLE"


class CASAOpenFailedError(RadioMSError):
    """casatools raised an exception when opening the MS."""

    error_type = "CASA_OPEN_FAILED"


class ComputationError(RadioMSError):
    """Internal error during a derived quantity computation."""

    error_type = "COMPUTATION_ERROR"


class CalibratorResolvedWarning(UserWarning):
    """
    Non-fatal warning: a calibrator is resolved at the array's maximum baseline.

    This is a warnings.warn() warning, not a raised exception. Tools issue it
    via warnings.warn(CalibratorResolvedWarning(...)) and also embed the
    warning text in the response envelope's 'warnings' list.
    """

    pass
