"""
tools/polcal_recovery.py — ms_polcal_recovery

Posterior verification of a polarisation calibration.

This is the check the contract prefers over a prior feasibility gate
(DESIGN.md 1.1.1, "prefer posterior verification to prior permission"). Instead of
asking beforehand whether polarisation calibration is permitted, it measures
afterwards whether the known answer came back:

  - recovered Stokes I against the MODEL_DATA flux density that setjy applied
  - recovered fractional polarisation against MODEL and against the catalogue
  - recovered EVPA against MODEL and against the catalogue
  - residual Stokes V, the direct observable for uncorrected leakage
  - D-term amplitudes from a Df caltable against the expected few percent

Why this is a better check than a gate. Its failure is loud, specific about which
quantity broke, and continuous: an 8 degree EVPA error is fatal for a
rotation-measure programme and tolerable for fractional-polarisation morphology,
and only the Skill knows which one it is looking at. A prior GO/NO-GO could not
express that, and when it failed it failed silently.

Two independent references are returned, deliberately:

  MODEL_DATA is the value that was actually APPLIED. Comparing CORRECTED against
  it closes the loop on the flux-scale trap: if setjy left MODEL pinned at the
  default 1 Jy, the I ratio shows it here even though every solve "succeeded".

  util/pol_calibrators is an INDEPENDENT catalogue value. It catches the case
  where MODEL itself was set wrong, which MODEL-relative agreement cannot.

Absolute Stokes I matters on its own. Fractional polarisation divides the flux
error out, so frac_pol can look perfect while sitting on an I that is wrong by a
factor of two.

Returns measurements, derived residuals with both of their inputs, and reference
values as labelled constants. No verdict.
"""

from __future__ import annotations

import math

import numpy as np

from ms_inspect.util.casa_context import open_table, validate_ms_path
from ms_inspect.util.conversions import corr_codes_to_labels
from ms_inspect.util.formatting import field, response_envelope
from ms_inspect.util.pol_calibrators import lookup_pol, pol_properties_at_freq

TOOL_NAME = "ms_polcal_recovery"

# Expected magnitude of VLA instrumental leakage, as a labelled reference. Not a
# test: D-terms run a few percent at low frequency and rise toward band edges and
# at high frequency, so a single pass/fail threshold would be wrong at one end of
# the range or the other. Compare and reason in skill 09.
DTERM_TYPICAL_MAX_FRAC = 0.05
DTERM_SUSPECT_FRAC = 0.20
DTERM_REFERENCE_SOURCE = (
    "NRAO VLA polarimetry guide: instrumental leakage typically a few percent, "
    "rising at band edges; amplitudes above ~20% usually mean the solve absorbed "
    "something that is not leakage"
)

# Epoch for the independent catalogue cross-check.
POL_DATA_EPOCH = "2019"


def stokes_from_corr(
    vis: dict[str, complex],
) -> tuple[dict[str, complex | None], str, str | None]:
    """
    Convert per-correlation visibilities to Stokes I, Q, U, V.

    Args:
        vis: {corr_label: complex} for one (field, SpW), already averaged.

    Returns:
        (stokes, basis, note). `stokes` values are None where the required
        correlations are absent. `basis` is 'linear', 'circular', or 'unknown'.

    Conventions (CASA / AIPS):
        linear   I = (XX + YY)/2   Q = (XX - YY)/2
                 U = (XY + YX)/2   V = (XY - YX)/(2i)
        circular I = (RR + LL)/2   V = (RR - LL)/2
                 Q = (RL + LR)/2   U = (RL - LR)/(2i)

    Note the asymmetry: in the linear basis the parallel hands carry Q and the
    cross hands carry U and V; in the circular basis it is the other way round.
    Getting this backwards silently swaps Q with U, which rotates every EVPA by
    45 degrees, so the basis is returned alongside for the reader to check.
    """
    have = set(vis)
    if {"XX", "YY"} <= have:
        basis = "linear"
        i_val = (vis["XX"] + vis["YY"]) / 2.0
        q_val = (vis["XX"] - vis["YY"]) / 2.0
        if {"XY", "YX"} <= have:
            u_val = (vis["XY"] + vis["YX"]) / 2.0
            v_val = (vis["XY"] - vis["YX"]) / 2.0j
        else:
            u_val = v_val = None
    elif {"RR", "LL"} <= have:
        basis = "circular"
        i_val = (vis["RR"] + vis["LL"]) / 2.0
        v_val = (vis["RR"] - vis["LL"]) / 2.0
        if {"RL", "LR"} <= have:
            q_val = (vis["RL"] + vis["LR"]) / 2.0
            u_val = (vis["RL"] - vis["LR"]) / 2.0j
        else:
            q_val = u_val = None
    else:
        return (
            {"I": None, "Q": None, "U": None, "V": None},
            "unknown",
            (f"Cannot form Stokes from correlations {sorted(have)}: need XX+YY or RR+LL"),
        )

    note = None
    if u_val is None:
        note = (
            "Cross-hand correlations absent, so U and V could not be formed. "
            "This is a parallel-hand-only dataset; polarisation recovery cannot "
            "be verified from it."
        )
    return {"I": i_val, "Q": q_val, "U": u_val, "V": v_val}, basis, note


