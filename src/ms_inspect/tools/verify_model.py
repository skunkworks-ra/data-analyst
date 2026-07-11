"""
tools/verify_model.py — ms_verify_model

Read-only sanity probe of the MODEL_DATA column after a setjy / setjy_polcal
step. Answers one question per (field): "does MODEL_DATA look like a real flux
model that was actually written, or like an untouched / clobbered one?"

It measures — it does not decide. Three smell tests per field, each surfaced as
a CompletionFlag on a numeric field; the skill reasons about what to do:

  1. Default-pinned — an unwritten model sits at CASA's default MODEL_DATA=1+0j:
     parallel-hand amplitude ~1.0 AND near-zero phase scatter. This is the exact
     signature of the flux-scale trap (a field left at 1 Jy makes fluxscale come
     out order-of-magnitude low). Gated on BOTH amp≈1 and flat phase, because a
     genuine calibrator can legitimately be ~1 Jy.
  2. Plausibility band — parallel-hand amplitude outside a physical Jy range is
     SUSPECT. A weak backstop for gross corruption only.
  3. Polarization presence — for fields the caller marks as pol-angle calibrators
     (polcal_fields), the cross-hand correlations (RL/LR, XY/YX) must be non-zero
     relative to the parallel hands. A Stokes-I-only model (or a polarized model
     clobbered by a later plain setjy) has zero cross-hands — the G55 failure.

Requires the physical MODEL_DATA column (usescratch=True). A virtual model
(usescratch=False) writes no MODEL_DATA and cannot be probed this way.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from ms_inspect.util.casa_context import open_table, validate_ms_path
from ms_inspect.util.formatting import field as fmt_field
from ms_inspect.util.formatting import normalize_field_sel, response_envelope

TOOL_NAME = "ms_verify_model"

_DEFAULT_MAX_ROWS = 200_000
# CASA Stokes codes: parallel-hand RR/LL/XX/YY and cross-hand RL/LR/XY/YX.
_PARALLEL_CODES = {5, 8, 9, 12}
_CROSSHAND_CODES = {6, 7, 10, 11}


def _indices(corr_codes: list[int], wanted: set[int]) -> list[int]:
    return [i for i, c in enumerate(corr_codes) if int(c) in wanted]


def _corr_codes(ms_str: str) -> list[int]:
    with open_table(str(Path(ms_str) / "POLARIZATION")) as tb:
        return [int(x) for x in tb.getcell("CORR_TYPE", 0)]


def _field_id_map(ms_str: str) -> dict[int, str]:
    with open_table(str(Path(ms_str) / "FIELD")) as tb:
        names = list(tb.getcol("NAME"))
    return {i: str(n) for i, n in enumerate(names)}


def _model_metrics(
    data: np.ndarray,
    flag: np.ndarray,
    par_idx: list[int],
    cross_idx: list[int],
) -> dict:
    """Median parallel/cross-hand MODEL amplitude and parallel phase RMS.

    MODEL is noise-free, so no per-channel vector averaging is needed — the
    metrics are taken over all unflagged samples on the selected correlations.
    """
    par = data[par_idx, :, :]
    par_good = ~flag[par_idx, :, :]
    pvis = par[par_good]
    pvis = pvis[np.abs(pvis) > 0]
    n_par = int(pvis.size)
    if n_par == 0:
        return {"n_par": 0, "par_amp": None, "par_phase_rms": None, "cross_amp": None}

    par_amp = float(np.median(np.abs(pvis)))
    phase = np.angle(pvis) * (180.0 / math.pi)
    par_phase_rms = float(np.sqrt(np.mean(phase**2)))

    cross_amp: float | None = None
    if cross_idx:
        cr = data[cross_idx, :, :]
        cr_good = ~flag[cross_idx, :, :]
        cvis = cr[cr_good]
        cvis = cvis[np.abs(cvis) > 0]
        # Median over ALL cross-hand samples (including exact zeros would bias
        # low); a Stokes-I model has no non-zero cross-hand samples at all, so
        # an empty cvis is itself the "no polarization" signal → 0.0.
        cross_amp = float(np.median(np.abs(cvis))) if cvis.size else 0.0

    return {
        "n_par": n_par,
        "par_amp": round(par_amp, 6),
        "par_phase_rms": round(par_phase_rms, 3),
        "cross_amp": None if cross_amp is None else round(cross_amp, 6),
    }


def classify(
    par_amp: float,
    par_phase_rms: float,
    cross_amp: float | None,
    is_polcal: bool,
    default_amp_tol: float,
    default_phase_rms_deg: float,
    plausible_min_jy: float,
    plausible_max_jy: float,
    crosshand_ratio_thresh: float,
) -> tuple[str, float | None, list[str]]:
    """Pure classification of one field's MODEL metrics.

    Returns (status_flag, crosshand_ratio, reasons). status_flag is 'SUSPECT'
    if any smell test fired, else 'COMPLETE'. CASA-free for unit testing.
    """
    reasons: list[str] = []

    if abs(par_amp - 1.0) <= default_amp_tol and par_phase_rms <= default_phase_rms_deg:
        reasons.append("pinned at MODEL=1 Jy default (amp≈1, flat phase) — model likely unwritten")
    if par_amp < plausible_min_jy or par_amp > plausible_max_jy:
        reasons.append(
            f"par_amp {par_amp} Jy outside plausible band [{plausible_min_jy}, {plausible_max_jy}]"
        )

    ratio: float | None = None
    if cross_amp is not None and par_amp > 0:
        ratio = round(cross_amp / par_amp, 6)
    if is_polcal:
        if ratio is None:
            reasons.append("pol-cal field but MS has no cross-hand correlations")
        elif ratio < crosshand_ratio_thresh:
            reasons.append(
                f"pol-cal field but cross/parallel ratio {ratio} < "
                f"{crosshand_ratio_thresh} — no polarization in MODEL "
                "(Stokes-I only, or clobbered by a later plain setjy)"
            )

    return ("SUSPECT" if reasons else "COMPLETE", ratio, reasons)


def run(
    ms_path: str,
    field: str = "",
    polcal_fields: str = "",
    default_amp_tol: float = 0.05,
    default_phase_rms_deg: float = 1.0,
    plausible_min_jy: float = 0.1,
    plausible_max_jy: float = 100.0,
    crosshand_ratio_thresh: float = 0.001,
    max_rows: int = _DEFAULT_MAX_ROWS,
) -> dict:
    """
    Probe MODEL_DATA for untouched-default, out-of-band, and missing-polarization
    models after a setjy / setjy_polcal step.

    Args:
        ms_path:                Path to the MS (MODEL_DATA / usescratch=True).
        field:                  CASA field-name selection (comma-separated) or ''
                                for all fields.
        polcal_fields:          Field names expected to carry a POLARIZED model
                                (the pol-angle calibrators set via
                                ms_setjy_polcal). Only these get the pol-presence
                                check; a Stokes-I flux cal or phase cal legitimately
                                has zero cross-hands, so it is never flagged for that.
        default_amp_tol:        |par_amp − 1.0| ≤ this AND flat phase ⇒ pinned at
                                the MODEL=1 Jy default (default 0.05).
        default_phase_rms_deg:  Phase RMS ≤ this counts as "flat" for the default
                                check (default 1.0°).
        plausible_min_jy/max_jy: Physical amplitude band; outside ⇒ SUSPECT
                                (defaults 0.1 / 100 Jy). Weak backstop.
        crosshand_ratio_thresh: cross_amp / par_amp ≥ this ⇒ polarization present
                                (default 0.001).
        max_rows:               Per-field row cap; rows sampled uniformly above it.

    Returns:
        Standard envelope with a per_field array. Each entry carries par_amp,
        par_phase_rms, cross_amp, crosshand_ratio, and a `status` field flagged
        COMPLETE / SUSPECT / UNAVAILABLE with the reason in its note.
    """
    field = normalize_field_sel(field)
    polcal_fields = normalize_field_sel(polcal_fields)
    p = validate_ms_path(ms_path)
    ms_str = str(p)
    casa_calls: list[str] = []
    warnings: list[str] = []

    name_map = _field_id_map(ms_str)
    casa_calls.append("tb.open(FIELD) → NAME")
    wanted = {n.strip() for n in field.split(",") if n.strip()}
    target_ids = [fid for fid, nm in name_map.items() if (not wanted or nm in wanted)]
    polcal_set = {n.strip() for n in polcal_fields.split(",") if n.strip()}
    if not target_ids:
        warnings.append(f"No fields matched selection '{field}'.")
        return response_envelope(
            tool_name=TOOL_NAME,
            ms_path=ms_path,
            data={"per_field": [], "polcal_fields": sorted(polcal_set)},
            warnings=warnings,
            casa_calls=casa_calls,
        )

    corr_codes = _corr_codes(ms_str)
    par_idx = _indices(corr_codes, _PARALLEL_CODES)
    cross_idx = _indices(corr_codes, _CROSSHAND_CODES)
    if not par_idx:
        par_idx = list(range(len(corr_codes)))
    casa_calls.append(f"tb.open(POLARIZATION) → CORR_TYPE, par={par_idx}, cross={cross_idx}")

    per_field: list[dict] = []
    with open_table(ms_str) as tb:
        if "MODEL_DATA" not in set(tb.colnames()):
            from ms_inspect.exceptions import ComputationError

            raise ComputationError(
                "MODEL_DATA column not present. Models may have been written "
                "virtually (usescratch=False), which writes no MODEL_DATA column; "
                "re-run setjy with usescratch=True to materialize it.",
                ms_path=ms_path,
            )
        for fid in target_ids:
            sub = tb.query(f"FIELD_ID == {fid}")
            try:
                n_rows = int(sub.nrows())
                if n_rows == 0:
                    continue
                step = max(1, n_rows // max_rows)
                data = sub.getcol("MODEL_DATA")
                flag = sub.getcol("FLAG")
            finally:
                sub.close()
            if step > 1:
                data = data[:, :, ::step]
                flag = flag[:, :, ::step]
            casa_calls.append(f"tb.query(FIELD_ID=={fid}) → MODEL_DATA, FLAG")

            m = _model_metrics(data, flag, par_idx, cross_idx)
            fname = name_map[fid]
            is_polcal = fname in polcal_set

            if m["n_par"] == 0:
                per_field.append(
                    {
                        "field_id": fid,
                        "field_name": fname,
                        "is_polcal_field": is_polcal,
                        "par_amp": fmt_field(None, flag="UNAVAILABLE", note="all data flagged"),
                        "par_phase_rms_deg": fmt_field(None, flag="UNAVAILABLE"),
                        "cross_amp": fmt_field(None, flag="UNAVAILABLE"),
                        "crosshand_ratio": fmt_field(None, flag="UNAVAILABLE"),
                        "status": fmt_field(
                            "UNAVAILABLE", flag="UNAVAILABLE", note="no unflagged MODEL samples"
                        ),
                    }
                )
                continue

            par_amp = m["par_amp"]
            status_flag, ratio, reasons = classify(
                par_amp,
                m["par_phase_rms"],
                m["cross_amp"],
                is_polcal,
                default_amp_tol,
                default_phase_rms_deg,
                plausible_min_jy,
                plausible_max_jy,
                crosshand_ratio_thresh,
            )
            status_note = "; ".join(reasons) if reasons else "model written and sensible"
            per_field.append(
                {
                    "field_id": fid,
                    "field_name": fname,
                    "is_polcal_field": is_polcal,
                    "par_amp": fmt_field(par_amp),
                    "par_phase_rms_deg": fmt_field(m["par_phase_rms"]),
                    "cross_amp": fmt_field(
                        m["cross_amp"],
                        flag="UNAVAILABLE" if m["cross_amp"] is None else "COMPLETE",
                        note="no cross-hand correlations" if m["cross_amp"] is None else None,
                    ),
                    "crosshand_ratio": fmt_field(
                        ratio, flag="UNAVAILABLE" if ratio is None else "COMPLETE"
                    ),
                    "status": fmt_field(status_flag, flag=status_flag, note=status_note),
                }
            )

    return response_envelope(
        tool_name=TOOL_NAME,
        ms_path=ms_path,
        data={
            "per_field": per_field,
            "polcal_fields": sorted(polcal_set),
            "thresholds": {
                "default_amp_tol": default_amp_tol,
                "default_phase_rms_deg": default_phase_rms_deg,
                "plausible_min_jy": plausible_min_jy,
                "plausible_max_jy": plausible_max_jy,
                "crosshand_ratio_thresh": crosshand_ratio_thresh,
            },
        },
        warnings=warnings,
        casa_calls=casa_calls,
    )
