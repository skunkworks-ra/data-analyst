"""
tools/fields.py — ms_field_list

Layer 1, Tool 2: What are the observed fields, their sky positions,
and their calibration roles?

CASA access: msmd.fieldnames(), msmd.phasecenter(), msmd.intentsforfield()
Falls back to calibrator catalogue matching when intents are absent.
"""

from __future__ import annotations

import math

import numpy as np

from ms_inspect.util.calibrators import infer_intents_from_role
from ms_inspect.util.calibrators import lookup as cal_lookup
from ms_inspect.util.casa_context import open_msmd, validate_ms_path
from ms_inspect.util.conversions import rad_to_deg, rad_to_dms, rad_to_hms
from ms_inspect.util.formatting import field, response_envelope
from ms_inspect.util.vla_calibrators import cone_search as vla_cone_search

TOOL_NAME = "ms_field_list"

# Threshold: if fewer than this fraction of fields have non-empty intents,
# switch to heuristic inference mode.
_INTENT_COVERAGE_THRESHOLD = 0.50

# Coordinates suspiciously close to (0, 0) — almost certainly a broken export.
# True (0, 0) on sky is on the meridian at the equator, essentially never a target.
_COORD_SUSPECT_THRESHOLD_DEG = 1.0 / 60.0  # 1 arcminute


