"""
tools/spw_amp_severity.py — ms_spw_amp_severity

Robust amplitude statistics of a visibility data column, computed PER CHANNEL
and aggregated PER SPW, across all (or selected) fields. The measurement that
answers "which SpWs are RFI-dominated, and how much of each can we discard?"

This is a READ-ONLY diagnostic tool. It does not interpret and it does not flag.
It returns robust statistics (median, MAD, robust-sigma, min, max) and a derived
discardable-fraction estimate. The verdict (flag-channels vs drop-SpW) belongs to
the skill; the flagging belongs to ms_modify.

Works on ANY data column ('CORRECTED_DATA', 'DATA', 'MODEL_DATA'), so it can be
run before and after a flagging/applycal step to measure the delta.

Memory strategy
---------------
Single sequential pass. The data column is read in row-sized chunks, one
DATA_DESC_ID at a time (a chunk spanning DDIDs with different channel counts
cannot form a rectangular array — same constraint as rfi.py). For each
(SpW, channel) the tool keeps:

  * exact running min / max / unflagged-count / flagged-count  (O(n_chan), cheap)
  * a bounded uniform reservoir sample of amplitudes              (memory knob)

Robust statistics are computed from the reservoir at the end. Peak (max) is kept
exactly, outside the reservoir, because it is the RFI headline and a sample can
miss the single worst visibility. Total memory is bounded by
n_chan_total * max_samples_per_chan * 8 bytes, independent of MS row count.

The reservoir uses the random-key method (assign each incoming amplitude a
uniform key in [0,1), keep the K smallest keys seen). This is an exact,
order-independent uniform sample over the unflagged population per channel.
"""

from __future__ import annotations

import numpy as np

from ms_inspect.util.casa_context import open_table, validate_ms_path
from ms_inspect.util.formatting import field as fmt_field
from ms_inspect.util.formatting import normalize_field_sel, response_envelope

TOOL_NAME = "ms_spw_amp_severity"

_DEFAULT_SIGMA = 5.0
_DEFAULT_MAX_SAMPLES = 5000
_DEFAULT_ROW_CHUNK = 20_000
_SEED = 1234  # fixed for reproducibility


class _ChanReservoir:
    """Uniform random-key reservoir + exact min/max/counts for one channel."""

    __slots__ = ("vals", "keys", "vmin", "vmax", "n_unflagged", "n_flagged", "_k")

    def __init__(self, k: int):
        self._k = k
        self.vals = np.empty(0, dtype=np.float64)
        self.keys = np.empty(0, dtype=np.float64)
        self.vmin = np.inf
        self.vmax = -np.inf
        self.n_unflagged = 0
        self.n_flagged = 0

    def add(self, amps: np.ndarray, rng: np.random.Generator) -> None:
        """Add a batch of unflagged amplitudes for this channel."""
        m = amps.size
        if m == 0:
            return
        self.n_unflagged += m
        self.vmin = min(self.vmin, float(amps.min()))
        self.vmax = max(self.vmax, float(amps.max()))
        batch_keys = rng.random(m)
        vals = np.concatenate([self.vals, amps])
        keys = np.concatenate([self.keys, batch_keys])
        if vals.size > self._k:
            keep = np.argpartition(keys, self._k)[: self._k]
            vals = vals[keep]
            keys = keys[keep]
        self.vals = vals
        self.keys = keys

    def stats(self) -> dict | None:
        """Robust statistics from the reservoir, or None if no unflagged data."""
        if self.vals.size == 0:
            return None
        med = float(np.median(self.vals))
        mad = float(np.median(np.abs(self.vals - med)))
        return {
            "median": med,
            "mad": mad,
            "robust_sigma": 1.4826 * mad,
            "min": float(self.vmin),
            "max": float(self.vmax),
            "p95": float(np.percentile(self.vals, 95)),
        }


def _corr_first_axis(arr: np.ndarray) -> np.ndarray:
    """Return amplitude array as [n_chan, n_elements] folding corr + rows together.

    Input getcol shape is [n_corr, n_chan, n_rows]; we want per-channel pooled
    over correlation and row.
    """
    n_corr, n_chan, n_rows = arr.shape
    # → [n_chan, n_corr, n_rows] → [n_chan, n_corr*n_rows]
    return np.transpose(arr, (1, 0, 2)).reshape(n_chan, n_corr * n_rows)


