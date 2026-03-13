"""
tools/rfi.py — ms_rfi_channel_stats

Layer 3a, Tool 1.

Computes per-SpW, per-channel flag fractions from the FLAG column.
Identifies contiguous bad-channel ranges and annotates them with known
RFI frequency catalogue entries (GPS, GSM, Iridium, etc.).

This is a READ-ONLY diagnostic tool. It does not modify the MS.
Use ms_apply_flags to act on its output.

CASA access:
- tb.getcolslice("FLAG", ...) — chunked parallel read (same strategy as flags.py)
- tb.getcol("DATA_DESC_ID") — map rows to SpW
- tb.open(MS/DATA_DESCRIPTION) — DATA_DESC_ID → spw_id mapping
- tb.open(MS/SPECTRAL_WINDOW) — channel centre frequencies per SpW

Parallel read strategy: same N-worker chunk approach as ms_antenna_flag_fraction.
"""

from __future__ import annotations

import multiprocessing as mp
import os

import numpy as np

from ms_inspect.util.casa_context import open_table, validate_ms_path
from ms_inspect.util.formatting import field, response_envelope

TOOL_NAME = "ms_rfi_channel_stats"

_DEFAULT_WORKERS = 4
_MAX_WORKERS = 8
_DEFAULT_FLAG_THRESHOLD = 0.50  # channel flagged if > 50% of visibilities flagged


# ---------------------------------------------------------------------------
# Known RFI frequency catalogue (MHz)
# Entries: (name, freq_mhz_low, freq_mhz_high, notes)
# ---------------------------------------------------------------------------
_RFI_CATALOGUE: list[tuple[str, float, float, str]] = [
    # L-band / UHF persistent RFI
    ("GSM-900 downlink", 935.0, 960.0, "Mobile telephony (GSM)"),
    ("GSM-900 uplink", 890.0, 915.0, "Mobile telephony (GSM)"),
    ("GPS L1", 1575.2, 1575.7, "GPS L1 C/A signal"),
    ("GPS L2", 1227.4, 1227.7, "GPS L2 signal"),
    ("GLONASS L1", 1598.0, 1606.0, "GLONASS navigation"),
    ("Iridium", 1616.0, 1626.5, "Iridium satellite phones"),
    ("DECT cordless", 1880.0, 1900.0, "Digital cordless telephones"),
    ("INMARSAT", 1525.0, 1559.0, "Geostationary mobile satellite"),
    ("Galileo E1", 1559.0, 1592.0, "Galileo navigation system"),
    ("ADS-B aviation", 1087.5, 1090.5, "Aircraft transponders"),
    ("DME aviation", 960.0, 1215.0, "Distance Measuring Equipment (aviation)"),
    ("L-band radar", 1215.0, 1400.0, "Various L-band radars (intermittent)"),
    # P-band / UHF low
    ("DAB digital radio", 174.0, 240.0, "Digital audio broadcasting (Europe)"),
    ("DVB-T digital TV", 470.0, 862.0, "Digital terrestrial television"),
    ("GSM-1800 downlink", 1805.0, 1880.0, "Mobile telephony (DCS-1800)"),
    # C-band
    ("WiFi 5 GHz", 5150.0, 5850.0, "802.11a/n/ac wireless LAN"),
    # S-band
    ("WiFi 2.4 GHz", 2400.0, 2484.0, "802.11b/g/n wireless LAN"),
    ("Bluetooth", 2400.0, 2485.0, "Bluetooth devices"),
]


def _annotate_freq_mhz(freq_mhz: float) -> list[str]:
    """Return list of RFI source names that overlap the given frequency."""
    hits = []
    for name, lo, hi, _ in _RFI_CATALOGUE:
        if lo <= freq_mhz <= hi:
            hits.append(name)
    return hits


# ---------------------------------------------------------------------------
# Worker function
# ---------------------------------------------------------------------------


def _rfi_chunk_worker(args: tuple) -> dict[int, np.ndarray]:
    """
    Worker: reads FLAG + DATA_DESC_ID for a row range.
    Returns {dd_id: channel_flag_count_array} where each array has length n_chan_max.
    All shapes normalised to n_chan_max (zero-padded) so they can be stacked.
    """
    ms_path, start_row, n_rows, n_chan_max = args

    # dd_id → [flagged_per_channel, total_per_channel]
    dd_flagged: dict[int, np.ndarray] = {}
    dd_total: dict[int, np.ndarray] = {}

    try:
        import casatools  # type: ignore[import]

        tb = casatools.table()
        tb.open(ms_path, nomodify=True)
        try:
            flag_chunk = tb.getcolslice(
                "FLAG", blc=[0, 0], trc=[-1, -1], startrow=start_row, nrow=n_rows
            )
            ddid_chunk = tb.getcol("DATA_DESC_ID", startrow=start_row, nrow=n_rows)
        finally:
            tb.close()

        # flag_chunk shape: [n_corr, n_chan, n_rows_in_chunk]
        n_corr, n_chan, n_chunk_rows = flag_chunk.shape

        # Per-channel flag fraction: collapse over correlations, accumulate per ddid
        # flagged_per_chan[row]: bool array [n_chan] — True if any corr is flagged
        # Use sum over corr axis, then compare to n_corr for "all flagged"
        # We count partial flags: fraction of (corr) elements flagged per (chan, row)

        for row_idx in range(n_chunk_rows):
            ddid = int(ddid_chunk[row_idx])
            row_flags = flag_chunk[:, :, row_idx]  # [n_corr, n_chan]

            # Per-channel: count flagged corr elements
            chan_flagged = row_flags.sum(axis=0).astype(np.int64)  # [n_chan]
            chan_total = np.full(n_chan, n_corr, dtype=np.int64)

            if ddid not in dd_flagged:
                dd_flagged[ddid] = np.zeros(n_chan_max, dtype=np.int64)
                dd_total[ddid] = np.zeros(n_chan_max, dtype=np.int64)

            dd_flagged[ddid][:n_chan] += chan_flagged
            dd_total[ddid][:n_chan] += chan_total

    except Exception:
        pass

    return {k: (dd_flagged[k], dd_total[k]) for k in dd_flagged}