def run(ms_path: str) -> dict:
    """
    Return the list of all observed fields with positions, intents, and
    calibrator role identification.
    """
    p = validate_ms_path(ms_path)
    casa_calls: list[str] = []
    warnings: list[str] = []

    with open_msmd(str(p)) as msmd:
        casa_calls.append("msmd.open()")

        # Field names
        field_names: list[str] = list(msmd.fieldnames())
        casa_calls.append("msmd.fieldnames()")
        n_fields = len(field_names)

        if n_fields == 0:
            warnings.append("No fields found in MS.")
            return response_envelope(
                tool_name=TOOL_NAME,
                ms_path=ms_path,
                data={"fields": [], "n_fields": 0},
                warnings=warnings,
                casa_calls=casa_calls,
            )

        # Phase centres — returns a dict with 'm0' (RA) and 'm1' (Dec) in radians
        phase_centers: list[dict] = []
        for fid in range(n_fields):
            try:
                pc = msmd.phasecenter(fid)
                phase_centers.append(pc)
            except Exception:
                phase_centers.append({})
        casa_calls.append("msmd.phasecenter(field_id) for each field")

        # Intents per field
        raw_intents: list[set[str]] = []
        for fid in range(n_fields):
            try:
                intents = set(msmd.intentsforfield(fid))
            except Exception:
                intents = set()
            raw_intents.append(intents)
        casa_calls.append("msmd.intentsforfield(field_id) for each field")

        # Source IDs (for mosaic grouping)
        try:
            source_ids: list[int] = list(msmd.sourceidforfield(list(range(n_fields))))
        except Exception:
            source_ids = list(range(n_fields))

    # ------------------------------------------------------------------
    # Determine if we're in intent-inference mode
    # ------------------------------------------------------------------
    n_with_intents = sum(1 for s in raw_intents if s)
    intent_fraction = n_with_intents / n_fields if n_fields > 0 else 0.0
    heuristic_mode = intent_fraction < _INTENT_COVERAGE_THRESHOLD

    if heuristic_mode:
        warnings.append(
            f"Only {n_with_intents}/{n_fields} fields have scan intent metadata "
            f"({intent_fraction * 100:.0f}% coverage, threshold {_INTENT_COVERAGE_THRESHOLD * 100:.0f}%). "
            "Switching to heuristic intent inference from field names via calibrator catalogue. "
            "Inferred intents are tagged INFERRED — verify before use."
        )

    # ------------------------------------------------------------------
    # Build field records
    # ------------------------------------------------------------------
    fields_out: list[dict] = []

    for fid in range(n_fields):
        name = field_names[fid]
        pc = phase_centers[fid]
        intents = raw_intents[fid]

        # --- Coordinates ---
        ra_rad, dec_rad, coord_flag, coord_note = _extract_coords(pc, name, fid)

        ra_deg = rad_to_deg(ra_rad) if ra_rad is not None else None
        dec_deg = rad_to_deg(dec_rad) if dec_rad is not None else None
        ra_hms = rad_to_hms(ra_rad) if ra_rad is not None else None
        dec_dms = rad_to_dms(dec_rad) if dec_rad is not None else None

        # --- Calibrator catalogue match ---
        cal_entry = cal_lookup(name)
        if cal_entry:
            cal_match = field(
                cal_entry.canonical_name,
                flag="COMPLETE",
                note=f"Matched '{name}' to catalogue entry '{cal_entry.canonical_name}'",
            )
            cal_role = field(cal_entry.role, flag="COMPLETE")
            cal_standard = field(cal_entry.flux_standard, flag="COMPLETE")
            cal_resolved = field(cal_entry.resolved, flag="COMPLETE")
            if cal_entry.notes:
                warnings.append(f"[{name}] {cal_entry.notes}")
        else:
            cal_match = field(None, flag="UNAVAILABLE", note="Not in bundled calibrator catalogue")
            cal_role = field(None, flag="UNAVAILABLE")
            cal_standard = field(None, flag="UNAVAILABLE")
            cal_resolved = field(None, flag="UNAVAILABLE")

        # --- VLA calibrator positional cross-match ---
        vla_cal_match_field = _vla_positional_match(ra_deg, dec_deg)

        # --- Intents ---
        if intents:
            intent_field = field(sorted(intents), flag="COMPLETE")
        elif heuristic_mode and cal_entry:
            inferred = infer_intents_from_role(cal_entry.role)
            intent_field = field(
                inferred,
                flag="INFERRED",
                note=f"Inferred from calibrator catalogue role: {cal_entry.role}",
            )
        elif heuristic_mode:
            intent_field = field(
                [], flag="UNAVAILABLE", note="No intents in MS and no catalogue match for inference"
            )
        else:
            intent_field = field([], flag="UNAVAILABLE", note="No intents recorded for this field")

        record = {
            "field_id": fid,
            "name": name,
            "source_id": source_ids[fid] if fid < len(source_ids) else fid,
            "ra_j2000_deg": field(
                round(ra_deg, 6) if ra_deg is not None else None, flag=coord_flag, note=coord_note
            ),
            "dec_j2000_deg": field(
                round(dec_deg, 6) if dec_deg is not None else None, flag=coord_flag, note=coord_note
            ),
            "ra_hms": ra_hms,
            "dec_dms": dec_dms,
            "intents": intent_field,
            "calibrator_match": cal_match,
            "calibrator_role": cal_role,
            "flux_standard": cal_standard,
            "resolved_source": cal_resolved,
            "vla_cal_match": vla_cal_match_field,
        }
        fields_out.append(record)

    # ------------------------------------------------------------------
    # nearest_phase_cal enrichment for target fields
    # ------------------------------------------------------------------
    # Classify fields into phase_cals and targets
    phase_cal_records = []
    for rec in fields_out:
        role_field = rec.get("calibrator_role", {})
        role_val = role_field.get("value") if isinstance(role_field, dict) else role_field
        intents_field = rec.get("intents", {})
        intents_val = (
            intents_field.get("value") if isinstance(intents_field, dict) else intents_field
        )
        intents_val = intents_val or []
        is_phase = (
            (isinstance(role_val, list) and "phase" in role_val)
            or (isinstance(role_val, str) and "phase" in role_val)
            or any("PHASE" in str(i).upper() for i in intents_val)
        )
        if is_phase:
            ra_f = rec.get("ra_j2000_deg", {})
            dec_f = rec.get("dec_j2000_deg", {})
            ra = ra_f.get("value") if isinstance(ra_f, dict) else ra_f
            dec = dec_f.get("value") if isinstance(dec_f, dict) else dec_f
            phase_cal_records.append({"name": rec["name"], "ra": ra, "dec": dec})

    for rec in fields_out:
        role_field = rec.get("calibrator_role", {})
        role_val = role_field.get("value") if isinstance(role_field, dict) else role_field
        intents_field = rec.get("intents", {})
        intents_val = (
            intents_field.get("value") if isinstance(intents_field, dict) else intents_field
        )
        intents_val = intents_val or []
        is_target = (
            role_val is None
            or (isinstance(role_val, list) and not role_val)
            or any("TARGET" in str(i).upper() for i in intents_val)
        )
        if not is_target:
            continue
        ra_f = rec.get("ra_j2000_deg", {})
        dec_f = rec.get("dec_j2000_deg", {})
        tgt_ra = ra_f.get("value") if isinstance(ra_f, dict) else ra_f
        tgt_dec = dec_f.get("value") if isinstance(dec_f, dict) else dec_f
        if not phase_cal_records:
            rec["nearest_phase_cal"] = None
            rec["separation_deg"] = None
            if "no phase calibrator found" not in " ".join(warnings):
                warnings.append("no phase calibrator found — cannot compute separation")
        elif tgt_ra is None or tgt_dec is None:
            rec["nearest_phase_cal"] = None
            rec["separation_deg"] = None
        else:
            best_name = None
            best_sep = float("inf")
            for pc in phase_cal_records:
                if pc["ra"] is None or pc["dec"] is None:
                    continue
                sep = _angular_sep_deg(tgt_ra, tgt_dec, pc["ra"], pc["dec"])
                if sep < best_sep:
                    best_sep = sep
                    best_name = pc["name"]
            rec["nearest_phase_cal"] = best_name
            rec["separation_deg"] = round(best_sep, 2) if best_name is not None else None

    # ------------------------------------------------------------------
    # Mosaic detection: multiple fields same source_id → group them
    # ------------------------------------------------------------------
    mosaic_groups: dict[int, list[int]] = {}
    for fid, sid in enumerate(source_ids[:n_fields]):
        mosaic_groups.setdefault(sid, []).append(fid)
    mosaic_notes = [
        f"Source ID {sid}: {len(fids)} pointings (mosaic) — fields {fids}"
        for sid, fids in mosaic_groups.items()
        if len(fids) > 1
    ]
    if mosaic_notes:
        warnings.extend(mosaic_notes)

    data = {
        "n_fields": n_fields,
        "heuristic_intents": heuristic_mode,
        "fields": fields_out,
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


def _extract_coords(
    phasecenter: dict,
    field_name: str,
    field_id: int,
) -> tuple[float | None, float | None, str, str | None]:
    """
    Extract RA/Dec in radians from a CASA phasecenter dict.

    Returns (ra_rad, dec_rad, completeness_flag, note).
    """
    if not phasecenter:
        return None, None, "UNAVAILABLE", f"phasecenter() returned empty for field {field_id}"

    try:
        # CASA phasecenter returns a direction measure dict:
        # {'type': 'direction', 'refer': 'J2000',
        #  'm0': {'unit': 'rad', 'value': <RA>},
        #  'm1': {'unit': 'rad', 'value': <Dec>}}
        ra_rad = float(phasecenter["m0"]["value"])
        dec_rad = float(phasecenter["m1"]["value"])
    except (KeyError, TypeError, ValueError) as e:
        return None, None, "UNAVAILABLE", f"Could not parse phasecenter dict: {e}"

    # Normalise RA to [0, 2π)
    ra_rad = ra_rad % (2 * math.pi)

    # Suspect coordinate check: (0, 0) to within 1 arcminute
    ra_deg = math.degrees(ra_rad)
    dec_deg = math.degrees(dec_rad)
    if abs(ra_deg) < _COORD_SUSPECT_THRESHOLD_DEG and abs(dec_deg) < _COORD_SUSPECT_THRESHOLD_DEG:
        return (
            ra_rad,
            dec_rad,
            "SUSPECT",
            f"Coordinates ({ra_deg:.4f}°, {dec_deg:.4f}°) are within 1 arcmin of (0,0) J2000. "
            "This is almost certainly a broken UVFITS export. "
            "Elevation, parallactic angle, and phase-cal separation will be UNAVAILABLE for this field.",
        )

    return ra_rad, dec_rad, "COMPLETE", None


def _angular_sep_deg(ra1_deg: float, dec1_deg: float, ra2_deg: float, dec2_deg: float) -> float:
    """Haversine angular separation on the sphere in degrees."""
    r1 = np.radians(ra1_deg)
    r2 = np.radians(ra2_deg)
    d1 = np.radians(dec1_deg)
    d2 = np.radians(dec2_deg)
    c = np.sin(d1) * np.sin(d2) + np.cos(d1) * np.cos(d2) * np.cos(r1 - r2)
    return float(np.degrees(np.arccos(np.clip(c, -1.0, 1.0))))


def _vla_positional_match(
    ra_deg: float | None,
    dec_deg: float | None,
) -> dict:
    """
    Attempt a positional cross-match against the VLA calibrator database.

    Returns a formatted field() dict with the match result.
    """
    if ra_deg is None or dec_deg is None:
        return field(None, flag="UNAVAILABLE", note="No coordinates for VLA positional match")

    try:
        result = vla_cone_search(ra_deg, dec_deg, radius_arcsec=5.0)
    except Exception as e:
        return field(
            None,
            flag="UNAVAILABLE",
            note=f"VLA calibrator positional search failed: {e}",
        )

    if result is None:
        return field(None, flag="UNAVAILABLE", note="No VLA calibrator within 5 arcsec")

    # Declination guard case — result has a note but empty name
    if result.note and not result.name:
        return field(None, flag="UNAVAILABLE", note=result.note)

    match_data = {
        "name": result.name,
        "alt_name": result.alt_name,
        "separation_arcsec": result.separation_arcsec,
        "position_code": result.position_code,
        "bands": {
            k: {
                "qual_A": v.qual_A,
                "qual_B": v.qual_B,
                "qual_C": v.qual_C,
                "qual_D": v.qual_D,
                "flux_jy": v.flux_jy,
            }
            for k, v in result.bands.items()
        },
    }

    flag_val = "COMPLETE" if result.separation_arcsec < 1.0 else "INFERRED"
    note = f"VLA callist match: {result.name}"
    if result.alt_name:
        note += f" ({result.alt_name})"
    note += f" at {result.separation_arcsec:.3f} arcsec"

    return field(match_data, flag=flag_val, note=note)
