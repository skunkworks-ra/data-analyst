"""
calsol_plot_library.py — ms_plot_caltable_library

Plot an explicit list of CASA calibration tables in one call. Each caltable
is passed to ms_calsol_plot (calsol_plot.run) independently so a single bad
table produces a per-entry error rather than aborting the whole batch.

Returns a summary envelope with one entry per caltable: html_path, table_type,
and any per-table errors or warnings.
"""

from __future__ import annotations

from pathlib import Path

from ms_inspect.tools import calsol_plot
from ms_inspect.util.formatting import field as fmt_field
from ms_inspect.util.formatting import response_envelope

TOOL_NAME = "ms_plot_caltable_library"


def run(
    caltable_paths: list[str],
    output_dir: str,
) -> dict:
    """
    Plot an explicit list of CASA calibration tables.

    Each table is passed to calsol_plot.run() independently. A table that
    fails (not found, unsupported type, CASA error) records an error entry
    rather than aborting the batch.

    Args:
        caltable_paths: Ordered list of paths to caltable directories.
        output_dir:     Directory to write all dashboard HTML and NPZ files.

    Returns:
        Standard response envelope. data["plots"] is a list of per-table
        dicts with keys: caltable, status, html_path, npz_path, table_type,
        error (if status == "error").
    """
    out = Path(output_dir).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)

    plots: list[dict] = []
    warnings: list[str] = []
    casa_calls: list[str] = []

    for raw_path in caltable_paths:
        p = Path(raw_path).expanduser().resolve()
        entry: dict = {"caltable": str(p)}

        result = calsol_plot.run(str(p), str(out))
        casa_calls.extend(result.get("provenance", {}).get("casa_calls", []))

        if result.get("status") == "error":
            entry["status"] = "error"
            entry["error"] = result.get("message", "unknown error")
            warnings.append(f"{p.name}: {entry['error']}")
        else:
            d = result.get("data", {})
            entry["status"] = "ok"
            entry["html_path"] = d.get("html_path", {}).get("value", "")
            entry["npz_path"] = d.get("npz_path", {}).get("value", "")
            entry["table_type"] = d.get("table_type", {}).get("value", "")
            for w in result.get("warnings", []):
                warnings.append(f"{p.name}: {w}")

        plots.append(entry)

    n_ok = sum(1 for e in plots if e["status"] == "ok")
    n_err = len(plots) - n_ok

    return response_envelope(
        tool_name=TOOL_NAME,
        ms_path=output_dir,
        data={
            "plots": fmt_field(plots),
            "n_ok": fmt_field(n_ok),
            "n_error": fmt_field(n_err),
            "output_dir": fmt_field(str(out)),
        },
        warnings=warnings,
        casa_calls=casa_calls,
    )
