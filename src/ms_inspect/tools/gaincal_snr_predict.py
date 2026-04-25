"""
tools/gaincal_snr_predict.py — ms_gaincal_snr_predict

Predictive SNR for gaincal(solint='inf'). Per-(antenna, SpW) SNR formula:

    SNR = S_jy / (SEFD / sqrt(2 * bw_hz * t_on_src_s * n_baselines_per_ant))
    where n_baselines_per_ant = n_ant - 1

No new CASA table writes. Read-only.
"""

from __future__ import annotations

import math

import numpy as np

from ms_inspect.util.calibrators import lookup as cal_lookup
from ms_inspect.util.casa_context import open_msmd, open_table, validate_ms_path
from ms_inspect.util.conversions import freq_to_band_name
from ms_inspect.util.formatting import field as fmt_field
from ms_inspect.util.formatting import response_envelope

TOOL_NAME = "ms_gaincal_snr_predict"

# SEFD table — verbatim from 11-imaging.md §Step 7
SEFD_JY: dict[str, dict[str, float]] = {
    "EVLA": {"P": 2600, "L": 420, "S": 370, "C": 310, "X": 280},
    "MeerKAT": {"L": 400, "S": 380, "C": 420},
    "uGMRT": {"P": 1800, "L": 600, "S": 560},
}

# Telescope name normalisation — OBSERVATION.TELESCOPE_NAME values
_TELESCOPE_ALIAS: dict[str, str] = {
    "EVLA": "EVLA",
    "VLA": "EVLA",
    "JVLA": "EVLA",
    "MEERKAT": "MeerKAT",
    "MEERKAT+": "MeerKAT",
    "UGMRT": "uGMRT",
    "GMRT": "uGMRT",
}


def _normalise_telescope(name: str) -> str | None:
    return _TELESCOPE_ALIAS.get(name.upper().strip())


