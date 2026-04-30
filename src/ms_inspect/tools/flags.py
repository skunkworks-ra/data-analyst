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

FLAG_CMD subtable is read in full (REASON, APPLIED, TYPE, TIME, INTERVAL,
COMMAND) to provide flag provenance: how many flags came from the telescope
online system at import vs user-applied commands.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import re

import numpy as np

from ms_inspect.util.casa_context import open_table, validate_ms_path
from ms_inspect.util.formatting import field, response_envelope

TOOL_NAME = "ms_antenna_flag_fraction"
PREFLIGHT_TOOL_NAME = "ms_flag_preflight"

# Worker count: read from env, cap at 8
_DEFAULT_WORKERS = 4
_MAX_WORKERS = 8
# Minimum rows per worker — below this threshold single-process is faster
_MIN_ROWS_PER_WORKER = 100_000
# Empirical throughput estimate for FLAG column reads (rows/second, single process)
# Calibrated on a 2-SPW, 64-channel, 4-corr VLA dataset. Adjust if needed.
_ROWS_PER_SECOND = 50_000


def _get_n_workers() -> int:
    try:
        n = int(os.environ.get("RADIO_MCP_WORKERS", _DEFAULT_WORKERS))
        return max(1, min(n, _MAX_WORKERS))
    except (ValueError, TypeError):
        return _DEFAULT_WORKERS