def run(
    ms_path: str,
    datacolumn: str = "CORRECTED_DATA",
    field: str = "",
    sigma: float = _DEFAULT_SIGMA,
    max_samples_per_chan: int = _DEFAULT_MAX_SAMPLES,
    row_chunk: int = _DEFAULT_ROW_CHUNK,
) -> dict:
    """
    Per-channel robust amplitude stats of a data column, aggregated per SpW.

    Args:
        ms_path:              Path to the Measurement Set.
        datacolumn:           Column to measure: 'CORRECTED_DATA' (default),
                              'DATA', or 'MODEL_DATA'. Use the same tool on two
                              columns to compare before/after a step.
        field:                CASA field selection (empty = all fields).
        sigma:                N in the elevation threshold band_floor + N*robust_sigma.
                              Only used to derive the discardable-fraction estimate.
        max_samples_per_chan: Reservoir size per channel (memory knob).
        row_chunk:            Rows read per block (memory knob; smaller = less RAM).

    Returns:
        Standard envelope with per_spw → per-channel robust stats + per-SpW
        aggregate severity numbers. No verdict, no flagging.
    """
    field = normalize_field_sel(field)
    p = validate_ms_path(ms_path)
    ms_str = str(p)
    casa_calls: list[str] = []
    warnings: list[str] = []
    rng = np.random.default_rng(_SEED)

    # ------------------------------------------------------------------
    # DATA_DESCRIPTION → SpW mapping
    # ------------------------------------------------------------------
    with open_table(ms_str + "/DATA_DESCRIPTION") as tb:
        casa_calls.append("tb.open(DATA_DESCRIPTION) → SPECTRAL_WINDOW_ID")
        dd_to_spw: list[int] = [int(x) for x in tb.getcol("SPECTRAL_WINDOW_ID")]

    # ------------------------------------------------------------------
    # SpW channel frequencies
    # ------------------------------------------------------------------
    spw_chan_freqs: dict[int, np.ndarray] = {}
    with open_table(ms_str + "/SPECTRAL_WINDOW") as tb:
        casa_calls.append("tb.open(SPECTRAL_WINDOW) → CHAN_FREQ")
        for spw_id in range(tb.nrows()):
            try:
                spw_chan_freqs[spw_id] = np.asarray(tb.getcell("CHAN_FREQ", spw_id))
            except Exception:
                spw_chan_freqs[spw_id] = np.array([])

    # ------------------------------------------------------------------
    # Optional field selection → set of FIELD_IDs
    # ------------------------------------------------------------------
    field_ids: set[int] | None = None
    if field:
        with open_table(ms_str + "/FIELD") as tb:
            names = list(tb.getcol("NAME"))
        wanted = {n.strip() for n in field.split(",") if n.strip()}
        # accept either names or integer ids
        field_ids = set()
        for sel in wanted:
            if sel.isdigit():
                field_ids.add(int(sel))
            else:
                field_ids.update(i for i, nm in enumerate(names) if nm == sel)
        if not field_ids:
            warnings.append(f"No fields matched selection '{field}'. Measuring all fields.")
            field_ids = None

    # ------------------------------------------------------------------
    # Validate column presence
    # ------------------------------------------------------------------
    with open_table(ms_str) as tb:
        if datacolumn not in set(tb.colnames()):
            from ms_inspect.exceptions import ComputationError

            raise ComputationError(
                f"{datacolumn} column not present in {ms_path}.",
                ms_path=ms_path,
            )

    # ------------------------------------------------------------------
    # Per-(spw, chan) reservoirs. Multiple DDIDs may map to one SpW; we feed
    # each DDID's data into its SpW's reservoirs directly.
    # ------------------------------------------------------------------
    spw_reservoirs: dict[int, list[_ChanReservoir]] = {}
    for spw_id, freqs in spw_chan_freqs.items():
        n_chan = len(freqs)
        if n_chan > 0:
            spw_reservoirs[spw_id] = [
                _ChanReservoir(max_samples_per_chan) for _ in range(n_chan)
            ]

    field_clause = ""
    if field_ids is not None:
        ids = ",".join(str(i) for i in sorted(field_ids))
        field_clause = f" && FIELD_ID IN [{ids}]"

    with open_table(ms_str) as tb:
        for ddid, spw_id in enumerate(dd_to_spw):
            if spw_id not in spw_reservoirs:
                continue
            sub = tb.query(f"DATA_DESC_ID == {ddid}{field_clause}")
            try:
                n_rows = int(sub.nrows())
                if n_rows == 0:
                    continue
                reservoirs = spw_reservoirs[spw_id]
                for start in range(0, n_rows, row_chunk):
                    nrow = min(row_chunk, n_rows - start)
                    data = sub.getcol(datacolumn, startrow=start, nrow=nrow)
                    flag = sub.getcol("FLAG", startrow=start, nrow=nrow)
                    amp = np.abs(data)  # [n_corr, n_chan, nrow]
                    amp_c = _corr_first_axis(amp)  # [n_chan, n_corr*nrow]
                    flag_c = _corr_first_axis(flag.astype(bool))
                    n_chan = amp_c.shape[0]
                    for ch in range(n_chan):
                        good = ~flag_c[ch]
                        reservoirs[ch].n_flagged += int((~good).sum())
                        vals = amp_c[ch][good]
                        # exclude exact zeros (un-populated cells)
                        vals = vals[vals > 0]
                        reservoirs[ch].add(vals, rng)
            finally:
                sub.close()
            casa_calls.append(
                f"tb.query(DATA_DESC_ID=={ddid}{field_clause}) → getcol({datacolumn},FLAG) chunked"
            )

    # ------------------------------------------------------------------
    # Pass-1 reduction: per-channel robust stats + per-SpW floor/threshold.
    # Stored, not yet emitted — pass 2 needs the per-SpW threshold first.
    # ------------------------------------------------------------------
    spw_stats: dict[int, dict] = {}
    for spw_id in sorted(spw_reservoirs.keys()):
        reservoirs = spw_reservoirs[spw_id]
        n_chan = len(reservoirs)
        chan_st: list[dict | None] = [reservoirs[ch].stats() for ch in range(n_chan)]
        chan_medians = [st["median"] for st in chan_st if st is not None]
        chan_sigmas = [st["robust_sigma"] for st in chan_st if st is not None]
        if chan_medians:
            band_floor = float(np.median(chan_medians))
            band_sigma = float(np.median(chan_sigmas))
            threshold = band_floor + sigma * band_sigma
        else:
            band_floor = band_sigma = threshold = None
        spw_stats[spw_id] = {
            "chan_st": chan_st,
            "band_floor": band_floor,
            "band_sigma": band_sigma,
            "threshold": threshold,
        }

    # ------------------------------------------------------------------
    # Clean-floor anchor: median of the lowest-quartile band_floors — a robust
    # estimate of the thermal+source floor taken from the QUIETEST SpWs. Severity
    # is anchored to this, not the overall median, so it stays correct even when
    # much of the band is RFI-dominated (a contaminated median understates it).
    # ------------------------------------------------------------------
    valid_floors = sorted(
        s["band_floor"] for s in spw_stats.values() if s["band_floor"] is not None
    )
    if valid_floors:
        q = max(1, len(valid_floors) // 4)
        clean_floor = float(np.median(valid_floors[:q]))
    else:
        clean_floor = None

    # ------------------------------------------------------------------
    # Assemble per-SpW output
    # ------------------------------------------------------------------
    per_spw: list[dict] = []
    for spw_id in sorted(spw_stats.keys()):
        st = spw_stats[spw_id]
        freqs = spw_chan_freqs[spw_id]
        reservoirs = spw_reservoirs[spw_id]
        chan_st = st["chan_st"]
        n_chan = len(chan_st)
        band_floor = st["band_floor"]
        threshold = st["threshold"]

        chan_records: list[dict] = []
        for ch in range(n_chan):
            cst = chan_st[ch]
            freq_mhz = round(float(freqs[ch]) / 1e6, 3) if ch < len(freqs) else None
            if cst is None:
                chan_records.append(
                    {
                        "chan": ch,
                        "freq_mhz": freq_mhz,
                        "n_unflagged": 0,
                        "n_flagged": reservoirs[ch].n_flagged,
                        "median": fmt_field(None, "UNAVAILABLE", note="no unflagged data"),
                    }
                )
                continue
            chan_disc = float(np.mean(reservoirs[ch].vals > threshold))
            chan_records.append(
                {
                    "chan": ch,
                    "freq_mhz": freq_mhz,
                    "n_unflagged": reservoirs[ch].n_unflagged,
                    "n_flagged": reservoirs[ch].n_flagged,
                    "median": round(cst["median"], 6),
                    "mad": round(cst["mad"], 6),
                    "robust_sigma": round(cst["robust_sigma"], 6),
                    "min": round(cst["min"], 6),
                    "max": round(cst["max"], 6),
                    "p95": round(cst["p95"], 6),
                    "peak_to_floor": (
                        round(cst["max"] / cst["median"], 3) if cst["median"] > 0 else None
                    ),
                    "discardable_frac": round(chan_disc, 4),
                }
            )

        if band_floor is None:
            per_spw.append(
                {
                    "spw_id": spw_id,
                    "n_channels": n_chan,
                    "centre_freq_mhz": (
                        round(float(freqs[n_chan // 2]) / 1e6, 3) if n_chan else None
                    ),
                    "band_floor": fmt_field(None, "UNAVAILABLE", note="no unflagged data"),
                    "severity": None,
                    "per_chan": chan_records,
                }
            )
            warnings.append(f"SpW {spw_id} has no unflagged data in {datacolumn}.")
            continue

        # discardable element fraction over the SpW, weighted by unflagged count.
        num = den = 0.0
        for ch in range(n_chan):
            res = reservoirs[ch]
            if res.vals.size == 0:
                continue
            num += float(np.mean(res.vals > threshold)) * res.n_unflagged
            den += res.n_unflagged
        discardable = (num / den) if den > 0 else 0.0

        severity = (
            round(band_floor / clean_floor, 3) if clean_floor and clean_floor > 0 else None
        )
        ptf_vals = [r["peak_to_floor"] for r in chan_records if r.get("peak_to_floor") is not None]

        per_spw.append(
            {
                "spw_id": spw_id,
                "n_channels": n_chan,
                "centre_freq_mhz": round(float(freqs[n_chan // 2]) / 1e6, 3),
                "band_floor": fmt_field(round(band_floor, 6)),
                "band_robust_sigma": fmt_field(round(st["band_sigma"], 6)),
                "severity": severity,
                "elevation_threshold": round(threshold, 6),
                "estimated_discardable_frac": fmt_field(round(discardable, 4)),
                "max_peak_to_floor": max(ptf_vals) if ptf_vals else None,
                "per_chan": chan_records,
            }
        )

    data = {
        "datacolumn": datacolumn,
        "clean_floor_anchor": round(clean_floor, 6) if clean_floor is not None else None,
        "field": field,
        "sigma": sigma,
        "max_samples_per_chan": max_samples_per_chan,
        "row_chunk": row_chunk,
        "note": (
            "band_floor = median of per-channel medians (robust SpW floor). "
            "clean_floor_anchor = median of the lowest-quartile band_floors (thermal "
            "floor from the quietest SpWs). severity = band_floor / clean_floor_anchor; "
            ">>1 = uniformly RFI-dominated (drop candidate), robust even when much of "
            "the band is bad. estimated_discardable_frac = fraction of unflagged "
            "elements above band_floor + sigma*band_robust_sigma (localized RFI "
            "magnitude). Per-channel discardable_frac localizes the contamination. "
            "Intermittent-vs-persistent (time structure) is out of scope for this "
            "tool — it pools over time; use a time-resolved pass for that."
        ),
        "n_spw": len(per_spw),
        "per_spw": per_spw,
    }

    return response_envelope(
        tool_name=TOOL_NAME,
        ms_path=ms_path,
        data=data,
        warnings=warnings,
        casa_calls=casa_calls,
    )
