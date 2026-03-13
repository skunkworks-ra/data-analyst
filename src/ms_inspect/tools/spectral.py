"""
tools/spectral.py — ms_spectral_window_list and ms_correlator_config

Layer 1, Tools 5 & 6.

ms_spectral_window_list: Frequency and channel structure of each SpW.
ms_correlator_config:    Dump time and polarization basis summary.

CASA access:
  msmd.nspw(), msmd.chanfreqs(), msmd.chanwidths(), msmd.bandwidths()
  tb → POLARIZATION, DATA_DESCRIPTION subtables
  msmd.exposuretime()
"""

from __future__ import annotations

import numpy as np

from ms_inspect.util.casa_context import open_msmd, open_table, validate_ms_path, validate_subtable
from ms_inspect.util.conversions import (
    corr_codes_to_labels,
    freq_to_band_name,
    hz_to_human,
    is_full_stokes,
    polarization_basis,
)
from ms_inspect.util.formatting import field, response_envelope

TOOL_SPW = "ms_spectral_window_list"
TOOL_CORR = "ms_correlator_config"


def _get_telescope_name(ms_path_str: str) -> str | None:
    """Read telescope name from OBSERVATION subtable. Returns None on failure."""
    try:
        with open_table(ms_path_str + "/OBSERVATION") as tb:
            names = tb.getcol("TELESCOPE_NAME")
            return str(names[0]).strip() if len(names) > 0 else None
    except Exception:
        return None


def _read_polarization_table(ms_path_str: str) -> tuple[dict[int, list[int]], list[str]]:
    """
    Read POLARIZATION subtable.
    Returns:
        pol_id_to_codes: {pol_id: [corr_type_int, ...]}
        casa_calls:      list of CASA call strings for provenance
    """
    casa_calls = [f"tb.open('{ms_path_str}/POLARIZATION')"]
    pol_id_to_codes: dict[int, list[int]] = {}

    with open_table(ms_path_str + "/POLARIZATION") as tb:
        n_rows = tb.nrows()
        for row in range(n_rows):
            corr_types = tb.getcell("CORR_TYPE", row)
            pol_id_to_codes[row] = list(corr_types)

    return pol_id_to_codes, casa_calls


def _read_data_description_table(ms_path_str: str) -> tuple[dict[int, tuple[int, int]], list[str]]:
    """
    Read DATA_DESCRIPTION subtable.
    Returns:
        dd_to_spw_pol: {dd_id: (spw_id, pol_id)}
        casa_calls
    """
    casa_calls = [f"tb.open('{ms_path_str}/DATA_DESCRIPTION')"]
    dd_to_spw_pol: dict[int, tuple[int, int]] = {}

    with open_table(ms_path_str + "/DATA_DESCRIPTION") as tb:
        spw_ids = tb.getcol("SPECTRAL_WINDOW_ID")
        pol_ids = tb.getcol("POLARIZATION_ID")
        for dd_id, (spw_id, pol_id) in enumerate(zip(spw_ids, pol_ids, strict=False)):
            dd_to_spw_pol[dd_id] = (int(spw_id), int(pol_id))

    return dd_to_spw_pol, casa_calls