def frac_pol_and_evpa(
    stokes: dict[str, complex | None],
) -> tuple[float | None, float | None]:
    """
    Return (fractional linear polarisation, EVPA in degrees) from Stokes.

    frac_pol = sqrt(Q^2 + U^2) / I
    EVPA     = 0.5 * atan2(U, Q), in degrees, wrapped to [-90, +90)

    EVPA is defined modulo 180 degrees, so the half-angle lands in a 180 degree
    range; the wrap keeps it in [-90, +90) so two values are comparable without
    a spurious 180 degree difference.
    """
    i_val, q_val, u_val = stokes["I"], stokes["Q"], stokes["U"]
    if i_val is None or q_val is None or u_val is None:
        return None, None
    i_r = float(np.real(i_val))
    q_r = float(np.real(q_val))
    u_r = float(np.real(u_val))
    if i_r == 0.0:
        return None, None
    frac = math.hypot(q_r, u_r) / abs(i_r)
    evpa = math.degrees(0.5 * math.atan2(u_r, q_r))
    evpa = ((evpa + 90.0) % 180.0) - 90.0
    return frac, evpa


def evpa_difference_deg(measured: float | None, reference: float | None) -> float | None:
    """
    Signed smallest difference between two EVPAs, in degrees, within +/-90.

    EVPA is modulo 180, so a naive subtraction of 89 and -89 gives 178 degrees
    when the real disagreement is 2 degrees.
    """
    if measured is None or reference is None:
        return None
    diff = (measured - reference + 90.0) % 180.0 - 90.0
    return diff


def _vector_average(
    data: np.ndarray,
    flag: np.ndarray,
    chan_start: int | None,
    chan_end: int | None,
) -> np.ndarray | None:
    """
    Vector-average a [n_corr, n_chan, n_row] block over channels and rows.

    Vector (complex) averaging, not amplitude averaging: averaging |V| would be
    noise-biased upward on a faint source and would destroy the phase information
    that Q, U, and the EVPA depend on entirely.

    Returns a [n_corr] complex array, or None if every sample is flagged.
    """
    sl = slice(chan_start, chan_end)
    d = data[:, sl, :]
    f = flag[:, sl, :]
    good = ~f
    if not good.any():
        return None
    masked = np.where(good, d, 0.0 + 0.0j)
    counts = good.sum(axis=(1, 2))
    with np.errstate(invalid="ignore", divide="ignore"):
        avg = masked.sum(axis=(1, 2)) / counts
    avg[counts == 0] = np.nan
    return avg


