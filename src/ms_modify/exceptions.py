"""
exceptions.py — Error types for ms_modify utilities.
"""

from __future__ import annotations

from ms_inspect.exceptions import RadioMSError


class IntentsAlreadyPopulatedError(RadioMSError):
    """Raised when ≥50% of fields already have non-empty scan intents."""

    error_type = "INTENTS_ALREADY_POPULATED"


class InitialBandpassFailedError(RadioMSError):
    """
    Raised when gaincal or bandpass fails to produce the expected caltable.

    Message always includes the CASA command that was attempted.
    """

    error_type = "INITIAL_BANDPASS_FAILED"


class GaincalFailedError(RadioMSError):
    """Raised when gaincal fails to produce the expected caltable."""

    error_type = "GAINCAL_FAILED"


class BandpassFailedError(RadioMSError):
    """Raised when bandpass fails to produce the expected caltable."""

    error_type = "BANDPASS_FAILED"


class FluxscaleFailedError(RadioMSError):
    """Raised when fluxscale fails to produce the expected fluxtable."""

    error_type = "FLUXSCALE_FAILED"


class ApplycalFailedError(RadioMSError):
    """Raised when applycal raises an exception during in-process execution."""

    error_type = "APPLYCAL_FAILED"


class TcleanFailedError(RadioMSError):
    """Raised when tclean fails or does not produce the expected image."""

    error_type = "TCLEAN_FAILED"


class SlurmNotAvailableError(RadioMSError):
    """Raised when sbatch is not found on PATH."""

    error_type = "SLURM_NOT_AVAILABLE"
