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

from ms_inspect.util.calibrators import (
    CalibratorEntry,
    infer_intents_from_role,
    resolve_flux_standard,
    role_from_intents,
    roles_disagree,
)
from ms_inspect.util.calibrators import lookup as cal_lookup
from ms_inspect.util.casa_context import open_msmd, validate_ms_path
from ms_inspect.util.conversions import rad_to_deg, rad_to_dms, rad_to_hms
from ms_inspect.util.formatting import field, response_envelope
from ms_inspect.util.frequencies import field_frequencies
from ms_inspect.util.vla_calibrators import cone_search as vla_cone_search

TOOL_NAME = "ms_field_list"

# Coverage below this raises a warning, and enables the whole-MS scan-pattern
# inference. It does NOT decide any field's role — that is per field, from that
# field's own intents. The response reports the raw fraction and its inputs, so
# the skill can apply its own threshold.
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

        # Scan sequence for pattern-based role inference (cheap; used only in heuristic mode)
        scan_sequence: list[tuple[int, int, float]] = []  # (scan_num, field_id, duration_s)
        try:
            for snum in sorted(msmd.scannumbers()):
                fids = list(msmd.fieldsforscan(snum))
                if not fids:
                    continue
                times = msmd.timesforscans([snum])
                dur = float(max(times) - min(times)) if len(times) > 1 else 0.0
                scan_sequence.append((snum, int(fids[0]), dur))
            casa_calls.append("msmd.scannumbers/fieldsforscan/timesforscans (scan pattern)")
        except Exception:
            scan_sequence = []

        # Observing frequency per field. This tool was field-only until now; the
        # read is here because frequency is what decides whether a flux standard
        # applies to a source (see FLUX_STANDARD_DESIGN.md 2.2), and the answer
        # is per FIELD — a field is only observed in the SpWs it was observed in.
        field_freqs = field_frequencies(msmd, n_fields)
        casa_calls.append("msmd.spwsforfield(field_id) + msmd.chanfreqs(spw) for each field")

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
            "Roles are still resolved per field: a field WITH intents uses them. "
            "Fields without intents fall back to the calibrator catalogue and are "
            "tagged INFERRED — check those individually rather than trusting this "
            "MS-wide figure."
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
            catalogue_role = field(
                cal_entry.role,
                flag="COMPLETE",
                note=(
                    f"What the catalogue lists {cal_entry.canonical_name} as suitable for. "
                    "A cross-check against the intents, not the answer."
                ),
            )
            cal_resolved = field(cal_entry.resolved, flag="COMPLETE")
            if cal_entry.notes:
                warnings.append(f"[{name}] {cal_entry.notes}")
        else:
            cal_match = field(None, flag="UNAVAILABLE", note="Not in bundled calibrator catalogue")
            catalogue_role = field(None, flag="UNAVAILABLE")
            cal_resolved = field(None, flag="UNAVAILABLE")

        # --- Role resolution: this field's own intents decide ---
        #
        # Per FIELD, deliberately. The old code gated the catalogue fallback on
        # a whole-MS coverage threshold, so one field missing its intents inside
        # a well-populated MS got no role at all, even where the catalogue could
        # have answered for it. Coverage is a property of the MS; having intents
        # is a property of the field.
        intent_roles = role_from_intents(intents) if intents else []
        catalogue_roles = cal_entry.role if cal_entry else []

        if intent_roles:
            role_out = field(
                intent_roles,
                flag="COMPLETE",
                note="Derived from this field's scan intents.",
            )
        elif catalogue_roles:
            role_out = field(
                catalogue_roles,
                flag="INFERRED",
                note=(
                    "No intents name a role for this field. This is what the catalogue "
                    f"lists {cal_entry.canonical_name} as suitable for — not evidence of "
                    "how this observation used it."
                ),
            )
        else:
            role_out = field(
                None,
                flag="UNAVAILABLE",
                note="No scan intents name a role, and the field is not in the catalogue.",
            )

        if roles_disagree(intent_roles, catalogue_roles):
            warnings.append(
                f"[{name}] Intents and catalogue DISAGREE about this field's role. "
                f"The MS's scan intents say {intent_roles}; the catalogue lists "
                f"{cal_entry.canonical_name} as {catalogue_roles}. "
                "The intents win — they describe this observation, while the catalogue "
                "describes the source. Verify before calibrating."
            )

        # --- VLA calibrator positional cross-match ---
        vla_cal_match_field = _vla_positional_match(ra_deg, dec_deg, cal_entry)

        # --- Intents ---
        # Also per field. The catalogue fallback used to require heuristic_mode,
        # which meant a lone field missing its intents was never offered it.
        if intents:
            intent_field = field(sorted(intents), flag="COMPLETE")
        elif cal_entry:
            inferred = infer_intents_from_role(cal_entry.role)
            intent_field = field(
                inferred,
                flag="INFERRED",
                note=f"Inferred from calibrator catalogue role: {cal_entry.role}",
            )
        else:
            intent_field = field(
                [],
                flag="UNAVAILABLE",
                note="No intents recorded for this field and no catalogue match for inference",
            )

        # --- Observing frequency ---
        fq = field_freqs[fid] if fid < len(field_freqs) else None
        if fq and fq["min_ghz"] is not None:
            fq_note = f"Span of the {fq['n_spw']} spectral window(s) this field was observed in"
            if fq["excluded_spw"]:
                fq_note += f"; {fq['excluded_spw']} WVR/square-law window(s) excluded"
            freq_out = field(
                {
                    "min_ghz": round(fq["min_ghz"], 6),
                    "max_ghz": round(fq["max_ghz"], 6),
                    "centre_ghz": round(fq["centre_ghz"], 6),
                    "n_spw": fq["n_spw"],
                },
                flag="COMPLETE",
                note=fq_note,
            )
        else:
            freq_out = field(
                None,
                flag="UNAVAILABLE",
                note="No readable spectral window for this field",
            )

        # --- Flux standard: resolved from THIS field's frequency ---
        #
        # Deliberately after the frequency read, and deliberately not a
        # catalogue echo. The catalogue says which standard describes the
        # SOURCE; whether it describes this OBSERVATION depends on the band the
        # field was observed in. Reporting 'Perley-Butler 2017' COMPLETE on a
        # 230 GHz field was the defect this replaces.
        #
        # resolve_flux_standard lives in calibrators.py because ms_setjy calls
        # the same function. Two copies could disagree, and the tool that acts
        # would be the one that is wrong.
        std = resolve_flux_standard(
            cal_entry,
            fq["min_ghz"] if fq else None,
            fq["max_ghz"] if fq else None,
        )
        cal_standard = field(std.standard, flag=std.flag, note=std.note)

        # Warn only where the operator must do something: pick a different
        # standard, or supply a flux by hand. A constant-brightness-temperature
        # body and an unread frequency carry notes instead — the first is a
        # CASA modelling choice, not a metadata problem, and warning on either
        # would fire on every ALMA dataset.
        if std.needs_manual_flux:
            warnings.append(
                f"[{name}] CASA has no flux standard for this source. It needs an "
                "explicit manual flux density; do not substitute another standard."
            )
        elif std.flag == "UNAVAILABLE" and cal_entry is not None:
            warnings.append(f"[{name}] {std.note}")

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
            "observing_frequency": freq_out,
            "calibrator_match": cal_match,
            # field_role, not calibrator_role: the vocabulary includes 'target',
            # which is not a kind of calibrator.
            "field_role": role_out,
            "catalogue_role": catalogue_role,
            "flux_standard": cal_standard,
            # Whether the frequency gate actually RAN, not whether it passed.
            # flux_standard COMPLETE means both "checked and inside the range"
            # and "constant brightness temperature, no range exists to check".
            # Those are different amounts of evidence and the flag cannot
            # separate them.
            "flux_standard_range_checked": std.range_checked,
            "resolved_source": cal_resolved,
            "vla_cal_match": vla_cal_match_field,
        }
        fields_out.append(record)

    # ------------------------------------------------------------------
    # Scan-pattern role inference for UNAVAILABLE fields (heuristic mode only)
    # ------------------------------------------------------------------
    if heuristic_mode and scan_sequence:
        # Build per-field coord lookup (ra_deg, dec_deg)
        coords: dict[int, tuple[float | None, float | None]] = {}
        for rec in fields_out:
            fid = rec["field_id"]
            ra_f = rec.get("ra_j2000_deg", {})
            dec_f = rec.get("dec_j2000_deg", {})
            coords[fid] = (
                ra_f.get("value") if isinstance(ra_f, dict) else ra_f,
                dec_f.get("value") if isinstance(dec_f, dict) else dec_f,
            )

        inferred = _infer_roles_from_scan_pattern(scan_sequence, coords)

        for rec in fields_out:
            fid = rec["field_id"]
            intent_f = rec.get("intents", {})
            if (
                isinstance(intent_f, dict)
                and intent_f.get("flag") == "UNAVAILABLE"
                and fid in inferred
            ):
                roles, reason = inferred[fid]
                rec["intents"] = field(
                    infer_intents_from_role(roles),
                    flag="INFERRED",
                    note=f"Scan-pattern inference: {reason}",
                )

    # ------------------------------------------------------------------
    # nearest_phase_cal enrichment for target fields
    # ------------------------------------------------------------------
    # Classify fields into phase_cals and targets
    phase_cal_records = []
    for rec in fields_out:
        role_field = rec.get("field_role", {})
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
        role_field = rec.get("field_role", {})
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
        # A measurement, not a verdict. This used to be a boolean
        # `heuristic_intents`, set from the threshold below — but once role
        # resolution became per field, that boolean no longer described any
        # field's role, and it was wrong in both directions: true while a field
        # with intents used them, false while a field without intents fell back
        # to the catalogue. The per-field `field_role` flag is the answer; this
        # is the coverage statistic, with its inputs, for the skill to threshold
        # as it sees fit.
        "n_fields_with_intents": n_with_intents,
        "intent_coverage_fraction": round(intent_fraction, 4),
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