def run_spectral_window_list(ms_path: str) -> dict:
    """
    Return frequency and channel structure for all spectral windows.
    """
    p = validate_ms_path(ms_path)
    ms_str = str(p)
    casa_calls: list[str] = []
    warnings: list[str] = []

    telescope = _get_telescope_name(ms_str)

    # Read polarization and data_description tables
    validate_subtable(p, "POLARIZATION")
    validate_subtable(p, "DATA_DESCRIPTION")

    pol_id_to_codes, pol_calls = _read_polarization_table(ms_str)
    dd_to_spw_pol, dd_calls = _read_data_description_table(ms_str)
    casa_calls.extend(pol_calls + dd_calls)

    # Build spw_id → pol_id mapping (take first DD entry per SpW)
    spw_to_pol: dict[int, int] = {}
    for _dd_id, (spw_id, pol_id) in dd_to_spw_pol.items():
        if spw_id not in spw_to_pol:
            spw_to_pol[spw_id] = pol_id

    with open_msmd(ms_str) as msmd:
        casa_calls.append("msmd.open()")
        n_spw = msmd.nspw()
        casa_calls.append("msmd.nspw()")

        spws_out: list[dict] = []

        for spw_id in range(n_spw):
            chan_freqs = np.asarray(msmd.chanfreqs(spw_id))  # Hz
            chan_widths = np.asarray(msmd.chanwidths(spw_id))  # Hz (signed)
            n_chan = len(chan_freqs)

            casa_calls.append(f"msmd.chanfreqs({spw_id}), msmd.chanwidths({spw_id})")

            # Channel widths: take absolute value (can be negative = decreasing freq)
            abs_widths = np.abs(chan_widths)

            # Detect non-uniform channel widths
            width_uniform = bool(np.allclose(abs_widths, abs_widths[0], rtol=1e-3))
            chan_width_hz = float(abs_widths[0])

            freq_min = float(chan_freqs.min())
            freq_max = float(chan_freqs.max())
            freq_centre = float(chan_freqs.mean())
            total_bw = float(abs_widths.sum())

            band_name = freq_to_band_name(freq_centre, telescope or "")
            band_flag = "COMPLETE" if (telescope and band_name) else "UNAVAILABLE"
            if not telescope:
                band_flag = "UNAVAILABLE"

            # Single-channel SpW (frequency-averaged)
            if n_chan == 1:
                warnings.append(
                    f"SpW {spw_id} has only 1 channel ({hz_to_human(freq_centre)}). "
                    "This is a frequency-averaged SpW — per-channel bandpass calibration is not possible."
                )

            # Correlations for this SpW
            pol_id = spw_to_pol.get(spw_id)
            if pol_id is not None and pol_id in pol_id_to_codes:
                codes = pol_id_to_codes[pol_id]
                labels = corr_codes_to_labels(codes)
                corr_field = field(labels, flag="COMPLETE")
            else:
                labels = []
                corr_field = field(
                    None, flag="UNAVAILABLE", note=f"No DATA_DESCRIPTION row maps to SpW {spw_id}"
                )

            record: dict = {
                "spw_id": spw_id,
                "centre_freq_hz": field(round(freq_centre, 2)),
                "centre_freq_human": hz_to_human(freq_centre),
                "freq_min_hz": field(round(freq_min, 2)),
                "freq_max_hz": field(round(freq_max, 2)),
                "total_bw_hz": field(round(total_bw, 2)),
                "total_bw_human": hz_to_human(total_bw),
                "n_channels": field(n_chan),
                "channel_width_hz": field(round(chan_width_hz, 2)),
                "channel_width_human": hz_to_human(chan_width_hz),
                "channel_width_uniform": width_uniform,
                "correlations": corr_field,
                "band_name": field(
                    band_name, flag=band_flag, note=None if telescope else "Telescope unknown"
                ),
            }

            if not width_uniform:
                warnings.append(
                    f"SpW {spw_id} has non-uniform channel widths "
                    f"(min {hz_to_human(float(abs_widths.min()))}, "
                    f"max {hz_to_human(float(abs_widths.max()))}). "
                    "This is unusual — verify the data origin."
                )
                record["channel_width_hz"] = field(
                    round(chan_width_hz, 2),
                    flag="PARTIAL",
                    note="Non-uniform widths; value is width of first channel",
                )

            spws_out.append(record)

    data = {
        "n_spw": n_spw,
        "telescope": telescope or "UNKNOWN",
        "spectral_windows": spws_out,
    }

    return response_envelope(
        tool_name=TOOL_SPW,
        ms_path=ms_path,
        data=data,
        warnings=warnings,
        casa_calls=casa_calls,
    )


def run_correlator_config(ms_path: str) -> dict:
    """
    Return correlator dump time, polarization basis, and full-Stokes flag.
    """
    p = validate_ms_path(ms_path)
    ms_str = str(p)
    casa_calls: list[str] = []
    warnings: list[str] = []

    # Read polarization products
    validate_subtable(p, "POLARIZATION")
    pol_id_to_codes, pol_calls = _read_polarization_table(ms_str)
    casa_calls.extend(pol_calls)

    # Collect all unique correlation labels across all pol IDs
    all_labels: list[str] = []
    for codes in pol_id_to_codes.values():
        all_labels.extend(corr_codes_to_labels(codes))
    unique_labels = sorted(set(all_labels))

    pol_basis = polarization_basis(unique_labels)
    full_stokes = is_full_stokes(unique_labels)

    if len(pol_id_to_codes) > 1:
        warnings.append(
            f"Multiple POLARIZATION table rows ({len(pol_id_to_codes)}). "
            "Different SpWs may have different correlation products."
        )

    # Dump (integration) time — use first science scan if possible
    dump_time_s: float | None = None
    dump_flag = "COMPLETE"
    dump_note = None

    with open_msmd(ms_str) as msmd:
        casa_calls.append("msmd.open()")

        n_fields = len(msmd.fieldnames())
        n_scans = len(msmd.scannumbers())
        n_spw = msmd.nspw()
        casa_calls.append("msmd.fieldnames(), msmd.scannumbers(), msmd.nspw()")

        # Try to get dump time from first scan
        try:
            scan_nums = sorted(msmd.scannumbers())
            exp = msmd.exposuretime(scan=scan_nums[0])
            casa_calls.append(f"msmd.exposuretime(scan={scan_nums[0]})")
            if isinstance(exp, dict):
                dump_time_s = float(exp.get("value", float("nan")))
            else:
                dump_time_s = float(exp)
        except Exception as e:
            dump_flag = "UNAVAILABLE"
            dump_note = f"Could not retrieve dump time: {e}"
            warnings.append(dump_note)

    data = {
        "dump_time_s": field(
            round(dump_time_s, 3) if dump_time_s else None, flag=dump_flag, note=dump_note
        ),
        "polarization_basis": field(pol_basis),
        "correlation_products": field(unique_labels),
        "full_stokes": field(
            full_stokes,
            note=(
                "All four correlation products present"
                if full_stokes
                else "Not full-Stokes — polarisation imaging limited"
            ),
        ),
        "n_pol_setups": len(pol_id_to_codes),
        "n_fields": n_fields,
        "n_scans": n_scans,
        "n_spw": n_spw,
    }

    return response_envelope(
        tool_name=TOOL_CORR,
        ms_path=ms_path,
        data=data,
        warnings=warnings,
        casa_calls=casa_calls,
    )
