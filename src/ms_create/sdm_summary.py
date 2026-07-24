"""
sdm_summary.py — ms_sdm_summary

Pre-conversion inspection of a raw ASDM/SDM directory. Answers the question
"what is this dataset?" *before* committing to a (potentially multi-GB)
importasdm conversion: telescope, array configuration, band, spectral setup
(continuum vs spectral line), correlation products, sources, scan-intent
balance, and time span.

This is the ingest-side counterpart to ms_observation_info / ms_field_list /
ms_spectral_window_list, which all require an already-converted MS. Reads only
the ASDM XML tables (no casatools dependency, no binary data touched).

Per the project contract: this tool MEASURES. It derives structural quantities
(band, spectral mode, max source elevation) and flags them INFERRED with the
reasoning in a note. It does not emit go/no-go verdicts — that is the Skill's job.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from pathlib import Path

from ms_create.exceptions import ASDMNotFoundError
from ms_inspect.util.conversions import (
    hz_to_human,
    mjd_seconds_to_utc,
    rad_to_dms,
    rad_to_hms,
    seconds_to_human,
)
from ms_inspect.util.formatting import field as fmt_field
from ms_inspect.util.formatting import response_envelope
from ms_inspect.util.telescope import profile_from_name

TOOL_NAME = "ms_sdm_summary"

# VLA site geodetic latitude (deg). Used only for max-elevation geometry.
_VLA_LAT_DEG = 34.0784
# HI 21 cm rest frequency (Hz).
_HI_REST_HZ = 1_420_405_751.0
# ASDM ArrayTime is integer nanoseconds; MJD seconds = ns / 1e9.
_NS_PER_S = 1e9


def _resolve_sdm_dir(sdm_path: str) -> Path:
    """
    Accept either the SDM directory itself (contains ASDM.xml) or a wrapper
    directory containing exactly one such SDM. Returns the directory that
    holds the ASDM XML tables.
    """
    p = Path(sdm_path)
    if not p.exists():
        raise ASDMNotFoundError(f"SDM path not found: {sdm_path}", ms_path=sdm_path)
    if not p.is_dir():
        raise ASDMNotFoundError(f"SDM path is not a directory: {sdm_path}", ms_path=sdm_path)
    if (p / "ASDM.xml").exists():
        return p
    # Look one level down for a single SDM.
    candidates = [c for c in p.iterdir() if c.is_dir() and (c / "ASDM.xml").exists()]
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        raise ASDMNotFoundError(
            f"{sdm_path} contains {len(candidates)} SDMs; point at one explicitly: "
            + ", ".join(c.name for c in candidates),
            ms_path=sdm_path,
        )
    raise ASDMNotFoundError(
        f"No ASDM.xml under {sdm_path} (not a valid SDM directory).", ms_path=sdm_path
    )


def _rows(sdm: Path, table: str) -> list[ET.Element]:
    """Return the <row> elements of an ASDM table, or [] if the file is absent."""
    f = sdm / f"{table}.xml"
    if not f.exists():
        return []
    try:
        root = ET.parse(f).getroot()
    except ET.ParseError:
        return []
    return root.findall("row")


def _text(row: ET.Element, tag: str) -> str | None:
    el = row.find(tag)
    return el.text.strip() if el is not None and el.text else None


def _asdm_array(text: str | None) -> list[str]:
    """
    Parse an ASDM 1-D array literal: "<ndim> <n> v1 v2 ...". Drops the leading
    ndim and dimension sizes, returns the value tokens. Tolerant of scalars.
    """
    if not text:
        return []
    toks = text.split()
    if len(toks) < 2:
        return toks
    try:
        ndim = int(toks[0])
    except ValueError:
        return toks
    return toks[1 + ndim :]


def _classify_spectral_mode(chan_width_hz: float, tot_bw_hz: float, n_chan: int) -> tuple[str, str]:
    """
    Heuristic continuum-vs-line classification for a single SPW.
    Returns (mode, reasoning). EVLA continuum subbands are typically
    1-2 MHz channels over wide subbands; spectral-line setups use narrow
    channels and/or narrow total bandwidth.
    """
    if chan_width_hz and chan_width_hz < 250e3:
        return (
            "spectral_line",
            f"channel width {hz_to_human(chan_width_hz)} < 250 kHz — narrow-channel line setup",
        )
    if tot_bw_hz and tot_bw_hz <= 4e6 and n_chan >= 64:
        return (
            "spectral_line",
            f"total bandwidth {hz_to_human(tot_bw_hz)} over {n_chan} channels — narrow-band line setup",
        )
    return ("continuum", f"channel width {hz_to_human(chan_width_hz)} over {n_chan} channels")


def run(sdm_path: str) -> dict:
    """
    Summarise a raw ASDM/SDM directory prior to conversion.

    Args:
        sdm_path: Path to the SDM directory (contains ASDM.xml) or a wrapper
                  directory containing exactly one SDM.

    Returns:
        Standard response envelope. All measured fields carry COMPLETE;
        derived fields (band, spectral_mode, max elevation) carry INFERRED.
    """
    sdm = _resolve_sdm_dir(sdm_path)
    casa_calls = [f"parse ASDM XML tables under {sdm}"]
    warnings: list[str] = []

    # --- ExecBlock: telescope / config / observer / time -------------------
    eb_rows = _rows(sdm, "ExecBlock")
    telescope = config = observer = None
    n_antennas = None
    start_utc = end_utc = duration = None
    if eb_rows:
        r = eb_rows[0]
        telescope = _text(r, "telescopeName")
        config = _text(r, "configName")
        observer = _text(r, "observerName")
        na = _text(r, "numAntenna")
        n_antennas = int(na) if na and na.isdigit() else None
        st, en = _text(r, "startTime"), _text(r, "endTime")
        if st and en:
            st_s, en_s = float(st) / _NS_PER_S, float(en) / _NS_PER_S
            start_utc, end_utc = mjd_seconds_to_utc(st_s), mjd_seconds_to_utc(en_s)
            duration = seconds_to_human(en_s - st_s)

    # --- SpectralWindow: per-SPW setup -------------------------------------
    spw_rows = _rows(sdm, "SpectralWindow")
    spws = []
    ref_freq_hz = None
    covers_hi = False
    for i, r in enumerate(spw_rows):
        nch = _text(r, "numChan")
        n_chan = int(nch) if nch and nch.isdigit() else 0
        rf = _text(r, "refFreq")
        bw = _text(r, "totBandwidth")
        cw = _text(r, "chanWidth")
        rf_hz = float(rf) if rf else 0.0
        bw_hz = float(bw) if bw else 0.0
        cw_hz = float(cw) if cw else 0.0
        if ref_freq_hz is None and rf_hz:
            ref_freq_hz = rf_hz
        mode, reasoning = _classify_spectral_mode(cw_hz, bw_hz, n_chan)
        # Does this SPW span the HI line? (USB assumed from chanFreqStart upward.)
        fstart = _text(r, "chanFreqStart")
        fstart_hz = float(fstart) if fstart else rf_hz
        lo, hi = (
            min(fstart_hz, fstart_hz + cw_hz * n_chan),
            max(fstart_hz, fstart_hz + cw_hz * n_chan),
        )
        spw_covers_hi = lo <= _HI_REST_HZ <= hi
        covers_hi = covers_hi or spw_covers_hi
        spws.append(
            {
                "index": i,
                "name": _text(r, "name"),
                "n_chan": n_chan,
                "chan_width": hz_to_human(cw_hz) if cw_hz else None,
                "total_bandwidth": hz_to_human(bw_hz) if bw_hz else None,
                "ref_freq": hz_to_human(rf_hz) if rf_hz else None,
                "net_sideband": _text(r, "netSideband"),
                "spectral_mode_inferred": mode,
                "spectral_mode_reasoning": reasoning,
                "covers_hi_21cm": spw_covers_hi,
            }
        )

    # --- Polarization: correlation products --------------------------------
    pol_rows = _rows(sdm, "Polarization")
    corr_products = []
    for r in pol_rows:
        labels = _asdm_array(_text(r, "corrType"))
        if labels and labels not in corr_products:
            corr_products.append(labels)

    # --- Scan intents and source roles -------------------------------------
    scan_rows = _rows(sdm, "Scan")
    intent_counts: dict[str, int] = {}
    source_intents: dict[str, set[str]] = {}
    for r in scan_rows:
        src = _text(r, "sourceName")
        intents = _asdm_array(_text(r, "scanIntent"))
        for it in intents:
            intent_counts[it] = intent_counts.get(it, 0) + 1
        if src is not None:
            source_intents.setdefault(src, set()).update(intents)

    # --- Fields / source directions ----------------------------------------
    field_rows = _rows(sdm, "Field")
    fields = []
    target_decs_deg: list[float] = []
    for r in field_rows:
        name = _text(r, "fieldName")
        coords = _asdm_array(_text(r, "referenceDir"))
        ra_str = dec_str = None
        dec_deg = None
        if len(coords) >= 2:
            try:
                ra_rad, dec_rad = float(coords[0]), float(coords[1])
                ra_str, dec_str = rad_to_hms(ra_rad), rad_to_dms(dec_rad)
                dec_deg = math.degrees(dec_rad)
            except ValueError:
                pass
        roles = sorted(source_intents.get(name, set()))
        is_target = any("OBSERVE_TARGET" in x for x in roles)
        if is_target and dec_deg is not None:
            target_decs_deg.append(dec_deg)
        fields.append(
            {
                "name": name,
                "ra": ra_str,
                "dec": dec_str,
                "intents": roles,
            }
        )

    # --- Derived: band + max target elevation (EVLA geometry only) ----------
    _tp = profile_from_name(telescope) if telescope else None
    band = _tp.band_label(ref_freq_hz) if (_tp and ref_freq_hz) else None
    max_el = None
    if telescope and ("VLA" in telescope.upper()) and target_decs_deg:
        # Max elevation at upper culmination: 90 - |lat - dec|.
        max_el = round(min(90.0 - abs(_VLA_LAT_DEG - d) for d in target_decs_deg), 1)

    # Overall spectral-mode summary across science SPWs.
    modes = {s["spectral_mode_inferred"] for s in spws}
    overall_mode = next(iter(modes)) if len(modes) == 1 else ("mixed" if modes else None)
    if covers_hi:
        warnings.append(
            "A spectral window spans the HI 21 cm rest frequency (1420.4 MHz). "
            "If this is a line observation, cube imaging (specmode='cube') and "
            "continuum subtraction are required — not covered by ms_tclean."
        )

    data = {
        "sdm_dir": fmt_field(str(sdm)),
        "telescope": fmt_field(telescope) if telescope else fmt_field(None, "UNAVAILABLE"),
        "array_config": fmt_field(config) if config else fmt_field(None, "UNAVAILABLE"),
        "observer": fmt_field(observer) if observer else fmt_field(None, "UNAVAILABLE"),
        "n_antennas": fmt_field(n_antennas)
        if n_antennas is not None
        else fmt_field(None, "UNAVAILABLE"),
        "start_utc": fmt_field(start_utc) if start_utc else fmt_field(None, "UNAVAILABLE"),
        "end_utc": fmt_field(end_utc) if end_utc else fmt_field(None, "UNAVAILABLE"),
        "duration": fmt_field(duration) if duration else fmt_field(None, "UNAVAILABLE"),
        "band_inferred": fmt_field(band, "INFERRED", note="from SPW reference frequency")
        if band
        else fmt_field(None, "UNAVAILABLE", note="telescope or frequency missing"),
        "n_spw": fmt_field(len(spws)),
        "spectral_windows": fmt_field(spws),
        "spectral_mode_inferred": fmt_field(
            overall_mode,
            "INFERRED",
            note="heuristic from channel width / bandwidth; see per-SPW reasoning",
        )
        if overall_mode
        else fmt_field(None, "UNAVAILABLE"),
        "covers_hi_21cm": fmt_field(covers_hi),
        "correlation_products": fmt_field(corr_products)
        if corr_products
        else fmt_field(None, "UNAVAILABLE"),
        "scan_intent_counts": fmt_field(intent_counts),
        "n_scans": fmt_field(len(scan_rows)),
        "fields": fmt_field(fields),
        "target_max_elevation_deg": fmt_field(
            max_el,
            "INFERRED",
            note="upper-culmination geometry 90-|lat-dec| at VLA latitude 34.08 deg; "
            "ignores horizon mask and atmospheric refraction",
        )
        if max_el is not None
        else fmt_field(None, "UNAVAILABLE", note="non-VLA or no target direction"),
    }

    return response_envelope(
        tool_name=TOOL_NAME,
        ms_path=str(sdm),
        data=data,
        warnings=warnings,
        casa_calls=casa_calls,
    )
