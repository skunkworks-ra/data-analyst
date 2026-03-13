"""
tools/priorcals_check.py — ms_verify_priorcals

Verifies that prior calibration tables from ms_generate_priorcals exist
on disk and are structurally sound (non-empty CASA tables).

No casatasks required — filesystem + casatools table reads only.
"""

from __future__ import annotations

from pathlib import Path

from ms_inspect.util.casa_context import open_table
from ms_inspect.util.formatting import field as fmt_field
from ms_inspect.util.formatting import response_envelope

TOOL_NAME = "ms_verify_priorcals"

# Required columns present in all caltable types
_REQUIRED_COLS = ["TIME", "FIELD_ID", "SPECTRAL_WINDOW_ID", "FPARAM"]


def _check_table(path: str) -> dict:
    """
    Check that a CASA caltable exists and has rows.

    Returns a dict with exists, n_rows, has_required_cols, valid.
    """
    p = Path(path)
    if not p.exists() or not p.is_dir():
        return {
            "path": path,
            "exists": False,
            "n_rows": 0,
            "has_required_cols": False,
            "valid": False,
        }

    try:
        with open_table(path) as tb:
            n_rows = int(tb.nrows())
            col_names = set(tb.colnames())
    except Exception as exc:
        return {
            "path": path,
            "exists": True,
            "n_rows": 0,
            "has_required_cols": False,
            "valid": False,
            "error": str(exc),
        }

    has_required = all(c in col_names for c in _REQUIRED_COLS)
    valid = n_rows > 0 and has_required
    return {
        "path": path,
        "exists": True,
        "n_rows": n_rows,
        "has_required_cols": has_required,
        "valid": valid,
    }


def run(
    ms_path: str,
    workdir: str,
    table_names: list[str] | None = None,
) -> dict:
    """
    Verify prior calibration tables exist and are non-empty.

    Args:
        ms_path:     Path to the MS (used for provenance only).
        workdir:     Directory where ms_generate_priorcals wrote its tables.
        table_names: Specific table filenames to check (default: all four
                     standard tables: gain_curves.gc, opacities.opac,
                     requantizer.rq, antpos.ap).

    Returns:
        Standard response envelope with per-table check results and
        an overall all_valid flag.
    """
    casa_calls: list[str] = []
    warnings: list[str] = []

    workdir_path = Path(workdir)
    if not workdir_path.exists():
        from ms_inspect.exceptions import ComputationError
        raise ComputationError(
            f"workdir does not exist: {workdir}",
            ms_path=ms_path,
        )

    if table_names is None:
        table_names = ["gain_curves.gc", "opacities.opac", "requantizer.rq", "antpos.ap"]

    results: list[dict] = []
    for name in table_names:
        table_path = str(workdir_path / name)
        result = _check_table(table_path)
        casa_calls.append(f"tb.open({name!r}) → nrows()")
        results.append(result)

    n_valid = sum(1 for r in results if r["valid"])
    n_missing = sum(1 for r in results if not r["exists"])
    all_valid = n_missing == 0 and all(r["valid"] for r in results)

    if n_missing > 0:
        warnings.append(
            f"{n_missing} table(s) not found on disk. "
            "Run priorcals.py first, then re-verify."
        )

    data = {
        "all_valid": fmt_field(all_valid),
        "n_checked": fmt_field(len(results)),
        "n_valid": fmt_field(n_valid),
        "n_missing": fmt_field(n_missing),
        "tables": [fmt_field(r) for r in results],
    }

    return response_envelope(
        tool_name=TOOL_NAME,
        ms_path=ms_path,
        data=data,
        warnings=warnings,
        casa_calls=casa_calls,
    )