# ---------------------------------------------------------------------------
# Main tool function
# ---------------------------------------------------------------------------


def run(
    ms_path: str,
    flag_threshold: float = _DEFAULT_FLAG_THRESHOLD,
    min_bad_chan_run: int = 1,
) -> dict:
    """
    Compute per-SpW per-channel flag fractions.

    Args:
        ms_path:          Path to Measurement Set.
        flag_threshold:   Fraction above which a channel is considered 'bad' (0–1).
        min_bad_chan_run: Minimum contiguous bad channels to report as a range.
    """
    p = validate_ms_path(ms_path)
    ms_str = str(p)
    casa_calls: list[str] = []
    warnings: list[str] = []

    # ------------------------------------------------------------------
    # Read DATA_DESCRIPTION → SpW mapping
    # ------------------------------------------------------------------
    with open_table(ms_str + "/DATA_DESCRIPTION") as tb:
        casa_calls.append("tb.open(DATA_DESCRIPTION) → getcol(SPECTRAL_WINDOW_ID)")
        dd_to_spw: list[int] = list(tb.getcol("SPECTRAL_WINDOW_ID"))

    # ------------------------------------------------------------------
    # Read SpW channel frequencies
    # ------------------------------------------------------------------
    spw_chan_freqs: dict[int, np.ndarray] = {}
    with open_table(ms_str + "/SPECTRAL_WINDOW") as tb:
        casa_calls.append("tb.open(SPECTRAL_WINDOW) → getcol(CHAN_FREQ, NUM_CHAN)")
        n_spw = tb.nrows()
        for spw_id in range(n_spw):
            try:
                freqs = tb.getcell("CHAN_FREQ", spw_id)
                spw_chan_freqs[spw_id] = np.asarray(freqs)
            except Exception:
                spw_chan_freqs[spw_id] = np.array([])

    # Max channel count across all SpWs — needed for worker array sizing
    n_chan_max = max((len(v) for v in spw_chan_freqs.values()), default=1)

    # ------------------------------------------------------------------
    # Get total row count and partition
    # ------------------------------------------------------------------
    with open_table(ms_str) as tb:
        n_total_rows = tb.nrows()

    if n_total_rows == 0:
        warnings.append("MS MAIN table has zero rows.")
        return response_envelope(
            TOOL_NAME,
            ms_path,
            {"n_spw": n_spw, "per_spw": []},
            warnings=warnings,
            casa_calls=casa_calls,
        )

    n_workers = max(
        1, min(int(os.environ.get("RADIO_MCP_WORKERS", _DEFAULT_WORKERS)), _MAX_WORKERS)
    )
    chunk_size = max(1, n_total_rows // n_workers)
    chunks = []
    for i in range(n_workers):
        start = i * chunk_size
        size = chunk_size if i < n_workers - 1 else (n_total_rows - start)
        if size > 0:
            chunks.append((ms_str, start, size, n_chan_max))

    casa_calls.append(
        f"tb.getcolslice(FLAG) + tb.getcol(DATA_DESC_ID) "
        f"in {len(chunks)} parallel chunks ({n_workers} workers)"
    )

    # ------------------------------------------------------------------
    # Parallel reads
    # ------------------------------------------------------------------
    try:
        ctx = mp.get_context("spawn")
        with ctx.Pool(processes=n_workers) as pool:
            chunk_results = pool.map(_rfi_chunk_worker, chunks)
    except Exception as e:
        warnings.append(f"Parallel FLAG read failed ({e}). Falling back to single-process.")
        chunk_results = [_rfi_chunk_worker(c) for c in chunks]

    # ------------------------------------------------------------------
    # Aggregate: dd_id → (flagged_per_chan, total_per_chan)
    # ------------------------------------------------------------------
    dd_flagged_agg: dict[int, np.ndarray] = {}
    dd_total_agg: dict[int, np.ndarray] = {}
    for result in chunk_results:
        for ddid, (nf, nt) in result.items():
            if ddid not in dd_flagged_agg:
                dd_flagged_agg[ddid] = np.zeros(n_chan_max, dtype=np.int64)
                dd_total_agg[ddid] = np.zeros(n_chan_max, dtype=np.int64)
            dd_flagged_agg[ddid] += nf
            dd_total_agg[ddid] += nt

    # ------------------------------------------------------------------
    # Aggregate DD → SpW (multiple DDs can share a SpW for different pols)
    # ------------------------------------------------------------------
    spw_flagged: dict[int, np.ndarray] = {}
    spw_total: dict[int, np.ndarray] = {}
    for ddid, spw_id in enumerate(dd_to_spw):
        nf = dd_flagged_agg.get(ddid, np.zeros(n_chan_max))
        nt = dd_total_agg.get(ddid, np.zeros(n_chan_max))
        if spw_id not in spw_flagged:
            spw_flagged[spw_id] = np.zeros(n_chan_max, dtype=np.int64)
            spw_total[spw_id] = np.zeros(n_chan_max, dtype=np.int64)
        spw_flagged[spw_id] += nf
        spw_total[spw_id] += nt

    # ------------------------------------------------------------------
    # Build per-SpW result
    # ------------------------------------------------------------------
    per_spw = []
    for spw_id in sorted(spw_chan_freqs.keys()):
        chan_freqs_hz = spw_chan_freqs[spw_id]
        n_chan = len(chan_freqs_hz)
        if n_chan == 0:
            continue

        nf_arr = spw_flagged.get(spw_id, np.zeros(n_chan))[:n_chan]
        nt_arr = spw_total.get(spw_id, np.zeros(n_chan))[:n_chan]

        with np.errstate(divide="ignore", invalid="ignore"):
            frac_arr = np.where(nt_arr > 0, nf_arr / nt_arr, 0.0)

        # Overall SpW stats
        overall_frac = float(nf_arr.sum() / nt_arr.sum()) if nt_arr.sum() > 0 else 0.0
        fully_flagged = bool(np.all(frac_arr >= 1.0))

        # Bad channel ranges (contiguous runs above threshold)
        bad_channels = np.where(frac_arr > flag_threshold)[0]
        bad_ranges = _contiguous_ranges(bad_channels, min_run=min_bad_chan_run)

        # Annotate bad ranges with RFI catalogue
        annotated_ranges = []
        for ch_start, ch_end in bad_ranges:
            freq_lo_mhz = float(chan_freqs_hz[ch_start]) / 1e6
            freq_hi_mhz = float(chan_freqs_hz[min(ch_end, n_chan - 1)]) / 1e6
            rfi_hits = _annotate_freq_range(freq_lo_mhz, freq_hi_mhz)
            annotated_ranges.append(
                {
                    "channel_start": ch_start,
                    "channel_end": ch_end,
                    "freq_start_mhz": round(freq_lo_mhz, 3),
                    "freq_end_mhz": round(freq_hi_mhz, 3),
                    "mean_flag_frac": round(float(frac_arr[ch_start : ch_end + 1].mean()), 4),
                    "rfi_candidates": rfi_hits,
                    "casa_flagdata_cmd": (f"mode='manual' spw='{spw_id}:{ch_start}~{ch_end}'"),
                }
            )

        per_spw.append(
            {
                "spw_id": spw_id,
                "n_channels": n_chan,
                "centre_freq_mhz": field(round(float(chan_freqs_hz[n_chan // 2]) / 1e6, 3)),
                "overall_flag_frac": field(round(overall_frac, 4)),
                "fully_flagged": fully_flagged,
                "n_bad_channels": len(bad_channels),
                "bad_channel_ranges": annotated_ranges,
                "flag_threshold_used": flag_threshold,
            }
        )
        if fully_flagged:
            warnings.append(f"SpW {spw_id} is fully flagged.")

    data = {
        "n_spw": n_spw,
        "flag_threshold": flag_threshold,
        "min_bad_chan_run": min_bad_chan_run,
        "n_total_rows_read": n_total_rows,
        "per_spw": per_spw,
    }

    return response_envelope(
        tool_name=TOOL_NAME,
        ms_path=ms_path,
        data=data,
        warnings=warnings,
        casa_calls=casa_calls,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _contiguous_ranges(indices: np.ndarray, min_run: int = 1) -> list[tuple[int, int]]:
    """Convert an array of indices into (start, end) inclusive ranges."""
    if len(indices) == 0:
        return []
    ranges = []
    start = indices[0]
    prev = indices[0]
    for idx in indices[1:]:
        if idx == prev + 1:
            prev = idx
        else:
            if (prev - start + 1) >= min_run:
                ranges.append((int(start), int(prev)))
            start = idx
            prev = idx
    if (prev - start + 1) >= min_run:
        ranges.append((int(start), int(prev)))
    return ranges


def _annotate_freq_range(freq_lo_mhz: float, freq_hi_mhz: float) -> list[str]:
    """Return RFI source names that overlap [freq_lo_mhz, freq_hi_mhz]."""
    hits = []
    for name, lo, hi, _ in _RFI_CATALOGUE:
        if lo <= freq_hi_mhz and hi >= freq_lo_mhz:
            hits.append(name)
    return hits
