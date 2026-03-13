"""
caltables.py — ms_verify_caltables

Checks that the expected caltables from ms_initial_bandpass exist and are
structurally sound (non-empty, correct column names).

No casatasks required — pure filesystem and casatools table reads.
"""

from __future__ import annotations

from pathlib import Path

from ms_inspect.util.casa_context import open_table
from ms_inspect.util.formatting import field as fmt_field
from ms_inspect.util.formatting import response_envelope

TOOL_NAME = "ms_verify_caltables"


def _check_table(path: str, required_cols: list[str]) -> dict:
    """
    Open a CASA table and verify it is non-empty with required columns present.

    Returns a dict with keys: exists, n_rows, columns_present, missing_cols, valid.
    """
    p = Path(path)
    if not p.exists() or not p.is_dir():
        return {
            "exists": False,
            "n_rows": 0,
            "columns_present": [],
            "missing_cols": required_cols,
            "valid": False,
        }

    try:
        with open_table(path) as tb:
            n_rows = int(tb.nrows())
            col_names = list(tb.colnames())
    except Exception as exc:
        return {
            "exists": True,
            "n_rows": 0,
            "columns_present": [],
            "missing_cols": required_cols,
            "valid": False,
            "error": str(exc),
        }

    missing = [c for c in required_cols if c not in col_names]
    valid = n_rows > 0 and not missing
    return {
        "exists": True,
        "n_rows": n_rows,
        "columns_present": col_names,
        "missing_cols": missing,
        "valid": valid,
    }


def run(
    ms_path: str,
    init_gain_table: str,
    bp_table: str,
) -> dict:
    """
    Verify that caltables from ms_initial_bandpass exist and are structurally sound.

    Args:
        ms_path:         Source MS (for provenance only — not opened).
        init_gain_table: Path to init_gain.g.
        bp_table:        Path to BP0.b.

    Returns:
        Standard response envelope with per-table checks and caltables_valid flag.
    """
    casa_calls: list[str] = []
    warnings: list[str] = []

    init_check = _check_table(init_gain_table, required_cols=["CPARAM"])
    casa_calls.append(f"tb.open('{init_gain_table}') → nrows, colnames")

    bp_check = _check_table(bp_table, required_cols=["CPARAM", "FPARAM"])
    # BP tables may use either CPARAM (complex) or FPARAM (float) — either is fine
    bp_valid = bp_check["exists"] and bp_check["n_rows"] > 0 and (
        "CPARAM" in bp_check["columns_present"] or "FPARAM" in bp_check["columns_present"]
    )
    bp_check["valid"] = bp_valid
    casa_calls.append(f"tb.open('{bp_table}') → nrows, colnames")

    caltables_valid = init_check["valid"] and bp_check["valid"]

    if not init_check["exists"]:
        warnings.append(f"init_gain.g not found at '{init_gain_table}'. Has the bandpass script been run?")
    if not bp_check["exists"]:
        warnings.append(f"BP0.b not found at '{bp_table}'. Has the bandpass script been run?")
    if init_check.get("error"):
        warnings.append(f"init_gain.g open error: {init_check['error']}")
    if bp_check.get("error"):
        warnings.append(f"BP0.b open error: {bp_check['error']}")

    flag = "COMPLETE" if caltables_valid else "UNAVAILABLE"

    data = {
        "caltables_valid": fmt_field(caltables_valid, flag=flag),
        "init_gain_table": {
            "path": init_gain_table,
            "exists": fmt_field(init_check["exists"]),
            "n_rows": fmt_field(init_check["n_rows"]),
            "valid": fmt_field(init_check["valid"], flag="COMPLETE" if init_check["valid"] else "UNAVAILABLE"),
        },
        "bp_table": {
            "path": bp_table,
            "exists": fmt_field(bp_check["exists"]),
            "n_rows": fmt_field(bp_check["n_rows"]),
            "valid": fmt_field(bp_check["valid"], flag="COMPLETE" if bp_check["valid"] else "UNAVAILABLE"),
        },
    }

    return response_envelope(
        tool_name=TOOL_NAME,
        ms_path=ms_path,
        data=data,
        warnings=warnings,
        casa_calls=casa_calls,
    )
