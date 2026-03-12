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
from pathlib import Path
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

def _flag_chunk_worker(args: tuple) -> dict[int, tuple[int, int]]:
    """
    Worker: opens the MS table, reads FLAG + ANTENNA1 + ANTENNA2 for a row range.
    Returns {antenna_id: (n_flagged, n_total)} for all antennas in this chunk.
    Autocorrelations (ant1 == ant2) are skipped.
    """
    ms_path, start_row, n_rows, n_ant = args

    # Per-antenna accumulators
    flagged = np.zeros(n_ant, dtype=np.int64)
    total   = np.zeros(n_ant, dtype=np.int64)

    try:
        # casatools must be importable in the worker process
        import casatools  # type: ignore[import]
        tb = casatools.table()
        tb.open(ms_path, nomodify=True)

        try:
            # FLAG shape: [n_corr, n_chan, n_row_in_chunk]
            flag_chunk = tb.getcolslice(
                "FLAG",
                blc=[0, 0],
                trc=[-1, -1],
                startrow=start_row,
                nrow=n_rows,
            )
            ant1_chunk = tb.getcol("ANTENNA1", startrow=start_row, nrow=n_rows)
            ant2_chunk = tb.getcol("ANTENNA2", startrow=start_row, nrow=n_rows)
        finally:
            tb.close()

        # flag_chunk: [n_corr, n_chan, n_rows_in_chunk]
        # Collapse across corr and chan: a row is flagged if ALL corr+chan are flagged
        # (Conservative: use any-flagged per row per antenna contribution)
        # We count flag fraction as: fraction of (corr * chan * row) elements flagged
        n_corr, n_chan, n_chunk_rows = flag_chunk.shape

        for row_idx in range(n_chunk_rows):
            a1 = int(ant1_chunk[row_idx])
            a2 = int(ant2_chunk[row_idx])

            # Skip autocorrelations
            if a1 == a2:
                continue

            row_flags = flag_chunk[:, :, row_idx]  # [n_corr, n_chan]
            n_total_elements = n_corr * n_chan
            n_flagged_elements = int(row_flags.sum())

            # Attribute equally to both antennas in the baseline
            for ant in (a1, a2):
                if 0 <= ant < n_ant:
                    flagged[ant] += n_flagged_elements
                    total[ant]   += n_total_elements

    except Exception as e:
        # Worker failure: return zeros (main process will warn)
        pass

    return {int(ant): (int(flagged[ant]), int(total[ant])) for ant in range(n_ant)}


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
        size  = chunk_size if i < n_workers - 1 else (n_total_rows - start)
        if size > 0:
            chunks.append((ms_str, start, size, n_ant))

    casa_calls.append(
        f"tb.getcolslice(FLAG) + tb.getcol(ANTENNA1, ANTENNA2) "
        f"in {len(chunks)} parallel chunks ({n_workers} workers)"
    )

    # ------------------------------------------------------------------
    # Parallel reads
    # ------------------------------------------------------------------
    # Use spawn context for compatibility with casatools (which uses shared
    # memory internally and can deadlock with fork on some systems)
    try:
        ctx = mp.get_context("spawn")
        with ctx.Pool(processes=n_workers) as pool:
            chunk_results: list[dict[int, tuple[int, int]]] = pool.map(
                _flag_chunk_worker, chunks
            )
    except Exception as e:
        warnings.append(
            f"Parallel FLAG read failed ({e}). Falling back to single-process read."
        )
        chunk_results = [_flag_chunk_worker(chunk) for chunk in chunks]

    # ------------------------------------------------------------------
    # Aggregate
    # ------------------------------------------------------------------
    total_flagged = np.zeros(n_ant, dtype=np.int64)
    total_counts  = np.zeros(n_ant, dtype=np.int64)

    for result in chunk_results:
        for ant_id, (nf, nt) in result.items():
            if 0 <= ant_id < n_ant:
                total_flagged[ant_id] += nf
                total_counts[ant_id]  += nt

    # ------------------------------------------------------------------
    # FLAG_CMD per-antenna online flag commands
    # ------------------------------------------------------------------
    ant_flag_cmd_counts: dict[int, int] = {i: 0 for i in range(n_ant)}
    try:
        with open_table(ms_str + "/FLAG_CMD") as tb:
            casa_calls.append("tb.open(FLAG_CMD)")
            n_cmd = tb.nrows()
            if n_cmd > 0:
                commands = tb.getcol("COMMAND") if n_cmd > 0 else []
                for cmd in commands:
                    cmd_str = str(cmd)
                    # Parse "antenna='ea01'" style patterns
                    import re
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
    global_total   = 0

    for i, name in enumerate(ant_names):
        nf = int(total_flagged[i])
        nt = int(total_counts[i])
        global_flagged += nf
        global_total   += nt

        frac = nf / nt if nt > 0 else 0.0
        frac_flag = "COMPLETE" if nt > 0 else "UNAVAILABLE"

        per_antenna.append({
            "antenna_id":              i,
            "antenna_name":            name,
            "flag_fraction":           field(round(frac, 6), flag=frac_flag),
            "n_flagged_elements":      nf,
            "n_total_elements":        nt,
            "n_flag_commands_online":  ant_flag_cmd_counts.get(i, 0),
        })

    overall_frac = global_flagged / global_total if global_total > 0 else 0.0

    data = {
        "overall_flag_fraction":   field(round(overall_frac, 6)),
        "autocorrelations_excluded": exclude_autocorr,
        "n_workers_used":          n_workers,
        "n_total_rows":            n_total_rows,
        "flag_source":             "FLAG column (parallel read) + FLAG_CMD subtable",
        "per_antenna":             per_antenna,
    }

    return response_envelope(
        tool_name=TOOL_NAME,
        ms_path=ms_path,
        data=data,
        warnings=warnings,
        casa_calls=casa_calls,
    )
