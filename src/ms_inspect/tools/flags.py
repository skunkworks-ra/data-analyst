"""
tools/flags.py — ms_antenna_flag_fraction

Layer 2, Tool 6.

Computes per-antenna pre-existing flag fractions from the FLAG column.
Uses multiprocessing for parallel chunked reads on large MSs.

Parallel read strategy (DESIGN.md §6.6):
- Partition MAIN table rows into N chunks (default 4 workers, max 8)
- Each worker opens the table independently and reads its row range
- Workers return per-antenna (flagged_count, total_count) arrays
- Main process aggregates across workers

Autocorrelations (ANTENNA1 == ANTENNA2) are excluded by default.
FLAG_CMD subtable is also checked for pre-existing online flag commands.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import re
from typing import Any

import numpy as np

from ms_inspect.util.casa_context import open_table, validate_ms_path
from ms_inspect.util.formatting import field, response_envelope

TOOL_NAME = "ms_antenna_flag_fraction"

# Worker count: read from env, cap at 8
_DEFAULT_WORKERS = 4
_MAX_WORKERS = 8


def _get_n_workers() -> int:
    try:
        n = int(os.environ.get("RADIO_MCP_WORKERS", _DEFAULT_WORKERS))
        return max(1, min(n, _MAX_WORKERS))
    except (ValueError, TypeError):
        return _DEFAULT_WORKERS


# ---------------------------------------------------------------------------
# Worker function — must be module-level for multiprocessing pickle
# ---------------------------------------------------------------------------

def _flag_chunk_worker(args: tuple) -> tuple[np.ndarray, np.ndarray]:
    """
    Worker: opens the MS table, reads FLAG + ANTENNA1 + ANTENNA2 for a row
    range.  Returns (flagged_per_ant, total_per_ant) numpy arrays of shape
    (n_ant,) with int64 counts.

    Autocorrelations (ant1 == ant2) are skipped.

    Following the blacklight pattern: instantiate casatools.table() directly,
    use getcol with startrow/nrow for chunked access.
    """
    ms_path, start_row, n_rows, n_ant = args

    flagged = np.zeros(n_ant, dtype=np.int64)
    total = np.zeros(n_ant, dtype=np.int64)

    from casatools import table  # type: ignore[import]

    tb = table()
    tb.open(ms_path, nomodify=True)

    # FLAG shape: (n_corr, n_chan, n_rows_in_chunk)
    flag_chunk = tb.getcol("FLAG", startrow=start_row, nrow=n_rows)
    ant1 = tb.getcol("ANTENNA1", startrow=start_row, nrow=n_rows)
    ant2 = tb.getcol("ANTENNA2", startrow=start_row, nrow=n_rows)

    tb.close()

    # Mask out autocorrelations
    cross = ant1 != ant2
    if not cross.any():
        return flagged, total

    ant1 = ant1[cross]
    ant2 = ant2[cross]
    flag_chunk = flag_chunk[:, :, cross]  # (n_corr, n_chan, n_cross_rows)

    n_corr, n_chan, n_cross = flag_chunk.shape
    elements_per_row = n_corr * n_chan

    # Sum flagged elements per row: collapse corr and chan axes
    # flag_chunk is bool (n_corr, n_chan, n_cross) → sum over axes 0,1
    flagged_per_row = flag_chunk.sum(axis=(0, 1))  # (n_cross,)

    # Accumulate per-antenna using np.add.at (handles duplicate indices)
    np.add.at(flagged, ant1, flagged_per_row)
    np.add.at(flagged, ant2, flagged_per_row)
    np.add.at(total, ant1, elements_per_row)
    np.add.at(total, ant2, elements_per_row)

    return flagged, total


# ---------------------------------------------------------------------------
# Main tool function
# ---------------------------------------------------------------------------

def run(ms_path: str, exclude_autocorr: bool = True) -> dict:
    """
    Compute per-antenna pre-existing flag fractions.

    Reads the FLAG column in parallel chunks using multiprocessing.
    Also queries the FLAG_CMD subtable for online flag commands per antenna.

    Args:
        ms_path:          Path to Measurement Set.
        exclude_autocorr: If True (default), exclude autocorrelation rows
                          (ANTENNA1 == ANTENNA2) from flag statistics.
    """
    p = validate_ms_path(ms_path)
    ms_str = str(p)
    casa_calls: list[str] = []
    warnings: list[str] = []

    # ------------------------------------------------------------------
    # Read antenna names and count
    # ------------------------------------------------------------------
    with open_table(ms_str + "/ANTENNA") as tb:
        casa_calls.append("tb.open(ANTENNA) → getcol(NAME)")
        ant_names: list[str] = list(tb.getcol("NAME"))
    n_ant = len(ant_names)

    # ------------------------------------------------------------------
    # Get total row count and partition
    # ------------------------------------------------------------------
    with open_table(ms_str) as tb:
        casa_calls.append("tb.open(MS MAIN) → nrows()")
        n_total_rows = tb.nrows()

    if n_total_rows == 0:
        warnings.append("MS MAIN table has zero rows.")
        return response_envelope(
            tool_name=TOOL_NAME,
            ms_path=ms_path,
            data={"per_antenna": [], "overall_flag_fraction": field(None, flag="UNAVAILABLE")},
            warnings=warnings,
            casa_calls=casa_calls,
        )

    n_workers = _get_n_workers()
    chunk_size = max(1, n_total_rows // n_workers)

    chunks: list[tuple] = []
    for i in range(n_workers):
        start = i * chunk_size
        size = chunk_size if i < n_workers - 1 else (n_total_rows - start)
        if size > 0:
            chunks.append((ms_str, start, size, n_ant))

    casa_calls.append(
        f"tb.getcol(FLAG, ANTENNA1, ANTENNA2) "
        f"in {len(chunks)} parallel chunks ({n_workers} workers)"
    )

    # ------------------------------------------------------------------
    # Parallel reads — fork context (safe for casatools table access)
    # ------------------------------------------------------------------
    try:
        ctx = mp.get_context("fork")
        with ctx.Pool(processes=n_workers) as pool:
            chunk_results = pool.map(_flag_chunk_worker, chunks)
    except Exception as e:
        warnings.append(
            f"Parallel FLAG read failed ({e}). Falling back to single-process read."
        )
        chunk_results = [_flag_chunk_worker(chunk) for chunk in chunks]

    # ------------------------------------------------------------------
    # Aggregate
    # ------------------------------------------------------------------
    total_flagged = np.zeros(n_ant, dtype=np.int64)
    total_counts = np.zeros(n_ant, dtype=np.int64)

    for chunk_flagged, chunk_total in chunk_results:
        total_flagged += chunk_flagged
        total_counts += chunk_total

    # ------------------------------------------------------------------
    # FLAG_CMD per-antenna online flag commands
    # ------------------------------------------------------------------
    ant_flag_cmd_counts: dict[int, int] = {i: 0 for i in range(n_ant)}
    try:
        with open_table(ms_str + "/FLAG_CMD") as tb:
            casa_calls.append("tb.open(FLAG_CMD)")
            n_cmd = tb.nrows()
            if n_cmd > 0:
                commands = tb.getcol("COMMAND")
                for cmd in commands:
                    cmd_str = str(cmd)
                    match = re.search(r"antenna\s*=\s*['\"]?(\w+)['\"]?", cmd_str, re.IGNORECASE)
                    if match:
                        ant_name_in_cmd = match.group(1)
                        for i, name in enumerate(ant_names):
                            if name == ant_name_in_cmd:
                                ant_flag_cmd_counts[i] = ant_flag_cmd_counts.get(i, 0) + 1
    except Exception as e:
        warnings.append(f"Could not read FLAG_CMD subtable: {e}")

    # ------------------------------------------------------------------
    # Build per-antenna records
    # ------------------------------------------------------------------
    per_antenna: list[dict] = []
    global_flagged = 0
    global_total = 0

    for i, name in enumerate(ant_names):
        nf = int(total_flagged[i])
        nt = int(total_counts[i])
        global_flagged += nf
        global_total += nt

        frac = nf / nt if nt > 0 else 0.0
        frac_flag = "COMPLETE" if nt > 0 else "UNAVAILABLE"

        per_antenna.append({
            "antenna_id": i,
            "antenna_name": name,
            "flag_fraction": field(round(frac, 6), flag=frac_flag),
            "n_flagged_elements": nf,
            "n_total_elements": nt,
            "n_flag_commands_online": ant_flag_cmd_counts.get(i, 0),
        })

    overall_frac = global_flagged / global_total if global_total > 0 else 0.0

    data = {
        "overall_flag_fraction": field(round(overall_frac, 6)),
        "autocorrelations_excluded": exclude_autocorr,
        "n_workers_used": n_workers,
        "n_total_rows": n_total_rows,
        "flag_source": "FLAG column (parallel read) + FLAG_CMD subtable",
        "per_antenna": per_antenna,
    }

    return response_envelope(
        tool_name=TOOL_NAME,
        ms_path=ms_path,
        data=data,
        warnings=warnings,
        casa_calls=casa_calls,
    )