def _dterm_stats(caltable: str) -> tuple[dict, list[str], str | None]:
    """
    Median |D| per antenna and per SpW from a D/Df/Dflls caltable.

    Returns (stats, casa_calls, error). `stats` carries per-antenna and per-SpW
    medians plus the overall median, all as plain fractions (not percent).
    """
    casa_calls = [f"tb.open('{caltable}') → getcol(CPARAM, FLAG, ANTENNA1, SPECTRAL_WINDOW_ID)"]
    try:
        with open_table(caltable) as tb:
            cparam = tb.getcol("CPARAM")  # [n_pol, n_chan, n_row]
            cflag = tb.getcol("FLAG")
            ant1 = np.asarray(tb.getcol("ANTENNA1"))
            spw = np.asarray(tb.getcol("SPECTRAL_WINDOW_ID"))
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        return {}, casa_calls, f"Could not read caltable: {exc}"

    amp = np.abs(np.asarray(cparam))
    good = ~np.asarray(cflag)
    if not good.any():
        return {}, casa_calls, "Every solution in the caltable is flagged"

    per_row = np.full(amp.shape[2], np.nan)
    for r in range(amp.shape[2]):
        vals = amp[:, :, r][good[:, :, r]]
        if vals.size:
            per_row[r] = float(np.median(vals))

    def _median_by(keys: np.ndarray) -> list[dict]:
        out = []
        for k in sorted(set(int(x) for x in keys)):
            sel = per_row[keys == k]
            sel = sel[~np.isnan(sel)]
            if sel.size:
                out.append({"id": k, "median_abs_d": round(float(np.median(sel)), 5)})
        return out

    finite = per_row[~np.isnan(per_row)]
    stats = {
        "median_abs_d": round(float(np.median(finite)), 5) if finite.size else None,
        "max_abs_d": round(float(np.max(finite)), 5) if finite.size else None,
        "per_antenna": _median_by(ant1),
        "per_spw": _median_by(spw),
        "n_solutions_used": int(finite.size),
    }
    return stats, casa_calls, None


def _corr_labels(ms_str: str) -> tuple[list[str], list[str]]:
    """Read CORR_TYPE for the first polarization setup and return labels."""
    casa_calls = ["tb.open(POLARIZATION) → getcell(CORR_TYPE, 0)"]
    with open_table(ms_str + "/POLARIZATION") as tb:
        codes = [int(x) for x in tb.getcell("CORR_TYPE", 0)]
    return corr_codes_to_labels(codes), casa_calls


def _field_id_for_name(ms_str: str, name: str) -> tuple[int | None, list[str]]:
    casa_calls = ["tb.open(FIELD) → getcol(NAME)"]
    with open_table(ms_str + "/FIELD") as tb:
        names = [str(n) for n in tb.getcol("NAME")]
    for i, n in enumerate(names):
        if n == name:
            return i, casa_calls
    return None, casa_calls


def _spw_centre_ghz(ms_str: str, spw_id: int) -> float:
    with open_table(ms_str + "/SPECTRAL_WINDOW") as tb:
        chan = tb.getcell("CHAN_FREQ", spw_id)
    return float(np.median(chan)) / 1e9


