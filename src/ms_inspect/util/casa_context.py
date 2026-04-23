"""
util/casa_context.py — Context managers for safe CASA tool lifecycle.

Every tool that opens an msmd, ms, or tb instance must use these context
managers. They guarantee close() is called even on exception — CASA table
locks are persistent across processes and a missing close() can corrupt data
or deadlock subsequent opens.

Usage:
    from ms_inspect.util.casa_context import open_msmd, open_table

    with open_msmd("/data/obs/target.ms") as msmd:
        names = msmd.fieldnames()

    with open_table("/data/obs/target.ms/ANTENNA") as tb:
        positions = tb.getcol("POSITION")
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any

from ms_inspect.exceptions import (
    CASANotAvailableError,
    CASAOpenFailedError,
    MSNotFoundError,
    NotAMeasurementSetError,
    SubtableMissingError,
)


def _require_casatools() -> Any:
    """
    Lazily import casatools. Raises CASANotAvailableError with install
    instructions if not present.
    """
    try:
        import casatools  # type: ignore[import]

        return casatools
    except ImportError as exc:
        raise CASANotAvailableError(
            "casatools is not installed or cannot be imported.\n"
            "Install with: pip install casatools casatasks\n"
            "Or via pixi: pixi install (casatools is in [pypi-dependencies]).\n"
            "Requires Python 3.12 on Linux x86_64 or macOS ARM64/x86_64."
        ) from exc


def validate_ms_path(ms_path: str) -> Path:
    """
    Resolve and validate a Measurement Set path.

    Raises:
        MSNotFoundError           — path does not exist
        NotAMeasurementSetError   — path exists but is not an MS
    """
    p = Path(ms_path).expanduser().resolve()

    if not p.exists():
        raise MSNotFoundError(
            f"Measurement Set not found: {p}\n"
            "Check the path. MS directories end in .ms by convention "
            "and contain a 'table.info' file.",
            ms_path=ms_path,
        )

    if not (p / "table.info").exists():
        raise NotAMeasurementSetError(
            f"'{p}' exists but does not appear to be a CASA Measurement Set "
            "(missing 'table.info'). Is this a calibration table, image, or "
            "partially-written MS?",
            ms_path=ms_path,
        )

    return p


def validate_subtable(ms_path: Path, subtable: str) -> Path:
    """
    Check that a named subtable directory exists inside the MS.

    Raises SubtableMissingError if absent.
    """
    sub_path = ms_path / subtable
    if not sub_path.exists():
        raise SubtableMissingError(
            f"Subtable '{subtable}' not found in {ms_path}. "
            f"Expected at: {sub_path}. "
            "The MS may be incomplete, corrupted, or from an older format version.",
            ms_path=str(ms_path),
        )
    return sub_path


@contextmanager
def open_msmd(ms_path: str) -> Generator[Any, None, None]:
    """
    Context manager for casatools.msmetadata.

    Opens msmd on the given MS path and guarantees close() on exit.

    Raises:
        MSNotFoundError / NotAMeasurementSetError — bad path
        CASANotAvailableError                      — casatools not installed
        CASAOpenFailedError                        — CASA raised on open

    Usage:
        with open_msmd("/path/to/target.ms") as msmd:
            names = msmd.fieldnames()
    """
    p = validate_ms_path(ms_path)
    casatools = _require_casatools()

    msmd = casatools.msmetadata()
    try:
        opened = msmd.open(str(p))
        if not opened:
            raise CASAOpenFailedError(
                f"msmetadata.open() returned False for '{p}'. "
                "The MS may be locked by another process, or corrupted.",
                ms_path=ms_path,
            )
        yield msmd
    except CASAOpenFailedError:
        raise
    except Exception as exc:
        raise CASAOpenFailedError(
            f"casatools.msmetadata raised an exception opening '{p}': {exc}",
            ms_path=ms_path,
        ) from exc
    finally:
        import contextlib

        with contextlib.suppress(Exception):
            msmd.close()  # best-effort close; don't mask the original exception


@contextmanager
def open_table(table_path: str, *, read_only: bool = True) -> Generator[Any, None, None]:
    """
    Context manager for casatools.table (tb).

    Opens the table at `table_path` (may be an MS main table or any subtable).
    Always opens read-only unless read_only=False is explicitly passed.

    Raises:
        CASANotAvailableError  — casatools not installed
        CASAOpenFailedError    — CASA raised on open

    Usage:
        with open_table("/path/to/target.ms/ANTENNA") as tb:
            names = tb.getcol("NAME")

        with open_table("/path/to/target.ms/OBSERVATION") as tb:
            telescope = tb.getcell("TELESCOPE_NAME", 0)
    """
    casatools = _require_casatools()
    tb = casatools.table()
    try:
        opened = tb.open(table_path, nomodify=read_only)
        if not opened:
            raise CASAOpenFailedError(
                f"table.open() returned False for '{table_path}'. "
                "The table may be locked, missing, or corrupted.",
            )
        yield tb
    except CASAOpenFailedError:
        raise
    except Exception as exc:
        raise CASAOpenFailedError(
            f"casatools.table raised an exception opening '{table_path}': {exc}",
        ) from exc
    finally:
        with suppress(Exception):
            tb.close()


@contextmanager
def open_image(image_path: str) -> Generator[Any, None, None]:
    """
    Context manager for casatools.image (ia).

    Opens a CASA native image directory and guarantees done() on exit.

    Raises:
        MSNotFoundError        — path does not exist
        CASANotAvailableError  — casatools not installed
        CASAOpenFailedError    — CASA raised on open

    Usage:
        with open_image("/path/to/target.image") as ia:
            stats = ia.statistics()
    """
    p = Path(image_path).expanduser().resolve()
    if not p.exists():
        raise MSNotFoundError(
            f"Image not found: {p}",
            ms_path=image_path,
        )

    casatools = _require_casatools()
    ia = casatools.image()
    try:
        opened = ia.open(str(p))
        if not opened:
            raise CASAOpenFailedError(
                f"image.open() returned False for '{p}'. The image may be locked or corrupted.",
                ms_path=image_path,
            )
        yield ia
    except CASAOpenFailedError:
        raise
    except Exception as exc:
        raise CASAOpenFailedError(
            f"casatools.image raised an exception opening '{p}': {exc}",
            ms_path=image_path,
        ) from exc
    finally:
        with suppress(Exception):
            ia.done()


@contextmanager
def open_ms(ms_path: str) -> Generator[Any, None, None]:
    """
    Context manager for casatools.ms.

    Lower-level MS tool — used for operations not available in msmetadata.
    Opens in read-only mode.

    Usage:
        with open_ms("/path/to/target.ms") as ms:
            ms.selectinit(reset=True)
            data = ms.getdata(["UVW"])
    """
    p = validate_ms_path(ms_path)
    casatools = _require_casatools()

    ms = casatools.ms()
    try:
        opened = ms.open(str(p), nomodify=True)
        if not opened:
            raise CASAOpenFailedError(
                f"ms.open() returned False for '{p}'.",
                ms_path=ms_path,
            )
        yield ms
    except CASAOpenFailedError:
        raise
    except Exception as exc:
        raise CASAOpenFailedError(
            f"casatools.ms raised an exception opening '{p}': {exc}",
            ms_path=ms_path,
        ) from exc
    finally:
        with suppress(Exception):
            ms.close()