def _recommended_workers(n_rows: int) -> int:
    """
    Return the recommended worker count for a given row count.

    Collapses to 1 for small MSs where fork overhead exceeds read time.
    Caps at the env-configured maximum.
    """
    env_cap = _get_n_workers()
    ideal = max(1, n_rows // _MIN_ROWS_PER_WORKER)
    return min(ideal, env_cap)


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
# Preflight probe — fast, no FLAG column read
# ---------------------------------------------------------------------------


def run_preflight(ms_path: str) -> dict:
    """
    Fast pre-flight probe: return data volume and worker recommendation
    WITHOUT reading the FLAG column.

    Use this before ms_antenna_flag_fraction to:
    - Estimate wall-clock runtime (warn user if > threshold)
    - Determine the optimal worker count to pass as n_workers

    Args:
        ms_path: Path to Measurement Set.
    """
    p = validate_ms_path(ms_path)
    ms_str = str(p)
    casa_calls: list[str] = []
    warnings: list[str] = []

    # Row count
    with open_table(ms_str) as tb:
        casa_calls.append("tb.open(MS MAIN) → nrows()")
        n_rows = tb.nrows()

    # FLAG column shape: (n_corr, n_chan) per row
    # getcolshapestring returns e.g. "[4, 64]" — parse it
    n_corr, n_chan = 0, 0
    try:
        with open_table(ms_str) as tb:
            casa_calls.append("tb.open(MS MAIN) → getcolshapestring(FLAG)")
            shape_str = tb.getcolshapestring("FLAG")
            # shape_str is a list of identical strings like ["[4, 64]", ...]
            sample = shape_str[0] if shape_str else "[]"
            parts = [int(x) for x in sample.strip("[]").split(",")]
            n_corr = parts[0] if len(parts) > 0 else 0
            n_chan = parts[1] if len(parts) > 1 else 0
    except Exception as e:
        warnings.append(f"Could not read FLAG column shape: {e}")

    # SpW count from SPECTRAL_WINDOW subtable
    n_spw = 0
    try:
        with open_table(ms_str + "/SPECTRAL_WINDOW") as tb:
            casa_calls.append("tb.open(SPECTRAL_WINDOW) → nrows()")
            n_spw = tb.nrows()
    except Exception as e:
        warnings.append(f"Could not read SPECTRAL_WINDOW: {e}")

    # Data volume: FLAG column is 1 byte/element (bool stored as uchar)
    n_elements = n_rows * n_corr * n_chan
    data_volume_gb = round(n_elements / 1e9, 3)

    # Runtime estimate — single-process baseline, parallel scales approximately linearly
    recommended = _recommended_workers(n_rows)
    effective_workers = max(1, recommended)
    estimated_s = int(n_rows / (_ROWS_PER_SECOND * effective_workers)) if n_rows > 0 else 0
    estimated_min = round(estimated_s / 60, 1)

    will_parallelize = recommended > 1

    data = {
        "n_rows": field(n_rows),
        "flag_col_shape": field({"n_corr": n_corr, "n_chan": n_chan}),
        "n_spw": field(n_spw),
        "data_volume_gb": field(data_volume_gb),
        "estimated_runtime_s": field(estimated_s),
        "estimated_runtime_min": field(estimated_min),
        "recommended_workers": field(recommended),
        "will_parallelize": field(will_parallelize),
    }

    if estimated_min > 10:
        warnings.append(
            f"FLAG column read estimated at {estimated_min} min "
            f"({data_volume_gb} GB, {recommended} worker(s)). "
            "Warn the user before proceeding."
        )

    return response_envelope(
        tool_name=PREFLIGHT_TOOL_NAME,
        ms_path=ms_path,
        data=data,
        warnings=warnings,
        casa_calls=casa_calls,
    )


# ---------------------------------------------------------------------------
# Main tool function
# ---------------------------------------------------------------------------


def run(
    ms_path: str,
    exclude_autocorr: bool = True,
    n_workers: int | None = None,
    verbosity: str = "full",
) -> dict:
    """
    Compute per-antenna pre-existing flag fractions.

    Reads the FLAG column in parallel chunks using multiprocessing.
    Also queries the FLAG_CMD subtable for online flag commands per antenna.

    Args:
        ms_path:          Path to Measurement Set.
        exclude_autocorr: If True (default), exclude autocorrelation rows
                          (ANTENNA1 == ANTENNA2) from flag statistics.
        n_workers:        Worker count override. If None (default), computed
                          adaptively from row count via ms_flag_preflight
                          recommendation. Pass 1 to force single-process.
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

    if n_workers is not None:
        n_workers = max(1, min(n_workers, _MAX_WORKERS))
    else:
        n_workers = _recommended_workers(n_total_rows)
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
    # Parallel reads — spawn context (avoids inheriting asyncio event loop state)
    # ------------------------------------------------------------------
    try:
        ctx = mp.get_context("spawn")
        with ctx.Pool(processes=n_workers) as pool:
            chunk_results = pool.map(_flag_chunk_worker, chunks)
    except Exception as e:
        warnings.append(f"Parallel FLAG read failed ({e}). Falling back to single-process read.")
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
    # FLAG_CMD — full provenance read
    # Columns: REASON, APPLIED, TYPE, TIME, INTERVAL, COMMAND
    # REASON="ONLINE"  → telescope flagged at import (importasdm online=True)
    # REASON=""        → unspecified (older MSs, UVFITS conversions)
    # REASON=anything  → user or pipeline label
    # APPLIED=False    → command written but not yet applied (partial import)
    # ------------------------------------------------------------------
    flag_cmd_summary: dict = {
        "n_total": field(0),
        "n_applied": field(0),
        "n_unapplied": field(0),
        "by_reason": field({}),
    }
    # per-antenna: {ant_idx: {reason_str: count}}
    ant_cmd_by_reason: dict[int, dict[str, int]] = {i: {} for i in range(n_ant)}

    try:
        with open_table(ms_str + "/FLAG_CMD") as tb:
            casa_calls.append(
                "tb.open(FLAG_CMD) → getcol(REASON, APPLIED, TYPE, TIME, INTERVAL, COMMAND)"
            )
            n_cmd = tb.nrows()
            if n_cmd > 0:
                reasons = list(tb.getcol("REASON"))
                applied = list(tb.getcol("APPLIED"))
                commands = list(tb.getcol("COMMAND"))
                # TYPE and TIME/INTERVAL exist but are not needed for the summary

                # Build reason breakdown
                by_reason: dict[str, dict[str, int]] = {}
                for reason, is_applied in zip(reasons, applied, strict=False):
                    r = str(reason).strip() or "UNSPECIFIED"
                    if r not in by_reason:
                        by_reason[r] = {"n_total": 0, "n_applied": 0}
                    by_reason[r]["n_total"] += 1
                    if is_applied:
                        by_reason[r]["n_applied"] += 1

                n_applied_total = sum(1 for a in applied if a)

                flag_cmd_summary = {
                    "n_total": field(n_cmd),
                    "n_applied": field(n_applied_total),
                    "n_unapplied": field(
                        n_cmd - n_applied_total,
                        note="Unapplied commands may indicate a partial or interrupted import",
                    ),
                    "by_reason": field(by_reason),
                }

                # Per-antenna attribution: COMMAND string still carries antenna=<name>
                # Only attribute commands that explicitly name an antenna
                for reason, cmd_str in zip(reasons, commands, strict=False):
                    r = str(reason).strip() or "UNSPECIFIED"
                    match = re.search(
                        r"antenna\s*=\s*['\"]?([^'\",\s\)]+)['\"]?",
                        str(cmd_str),
                        re.IGNORECASE,
                    )
                    if match:
                        ant_name_in_cmd = match.group(1)
                        for i, name in enumerate(ant_names):
                            if name == ant_name_in_cmd:
                                ant_cmd_by_reason[i][r] = ant_cmd_by_reason[i].get(r, 0) + 1
    except Exception as e:
        warnings.append(f"Could not read FLAG_CMD subtable: {e}")
        flag_cmd_summary["by_reason"] = field(None, flag="UNAVAILABLE")

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

        cmd_breakdown = ant_cmd_by_reason.get(i, {})
        per_antenna.append(
            {
                "antenna_id": i,
                "antenna_name": name,
                "flag_fraction": field(round(frac, 6), flag=frac_flag),
                "n_flagged_elements": nf,
                "n_total_elements": nt,
                "flag_cmd": {
                    "n_attributed": sum(cmd_breakdown.values()),
                    "by_reason": cmd_breakdown,
                },
            }
        )

    overall_frac = global_flagged / global_total if global_total > 0 else 0.0

    # Compact verbosity: strip field() wrappers, roll up non-COMPLETE fields
    if verbosity == "compact":
        compact_per_antenna: list[dict] = []
        incomplete_fields: list[dict] = []
        for rec in per_antenna:
            compact_rec: dict = {}
            for k, v in rec.items():
                if isinstance(v, dict) and "value" in v and "flag" in v:
                    if v["flag"] != "COMPLETE":
                        incomplete_fields.append(
                            {
                                "path": f"per_antenna[{rec['antenna_id']}].{k}",
                                "flag": v["flag"],
                                "note": v.get("note"),
                            }
                        )
                    compact_rec[k] = v["value"]
                else:
                    compact_rec[k] = v
            compact_per_antenna.append(compact_rec)

        data = {
            "overall_flag_fraction": round(overall_frac, 6),
            "autocorrelations_excluded": exclude_autocorr,
            "n_workers_used": n_workers,
            "n_total_rows": n_total_rows,
            "flag_cmd_summary": flag_cmd_summary,
            "per_antenna": compact_per_antenna,
            "incomplete_fields": incomplete_fields,
        }
    else:
        data = {
            "overall_flag_fraction": field(round(overall_frac, 6)),
            "autocorrelations_excluded": exclude_autocorr,
            "n_workers_used": n_workers,
            "n_total_rows": n_total_rows,
            "flag_cmd_summary": flag_cmd_summary,
            "per_antenna": per_antenna,
        }

    return response_envelope(
        tool_name=TOOL_NAME,
        ms_path=ms_path,
        data=data,
        warnings=warnings,
        casa_calls=casa_calls,
    )