def run(
    ms_path: str,
    field_name: str,
    dterm_caltable: str | None = None,
    spw_ids: list[int] | None = None,
    chan_start: int | None = None,
    chan_end: int | None = None,
) -> dict:
    """
    Measure how well a polarisation calibration recovered known values.

    Run AFTER applycal with parang=True. Requires CORRECTED_DATA, and MODEL_DATA
    for the applied-reference comparison (usescratch=True on setjy).

    Args:
        ms_path:        Path to the Measurement Set.
        field_name:     Polarisation calibrator field to verify.
        dterm_caltable: Optional D/Df/Dflls caltable for leakage amplitudes.
        spw_ids:        SpWs to measure; None means every SpW present for the field.
        chan_start:     Channel range start for the vector average (Python slice).
        chan_end:       Channel range end, exclusive. None means open-ended.

    Returns:
        Standard envelope with per_spw recovery measurements, each carrying the
        measured Stokes parameters, both reference values, and the residuals with
        their inputs; plus dterm stats and the reference constants.
    """
    p = validate_ms_path(ms_path)
    ms_str = str(p)
    casa_calls: list[str] = []
    warnings: list[str] = []

    corr_labels, cc = _corr_labels(ms_str)
    casa_calls.extend(cc)

    fid, cc = _field_id_for_name(ms_str, field_name)
    casa_calls.extend(cc)
    if fid is None:
        return response_envelope(
            tool_name=TOOL_NAME,
            ms_path=ms_path,
            data={
                "field_name": field(None, "UNAVAILABLE", note=f"Field '{field_name}' not in FIELD"),
            },
            warnings=[f"Field '{field_name}' not found in the MS."],
            casa_calls=casa_calls,
        )

    # Independent catalogue entry, if this source is a known pol calibrator.
    cat_entry = lookup_pol(field_name)
    if cat_entry is None:
        warnings.append(
            f"'{field_name}' is not in the bundled pol calibrator catalogue, so the "
            "independent catalogue cross-check is unavailable. MODEL-relative "
            "comparison is still reported."
        )

    per_spw: list[dict] = []
    with open_table(ms_str) as tb:
        casa_calls.append("tb.open(MAIN) → query(FIELD_ID, DATA_DESC_ID) → CORRECTED/MODEL/FLAG")
        colnames = set(tb.colnames())
        if "CORRECTED_DATA" not in colnames:
            warnings.append("CORRECTED_DATA absent: run applycal(parang=True) first.")
        has_model = "MODEL_DATA" in colnames
        if not has_model:
            warnings.append(
                "MODEL_DATA absent, so the applied-reference comparison is unavailable. "
                "Re-run setjy / setjy_polcal with usescratch=True."
            )

        sub_all = tb.query(f"FIELD_ID=={fid}")
        try:
            ddids = sorted({int(x) for x in sub_all.getcol("DATA_DESC_ID")})
        finally:
            sub_all.close()

    with open_table(ms_str + "/DATA_DESCRIPTION") as tb:
        ddid_to_spw = {i: int(s) for i, s in enumerate(tb.getcol("SPECTRAL_WINDOW_ID"))}
    casa_calls.append("tb.open(DATA_DESCRIPTION) → SPECTRAL_WINDOW_ID")

    wanted = [d for d in ddids if spw_ids is None or ddid_to_spw.get(d) in spw_ids]

    for ddid in wanted:
        spw_id = ddid_to_spw.get(ddid, -1)
        entry: dict = {"spw_id": spw_id}
        with open_table(ms_str) as tb:
            sub = tb.query(f"FIELD_ID=={fid} && DATA_DESC_ID=={ddid}")
            try:
                if sub.nrows() == 0:
                    continue
                flag = np.asarray(sub.getcol("FLAG"))
                corrected = (
                    np.asarray(sub.getcol("CORRECTED_DATA"))
                    if "CORRECTED_DATA" in colnames
                    else None
                )
                model = np.asarray(sub.getcol("MODEL_DATA")) if has_model else None
            finally:
                sub.close()

        meas_stokes: dict[str, complex | None] = {"I": None, "Q": None, "U": None, "V": None}
        basis = "unknown"
        if corrected is not None:
            avg = _vector_average(corrected, flag, chan_start, chan_end)
            if avg is None:
                entry["measured"] = field(
                    None, "UNAVAILABLE", note="All samples flagged in this SpW"
                )
                per_spw.append(entry)
                continue
            vis = {lab: complex(v) for lab, v in zip(corr_labels, avg, strict=False)}
            meas_stokes, basis, note = stokes_from_corr(vis)
            if note:
                warnings.append(f"SpW {spw_id}: {note}")

        meas_frac, meas_evpa = frac_pol_and_evpa(meas_stokes)

        model_stokes: dict[str, complex | None] = {"I": None, "Q": None, "U": None, "V": None}
        if model is not None:
            m_avg = _vector_average(model, flag, chan_start, chan_end)
            if m_avg is not None:
                m_vis = {lab: complex(v) for lab, v in zip(corr_labels, m_avg, strict=False)}
                model_stokes, _, _ = stokes_from_corr(m_vis)
        model_frac, model_evpa = frac_pol_and_evpa(model_stokes)

        # Independent catalogue values at this SpW's centre frequency.
        cat_frac = cat_evpa = None
        if cat_entry is not None:
            try:
                props = pol_properties_at_freq(
                    cat_entry, _spw_centre_ghz(ms_str, spw_id), epoch=POL_DATA_EPOCH
                )
            except Exception:  # noqa: BLE001
                props = None
            if props is not None:
                cat_frac = props.frac_pol_pct / 100.0 if props.frac_pol_pct is not None else None
                cat_evpa = props.pol_angle_deg

        def _r(x: float | None, nd: int = 5) -> float | None:
            return round(x, nd) if x is not None else None

        meas_i = float(np.real(meas_stokes["I"])) if meas_stokes["I"] is not None else None
        model_i = float(np.real(model_stokes["I"])) if model_stokes["I"] is not None else None
        meas_v = float(np.real(meas_stokes["V"])) if meas_stokes["V"] is not None else None

        # Derived residuals. Each ships with both of its inputs, above.
        i_ratio = meas_i / model_i if (meas_i is not None and model_i not in (None, 0.0)) else None
        frac_v = abs(meas_v) / abs(meas_i) if (meas_v is not None and meas_i) else None

        entry.update(
            {
                "correlation_basis": basis,
                "measured_stokes_jy": {
                    "I": _r(meas_i),
                    "Q": _r(
                        float(np.real(meas_stokes["Q"])) if meas_stokes["Q"] is not None else None
                    ),
                    "U": _r(
                        float(np.real(meas_stokes["U"])) if meas_stokes["U"] is not None else None
                    ),
                    "V": _r(meas_v),
                },
                "measured_frac_pol": _r(meas_frac),
                "measured_evpa_deg": _r(meas_evpa, 3),
                # Reference 1: what setjy actually applied.
                "model_stokes_i_jy": _r(model_i),
                "model_frac_pol": _r(model_frac),
                "model_evpa_deg": _r(model_evpa, 3),
                "stokes_i_ratio_measured_over_model": _r(i_ratio, 4),
                "frac_pol_difference_vs_model": _r(
                    meas_frac - model_frac
                    if (meas_frac is not None and model_frac is not None)
                    else None
                ),
                "evpa_difference_deg_vs_model": _r(evpa_difference_deg(meas_evpa, model_evpa), 3),
                # Reference 2: independent catalogue.
                "catalogue_frac_pol": _r(cat_frac),
                "catalogue_evpa_deg": _r(cat_evpa, 3),
                "frac_pol_difference_vs_catalogue": _r(
                    meas_frac - cat_frac
                    if (meas_frac is not None and cat_frac is not None)
                    else None
                ),
                "evpa_difference_deg_vs_catalogue": _r(evpa_difference_deg(meas_evpa, cat_evpa), 3),
                # Direct observable for uncorrected leakage on a source with no
                # intrinsic circular polarisation, which pol calibrators are.
                "residual_frac_v": _r(frac_v),
            }
        )
        per_spw.append(entry)

    dterms: dict = {}
    if dterm_caltable:
        dterms, cc, err = _dterm_stats(dterm_caltable)
        casa_calls.extend(cc)
        if err:
            warnings.append(f"D-term caltable: {err}")

    data = {
        "field_name": field(field_name),
        "field_id": field(fid),
        "correlations": field(corr_labels),
        "channel_range": field([chan_start, chan_end]),
        "averaging": (
            "vector-averaged (complex) over the channel range and rows before "
            "forming Stokes; amplitude averaging would be noise-biased and would "
            "destroy the phase that Q, U and EVPA depend on"
        ),
        "per_spw": per_spw,
        "dterms": dterms if dterms else field(None, "UNAVAILABLE", note="No dterm_caltable given"),
        # Reference values as labelled constants. Not applied here.
        "dterm_typical_max_frac": DTERM_TYPICAL_MAX_FRAC,
        "dterm_suspect_frac": DTERM_SUSPECT_FRAC,
        "dterm_reference_source": DTERM_REFERENCE_SOURCE,
        "pol_cal_data_epoch": POL_DATA_EPOCH,
    }

    return response_envelope(
        tool_name=TOOL_NAME,
        ms_path=ms_path,
        data=data,
        warnings=warnings,
        casa_calls=casa_calls,
    )