def run(
    ms_path: str,
    field_name: str,
    solint_seconds: float = -1.0,
    snr_threshold: float = 3.0,
    flux_jy: float | None = None,
) -> dict:
    """
    Predictive SNR for gaincal(solint='inf') per (antenna, SpW).

    Args:
        ms_path:        Path to the MS.
        field_name:     Calibrator field name (looked up in bundled catalogue
                        for metadata only — NOT for flux density).
        solint_seconds: Solution interval in seconds; -1 = use scan length.
        snr_threshold:  SNR below which a SpW is flagged as low (default 3.0).
        flux_jy:        Stokes I flux density in Jy. Required for a numeric
                        prediction; if None, returns UNAVAILABLE per the
                        tools-measure-never-guess contract.
    """
    p = validate_ms_path(ms_path)
    ms_str = str(p)
    casa_calls: list[str] = []
    warnings: list[str] = []

    # 1. Read telescope name
    telescope_raw: str = "UNKNOWN"
    try:
        with open_table(ms_str + "/OBSERVATION") as tb:
            casa_calls.append("tb.open(OBSERVATION) → TELESCOPE_NAME")
            names = tb.getcol("TELESCOPE_NAME")
            telescope_raw = str(names[0]).strip() if len(names) > 0 else "UNKNOWN"
    except Exception as e:
        warnings.append(f"Could not read telescope name: {e}")

    telescope = _normalise_telescope(telescope_raw)
    if telescope is None:
        warnings.append(
            f"Telescope '{telescope_raw}' not in SEFD table. SNR prediction unavailable."
        )

    # 2. Flux density — must be supplied by caller.
    # The bundled catalogue stores role/notes only; not flux. A predictive
    # tool without a numeric flux would be misleading, so return UNAVAILABLE
    # per the "tools measure, never guess" contract.
    cal_entry = cal_lookup(field_name)  # for metadata annotation only
    cal_note: str | None = None
    if cal_entry is None:
        cal_note = "Field not in bundled calibrator catalogue (metadata only)."

    if flux_jy is None or flux_jy <= 0:
        warnings.append(
            "flux_jy not provided. Run ms_setjy first, read the derived flux "
            "density for this field at the observation band, and call this tool "
            "again with flux_jy set."
        )
        data = {
            "telescope": telescope_raw,
            "calibrator": field_name,
            "calibrator_flux_jy": fmt_field(
                None,
                flag="UNAVAILABLE",
                note="flux_jy not provided; required for numeric SNR prediction",
            ),
            "solint_seconds": fmt_field(None, flag="UNAVAILABLE"),
            "snr_threshold": snr_threshold,
            "n_ant": fmt_field(None, flag="UNAVAILABLE"),
            "per_spw": [],
            "n_spw_below_threshold": 0,
            "fraction_below_threshold": fmt_field(None, flag="UNAVAILABLE"),
            "recommendation_hint": "unavailable",
        }
        return response_envelope(
            tool_name=TOOL_NAME,
            ms_path=ms_path,
            data=data,
            warnings=warnings,
            casa_calls=casa_calls,
        )

    # 3. Read SpW info and n_ant from msmd
    n_ant = 0
    spw_info: list[dict] = []

    with open_msmd(ms_str) as msmd:
        casa_calls.append("msmd.open() → nspw, chanfreqs, nbaselines")
        n_spw = msmd.nspw()
        n_ant = len(msmd.antennanames())

        for spw_id in range(n_spw):
            chan_freqs = np.asarray(msmd.chanfreqs(spw_id))
            chan_widths = np.asarray(np.abs(msmd.chanwidths(spw_id)))
            centre_hz = float(chan_freqs.mean())
            bw_hz = float(chan_widths.sum())
            band = freq_to_band_name(centre_hz, telescope_raw) if telescope else None
            spw_info.append(
                {
                    "spw_id": spw_id,
                    "centre_hz": centre_hz,
                    "bw_hz": bw_hz,
                    "band": band,
                }
            )

        # 4. Resolve solint
        t_solint: float | None = None
        if solint_seconds > 0:
            t_solint = solint_seconds
        else:
            # Use scan length for the field (heuristic: first scan matching field_name)
            try:
                scan_nums = sorted(msmd.scannumbers())
                for scan in scan_nums:
                    scan_fields = [msmd.fieldnames()[f] for f in msmd.fieldsforscan(scan)]
                    if any(field_name.lower() in fn.lower() for fn in scan_fields):
                        times = msmd.timesforscans([scan])
                        if len(times) > 0:
                            t_solint = float(times.max() - times.min())
                        break
                if t_solint is None or t_solint <= 0:
                    # Fallback: use first scan duration
                    times = msmd.timesforscans([scan_nums[0]])
                    t_solint = float(times.max() - times.min()) if len(times) > 0 else 60.0
            except Exception as e:
                t_solint = 60.0
                warnings.append(f"Could not derive scan length, defaulting to 60s: {e}")
        casa_calls.append("msmd.timesforscans() for solint estimation")

    # 5. Compute per-SpW SNR
    n_baselines_per_ant = max(n_ant - 1, 1)
    per_spw: list[dict] = []
    n_below = 0

    for spw in spw_info:
        band = spw["band"]
        sefd_jy: float | None = None
        if telescope and band:
            sefd_jy = SEFD_JY.get(telescope, {}).get(band)

        if sefd_jy is None or t_solint is None or t_solint <= 0:
            per_spw.append(
                {
                    "spw_id": spw["spw_id"],
                    "band": band,
                    "sefd_jy": None,
                    "predicted_snr": None,
                }
            )
            continue

        bw_hz = spw["bw_hz"]
        snr = flux_jy / (sefd_jy / math.sqrt(2.0 * bw_hz * t_solint * n_baselines_per_ant))
        per_spw.append(
            {
                "spw_id": spw["spw_id"],
                "band": band,
                "sefd_jy": sefd_jy,
                "predicted_snr": round(snr, 2),
            }
        )
        if snr < snr_threshold:
            n_below += 1

    n_with_snr = sum(1 for s in per_spw if s["predicted_snr"] is not None)
    frac = n_below / n_with_snr if n_with_snr > 0 else 0.0

    if frac == 0:
        hint = "all_pass"
    elif frac < 0.2:
        hint = "tighten_solint"
    else:
        hint = "exclude_antennas_or_relax"

    data = {
        "telescope": telescope_raw,
        "calibrator": field_name,
        "calibrator_flux_jy": fmt_field(
            flux_jy,
            flag="COMPLETE",
            note=cal_note,
        ),
        "solint_seconds": fmt_field(round(t_solint, 1) if t_solint else None),
        "snr_threshold": snr_threshold,
        "n_ant": n_ant,
        "per_spw": per_spw,
        "n_spw_below_threshold": n_below,
        "fraction_below_threshold": round(frac, 3),
        "recommendation_hint": hint,
    }

    return response_envelope(
        tool_name=TOOL_NAME,
        ms_path=ms_path,
        data=data,
        warnings=warnings,
        casa_calls=casa_calls,
    )