def _infer_roles_from_scan_pattern(
    scan_sequence: list[tuple[int, int, float]],
    coords: dict[int, tuple[float | None, float | None]],
    phase_cal_sep_deg: float = 20.0,
) -> dict[int, tuple[list[str], str]]:
    """
    Infer calibration roles for fields that have no intent metadata and no
    catalogue match, using only the scan interleave pattern.

    Returns a dict of field_id → (role_list, one-line reason).
    Only fields that can be confidently assigned a role are included.

    Heuristic:
    - The most-observed field (by scan count) is the science target.
    - A field that alternates with the target (appears in scans adjacent to
      target scans) and has shorter median duration is a phase calibrator
      candidate.
    - Angular separation from the target must be < phase_cal_sep_deg.
    - If the candidate appears in ≥ 4 distinct scans spread across the
      observation, note potential leakage cal suitability (PA check needed).
    """
    if not scan_sequence:
        return {}

    # Count scans per field
    from collections import Counter

    scan_count: Counter[int] = Counter(fid for _, fid, _ in scan_sequence)
    if not scan_count:
        return {}

    # Most-observed field = target (if ≥ 2 fields present)
    sorted_by_count = scan_count.most_common()
    target_fid = sorted_by_count[0][0]

    # Build adjacency: for each non-target field, count how many of its scans
    # are immediately adjacent (±1 position) to a target scan
    field_ids_in_order = [fid for _, fid, _ in scan_sequence]
    target_positions = {i for i, fid in enumerate(field_ids_in_order) if fid == target_fid}

    adjacency: Counter[int] = Counter()
    for pos, fid in enumerate(field_ids_in_order):
        if fid == target_fid:
            continue
        if (pos - 1) in target_positions or (pos + 1) in target_positions:
            adjacency[fid] += 1

    # Median scan duration per field
    from statistics import median

    durations: dict[int, list[float]] = {}
    for _, fid, dur in scan_sequence:
        durations.setdefault(fid, []).append(dur)
    median_dur: dict[int, float] = {fid: median(dlist) for fid, dlist in durations.items()}
    target_dur = median_dur.get(target_fid, 0.0)

    target_ra, target_dec = coords.get(target_fid, (None, None))

    result: dict[int, tuple[list[str], str]] = {}

    for fid, adj_count in adjacency.items():
        total = scan_count[fid]
        if total < 2:
            continue  # single scan — too ambiguous
        if adj_count < max(1, total // 2):
            continue  # doesn't reliably bracket target

        # Duration check: phase cals are typically shorter than the target
        fid_dur = median_dur.get(fid, 0.0)
        if target_dur > 0 and fid_dur > target_dur * 1.5:
            continue  # longer than target — unlikely phase cal

        # Angular separation check
        fid_ra, fid_dec = coords.get(fid, (None, None))
        if (
            target_ra is not None
            and target_dec is not None
            and fid_ra is not None
            and fid_dec is not None
        ):
            sep = _angular_sep_deg(target_ra, target_dec, fid_ra, fid_dec)
            if sep > phase_cal_sep_deg:
                continue
            sep_str = f"{sep:.1f}° from target"
        else:
            sep_str = "separation unknown"

        roles = ["phase"]
        reason = (
            f"alternates with field {target_fid} in {adj_count}/{total} scans, "
            f"median duration {fid_dur:.0f}s vs target {target_dur:.0f}s, {sep_str}"
        )

        # Flag potential leakage cal if well-sampled across the observation
        if total >= 4:
            reason += "; ≥4 scans — check PA coverage with ms_parallactic_angle_vs_time for D-term/QU suitability"

        result[fid] = (roles, reason)

    return result


def _vla_positional_match(
    ra_deg: float | None,
    dec_deg: float | None,
    cal_entry: CalibratorEntry | None = None,
) -> dict:
    """
    Attempt a positional cross-match against the VLA calibrator database.

    Returns a formatted field() dict with the match result.

    Solar-system bodies are skipped deliberately. They move, so the recorded
    phase centre is a position at one epoch and not an identity. A cone search
    on it either finds nothing or — worse — lands on an unrelated VLA
    calibrator that happens to sit near the ecliptic, and reports a confident
    match. Skipping is stated in the note, not left silent.
    """
    if cal_entry is not None and cal_entry.solar_system:
        return field(
            None,
            flag="UNAVAILABLE",
            note=(
                f"Positional cross-match not attempted: {cal_entry.canonical_name} is a "
                "solar-system body, so its phase centre is an epoch-dependent position "
                "rather than an identity."
            ),
        )

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
