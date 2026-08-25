"""
util/frequencies.py — Per-field observing frequency, read from the MS.

Lives in util rather than in a tool because two tools need the identical
answer: ms_field_list reports it, and ms_setjy gates the flux standard on it.
A second copy would be free to drift.

No interpretation here — GHz in, GHz out.
"""

from __future__ import annotations

import numpy as np


def field_frequencies(msmd, n_fields: int) -> list[dict]:
    """
    Observed frequency coverage per field, in GHz.

    Returns one dict per field id with keys ``min_ghz``, ``max_ghz``,
    ``centre_ghz``, ``n_spw`` and ``excluded_spw`` — or all-None when the field
    has no usable spectral window.

    Two deliberate choices:

    - Coverage is the span across the SpWs THIS field was observed in, not the
      MS-wide span. A flux calibrator observed only in a subset of the SpWs must
      be judged on that subset.
    - ALMA water-vapour-radiometer and square-law-detector windows are dropped
      where the MS declares them. A WVR window sits near 183 GHz and would
      otherwise drag the reported span far off the science band. The count of
      what was dropped is reported rather than hidden.
    """
    skip_spws: set[int] = set()
    for probe in ("wvrspws", "almaspws"):
        try:
            fn = getattr(msmd, probe)
            skip_spws.update(int(s) for s in (fn(sqld=True) if probe == "almaspws" else fn()))
        except Exception:
            # Not an ALMA MS, or this msmd build has no such accessor. Neither is
            # an error: a non-ALMA MS has no WVR windows to exclude.
            continue

    out: list[dict] = []
    for fid in range(n_fields):
        empty = {
            "min_ghz": None,
            "max_ghz": None,
            "centre_ghz": None,
            "n_spw": 0,
            "excluded_spw": 0,
        }
        try:
            spws = [int(s) for s in msmd.spwsforfield(fid)]
        except Exception:
            out.append(empty)
            continue

        kept = [s for s in spws if s not in skip_spws]
        n_excluded = len(spws) - len(kept)

        lo: float | None = None
        hi: float | None = None
        for spw in kept:
            try:
                freqs = np.asarray(msmd.chanfreqs(spw), dtype=float)
            except Exception:
                continue
            freqs = freqs[np.isfinite(freqs)]
            if freqs.size == 0:
                continue
            f_lo = float(freqs.min()) / 1e9
            f_hi = float(freqs.max()) / 1e9
            lo = f_lo if lo is None else min(lo, f_lo)
            hi = f_hi if hi is None else max(hi, f_hi)

        if lo is None or hi is None:
            empty["excluded_spw"] = n_excluded
            out.append(empty)
            continue

        out.append(
            {
                "min_ghz": lo,
                "max_ghz": hi,
                # Midpoint of the observed span, NOT a bandwidth-weighted mean.
                # It exists to name the band in one number; the gate should use
                # min/max, because a span can straddle a model's edge.
                "centre_ghz": 0.5 * (lo + hi),
                "n_spw": len(kept),
                "excluded_spw": n_excluded,
            }
        )
    return out
