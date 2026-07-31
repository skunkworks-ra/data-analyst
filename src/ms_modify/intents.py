"""
intents.py — Populate scan intent metadata in a Measurement Set.

Writes the STATE subtable and updates STATE_ID in the MAIN table, based on
calibrator catalogue matching and VLA calibrator positional cross-match.

Exposed as the ms_set_intents MCP tool (via server.py) and also callable
directly as a utility function by skills and scripts.
"""

from __future__ import annotations

from ms_inspect.util.calibrators import infer_intents_from_role
from ms_inspect.util.calibrators import lookup as cal_lookup
from ms_inspect.util.casa_context import open_msmd, open_table, validate_ms_path
from ms_inspect.util.conversions import rad_to_deg
from ms_inspect.util.formatting import field, response_envelope
from ms_inspect.util.pol_calibrators import lookup_pol
from ms_inspect.util.vla_calibrators import cone_search as vla_cone_search
from ms_modify.exceptions import IntentsAlreadyPopulatedError

TOOL_NAME = "set_intents"

# If this fraction or more of fields already have intents, refuse to overwrite.
_ALREADY_POPULATED_THRESHOLD = 0.50

POL_ANGLE_INTENT = "CALIBRATE_POL_ANGLE#ON_SOURCE"
POL_LEAKAGE_INTENT = "CALIBRATE_POL_LEAKAGE#ON_SOURCE"


def _pol_intents_for_field(name: str) -> tuple[list[str], str | None]:
    """
    Pure function: polarisation intents implied by a field's *catalogue identity*.

    Identity only, never coverage or strategy:
      - Category A angle standard (3C286 / 3C138 / 3C48) -> POL_ANGLE.
      - Dedicated leakage calibrator, i.e. catalogue role is leakage and NOT
        angle (3C84, OQ208, J0713+4349, J2355+4950, 3C147) -> POL_LEAKAGE.

    A source whose role carries both, which is every Category A standard, gets
    POL_ANGLE only. It can still serve as a Df source through its known model,
    but labelling it the leakage calibrator would hide a dedicated zero-pol cal
    observed in the same track behind whichever field happens to come first.

    Nominating a field that is *not* in the pol catalogue (the phase cal, say)
    as the leakage calibrator is a calibration-strategy decision and belongs to
    the caller: pass it in ``pol_leakage_fields``.

    Returns (intents, catalogue_name | None).
    """
    entry = lookup_pol(name)
    if entry is None:
        return [], None

    role = set(entry.role or [])
    intents: list[str] = []
    if "angle" in role and entry.category == "A":
        intents.append(POL_ANGLE_INTENT)
    elif "leakage" in role:
        intents.append(POL_LEAKAGE_INTENT)
    return intents, entry.b1950_name


def _compute_intent_map(
    fields: list[dict],
    pol_angle_fields: tuple[str, ...] = (),
    pol_leakage_fields: tuple[str, ...] = (),
) -> list[dict]:
    """
    Pure function: compute intent assignments for each field.

    Args:
        fields: List of dicts with keys: field_id, name, ra_deg, dec_deg,
                existing_intents (set of strings).
        pol_angle_fields:   Field names or ids the caller nominates as the
                            polarisation *angle* calibrator.
        pol_leakage_fields: Field names or ids the caller nominates as the
                            polarisation *leakage* calibrator. Use this for a
                            source the pol catalogue does not know, typically
                            the phase calibrator; the choice is the caller's.

    Returns:
        List of dicts: {field_id, name, intents, source}.
        - source is a "+"-joined trail of what contributed, e.g.
          "primary_catalogue+pol_catalogue" or "vla_cone_search+caller_nominated"
    """
    results = []
    nominated_angle = {str(x) for x in pol_angle_fields}
    nominated_leakage = {str(x) for x in pol_leakage_fields}

    for f in fields:
        fid = f["field_id"]
        name = f["name"]
        ra_deg = f["ra_deg"]
        dec_deg = f["dec_deg"]

        intents: list[str] = []
        sources: list[str] = []

        # 1. Primary catalogue match
        cal_entry = cal_lookup(name)
        if cal_entry:
            intents.extend(infer_intents_from_role(cal_entry.role))
            sources.append("primary_catalogue")
        else:
            # 2. VLA cone search positional match
            matched = False
            if ra_deg is not None and dec_deg is not None:
                try:
                    result = vla_cone_search(ra_deg, dec_deg, radius_arcsec=5.0)
                    if result is not None and result.name:
                        intents.append("CALIBRATE_PHASE#ON_SOURCE")
                        sources.append("vla_cone_search")
                        matched = True
                except Exception:
                    pass  # graceful fallback — treat as target
            if not matched:
                # 3. Default: target
                intents.append("OBSERVE_TARGET#ON_SOURCE")
                sources.append("default_target")

        # 4. Polarisation intents from catalogue identity, on top of the above.
        # A flux/bandpass calibrator is very often also the pol angle standard;
        # these are additive, not a replacement.
        pol_intents, _pol_name = _pol_intents_for_field(name)
        if pol_intents:
            intents.extend(pol_intents)
            sources.append("pol_catalogue")

        # 5. Caller nominations, by field name or field id. Applied last so an
        # explicit choice wins, and recorded so it is visible in the response.
        if name in nominated_angle or str(fid) in nominated_angle:
            if POL_ANGLE_INTENT not in intents:
                intents.append(POL_ANGLE_INTENT)
            sources.append("caller_nominated_angle")
        if name in nominated_leakage or str(fid) in nominated_leakage:
            if POL_LEAKAGE_INTENT not in intents:
                intents.append(POL_LEAKAGE_INTENT)
            sources.append("caller_nominated_leakage")

        results.append(
            {
                "field_id": fid,
                "name": name,
                "intents": sorted(set(intents)),
                "source": "+".join(sources),
            }
        )

    return results


def _pol_sources_available(
    fields: list[dict],
    intent_map: list[dict],
    band_ghz: float | None,
) -> dict:
    """
    Pure function: what polarisation calibration this MS's *sources* can support.

    Enumerates, it does not choose. If no dedicated leakage calibrator was
    observed, that is reported as a fact with the fields that exist, so the
    caller can nominate one via ``pol_leakage_fields`` and re-run. The tool
    never nominates on its own: which field to press into service as a leakage
    calibrator depends on parallactic coverage and on the science goal, and
    `ms_pol_cal_conditions` ranks the candidates for exactly that decision.
    """
    from ms_inspect.tools.pol_cal_conditions import _effective_role_at_band

    intents_by_id = {m["field_id"]: m["intents"] for m in intent_map}
    catalogued: list[dict] = []
    for f in fields:
        entry = lookup_pol(f["name"])
        if entry is None:
            continue
        catalogued.append(
            {
                "field_id": f["field_id"],
                "name": f["name"],
                "catalogue_source": entry.b1950_name,
                "category": entry.category,
                "catalogue_role": list(entry.role or []),
                "effective_role_at_band": (
                    _effective_role_at_band(entry, band_ghz)
                    if band_ghz is not None
                    else "unknown (band centre unavailable)"
                ),
                "assigned_intents": intents_by_id.get(f["field_id"], []),
            }
        )

    has_angle = any(POL_ANGLE_INTENT in m["intents"] for m in intent_map)
    has_leakage = any(POL_LEAKAGE_INTENT in m["intents"] for m in intent_map)

    return {
        "band_centre_ghz": band_ghz,
        "catalogued_pol_sources": catalogued,
        "angle_intent_assigned": has_angle,
        "leakage_intent_assigned": has_leakage,
        "uncatalogued_fields": [
            {"field_id": m["field_id"], "name": m["name"], "intents": m["intents"]}
            for m in intent_map
            if lookup_pol(m["name"]) is None
        ],
        "note": (
            "Polarisation intents are assigned from catalogue identity only. "
            "If leakage_intent_assigned is false, no dedicated leakage calibrator "
            "was observed: rank the fields with ms_pol_cal_conditions, then re-run "
            "with pol_leakage_fields=['<field name>'] to nominate one. Choosing "
            "that field is a calibration-strategy decision and is left to you."
        ),
    }


def _build_set_intents_script(
    ms_path: str,
    intent_map: list[dict],
    obs_modes: dict[str, int],
) -> str:
    """Return a self-contained Python script that reproduces the STATE writes."""
    obs_modes_repr = repr(obs_modes)
    field_to_state = {m["field_id"]: obs_modes[";".join(sorted(m["intents"]))] for m in intent_map}
    field_to_state_repr = repr(field_to_state)
    return f"""\
#!/usr/bin/env python
\"\"\"
Auto-generated by ms_set_intents (ms_modify).
Run with: python set_intents.py
\"\"\"
import numpy as np
from casatools import table as _tbtool

ms_path = {ms_path!r}

# Mapping: obs_mode_string -> state_row_index
obs_modes = {obs_modes_repr}
# Mapping: field_id -> state_row_index
field_to_state = {field_to_state_repr}

# --- Write STATE subtable ---
tb = _tbtool()
state_path = ms_path.rstrip("/") + "/STATE"
tb.open(state_path, nomodify=False)
existing = tb.nrows()
if existing > 0:
    tb.removerows(list(range(existing)))
for obs_mode_str, _idx in sorted(obs_modes.items(), key=lambda x: x[1]):
    tb.addrows(1)
    row = tb.nrows() - 1
    has_cal = any(i.startswith("CALIBRATE_") for i in obs_mode_str.split(";"))
    tb.putcell("OBS_MODE", row, obs_mode_str)
    tb.putcell("CAL", row, 1.0 if has_cal else 0.0)
    tb.putcell("SIG", row, True)
    tb.putcell("SUB_SCAN", row, 0)
    tb.putcell("FLAG_ROW", row, False)
    tb.putcell("REF", row, 0)
tb.close()

# --- Update STATE_ID in MAIN table ---
tb.open(ms_path, nomodify=False)
field_ids = tb.getcol("FIELD_ID")
state_ids = np.array([field_to_state.get(int(fid), 0) for fid in field_ids], dtype=np.int32)
tb.putcol("STATE_ID", state_ids)
tb.close()
print(f"Done. {{len(obs_modes)}} state rows written, {{len(field_ids)}} MAIN rows updated.")
"""


def set_intents(
    ms_path: str,
    *,
    dry_run: bool | None = None,
    execute: bool = False,
    workdir: str = "",
    pol_angle_fields: tuple[str, ...] | list[str] = (),
    pol_leakage_fields: tuple[str, ...] | list[str] = (),
) -> dict:
    """
    Populate scan intent metadata in a Measurement Set.

    Reads field names and positions, matches against calibrator catalogues,
    writes the STATE subtable, and updates STATE_ID in the MAIN table.

    Polarisation intents (CALIBRATE_POL_ANGLE, CALIBRATE_POL_LEAKAGE) are
    assigned from pol-catalogue identity. To press a field the catalogue does
    not know into service as a leakage calibrator, name it in
    ``pol_leakage_fields``: that is a strategy decision, so the tool requires it
    to be made explicitly rather than inferring one.

    Args:
        ms_path:  Path to the Measurement Set.
        execute:  If False (default), preview the mapping and write a script to
                  workdir/set_intents.py (if workdir provided). No MS writes.
                  If True, write the STATE subtable and update MAIN STATE_ID.
        workdir:  Directory for generated script (only used when execute=False).
        dry_run:  Deprecated alias for ``not execute``. Logs a warning if used.
        pol_angle_fields:   Field names (or ids as strings) to mark as the
                            polarisation angle calibrator, in addition to any
                            catalogue match.
        pol_leakage_fields: Field names (or ids as strings) to mark as the
                            polarisation leakage calibrator.

    Returns:
        Standard response envelope with the intent mapping and write counts.

    Raises:
        IntentsAlreadyPopulatedError: If ≥50% of fields already have intents.
    """
    import math

    import numpy as np

    # Handle deprecated dry_run alias
    if dry_run is not None:
        import warnings as _warnings

        _warnings.warn(
            "dry_run is deprecated; use execute=not dry_run instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        execute = not dry_run

    p = validate_ms_path(ms_path)
    casa_calls: list[str] = []
    warnings: list[str] = []

    # ------------------------------------------------------------------
    # Step 1: Read fields, positions, existing intents
    # ------------------------------------------------------------------
    with open_msmd(str(p)) as msmd:
        casa_calls.append("msmd.open()")

        field_names = list(msmd.fieldnames())
        casa_calls.append("msmd.fieldnames()")
        n_fields = len(field_names)

        fields_info: list[dict] = []
        for fid in range(n_fields):
            # Phase centre
            ra_deg = None
            dec_deg = None
            try:
                pc = msmd.phasecenter(fid)
                ra_rad = float(pc["m0"]["value"]) % (2 * math.pi)
                dec_rad = float(pc["m1"]["value"])
                ra_deg = rad_to_deg(ra_rad)
                dec_deg = rad_to_deg(dec_rad)
            except Exception:
                pass
            casa_calls.append(f"msmd.phasecenter({fid})")

            # Existing intents
            try:
                existing = set(msmd.intentsforfield(fid))
            except Exception:
                existing = set()
            casa_calls.append(f"msmd.intentsforfield({fid})")

            fields_info.append(
                {
                    "field_id": fid,
                    "name": field_names[fid],
                    "ra_deg": ra_deg,
                    "dec_deg": dec_deg,
                    "existing_intents": existing,
                }
            )

    # ------------------------------------------------------------------
    # Step 2: Guard — refuse if intents are already populated
    # ------------------------------------------------------------------
    n_with_intents = sum(1 for f in fields_info if f["existing_intents"])
    if n_fields > 0 and n_with_intents / n_fields >= _ALREADY_POPULATED_THRESHOLD:
        raise IntentsAlreadyPopulatedError(
            f"{n_with_intents}/{n_fields} fields already have scan intents "
            f"({n_with_intents / n_fields * 100:.0f}% coverage, "
            f"threshold {_ALREADY_POPULATED_THRESHOLD * 100:.0f}%). "
            "Refusing to overwrite existing intent metadata. "
            "Clear the STATE subtable manually if you want to re-run.",
            ms_path=ms_path,
        )

    # ------------------------------------------------------------------
    # Step 3: Compute intent map
    # ------------------------------------------------------------------
    intent_map = _compute_intent_map(
        fields_info,
        pol_angle_fields=tuple(pol_angle_fields),
        pol_leakage_fields=tuple(pol_leakage_fields),
    )

    # Band centre, for the frequency-dependent pol role annotation only. It
    # never changes an intent assignment; a failure here costs the annotation,
    # not the run.
    band_ghz: float | None = None
    try:
        with open_table(str(p / "SPECTRAL_WINDOW")) as tb:
            chan_freqs = tb.getcell("CHAN_FREQ", 0)
        band_ghz = round(float(np.median(chan_freqs)) / 1e9, 4)
        casa_calls.append("tb.getcell('CHAN_FREQ', 0) (band centre for pol roles)")
    except Exception as e:
        warnings.append(
            f"Could not read band centre frequency: {e}. Polarisation roles are "
            "reported without their frequency dependence resolved."
        )

    pol_sources = _pol_sources_available(fields_info, intent_map, band_ghz)

    # Name any caller nomination that matched no field, rather than silently
    # dropping it: a typo here means the leakage intent is simply absent.
    all_names = {f["name"] for f in fields_info} | {str(f["field_id"]) for f in fields_info}
    for label, nominated in (
        ("pol_angle_fields", pol_angle_fields),
        ("pol_leakage_fields", pol_leakage_fields),
    ):
        for entry_name in nominated:
            if str(entry_name) not in all_names:
                warnings.append(
                    f"{label} entry '{entry_name}' matched no field in this MS; "
                    "no intent was assigned for it."
                )

    # ------------------------------------------------------------------
    # Step 4: Compute obs_modes (needed for both paths)
    # ------------------------------------------------------------------
    obs_modes: dict[str, int] = {}
    for m in intent_map:
        obs_mode = ";".join(sorted(m["intents"]))
        if obs_mode not in obs_modes:
            obs_modes[obs_mode] = len(obs_modes)

    # ------------------------------------------------------------------
    # Step 5: If execute=False, write script and return preview
    # ------------------------------------------------------------------
    if not execute:
        script_path: str | None = None
        if workdir:
            from pathlib import Path as _Path

            script_path = str(_Path(workdir) / "set_intents.py")
            _Path(script_path).write_text(_build_set_intents_script(ms_path, intent_map, obs_modes))
            casa_calls.append(f"write_script → {script_path}")

        data = {
            "n_fields": n_fields,
            "field_intent_map": [
                {
                    "field_id": m["field_id"],
                    "name": m["name"],
                    "intents": field(m["intents"], flag="INFERRED", note=f"source: {m['source']}"),
                    "source": m["source"],
                }
                for m in intent_map
            ],
            "n_unique_states": len(obs_modes),
            "state_rows_written": 0,
            "main_rows_updated": 0,
            "execute": False,
            "pol_sources_available": pol_sources,
        }
        if script_path:
            data["script_path"] = script_path
            warnings.append(
                f"Preview only — script written to {script_path}. "
                "Set execute=True to write intents to the MS."
            )
        else:
            warnings.append(
                "Preview only — no changes written to the MS. Pass workdir to also generate a script."
            )
        return response_envelope(
            tool_name=TOOL_NAME,
            ms_path=ms_path,
            data=data,
            warnings=warnings,
            casa_calls=casa_calls,
        )

    # ------------------------------------------------------------------
    # Step 6: Write STATE subtable (execute=True path)
    # ------------------------------------------------------------------
    state_path = str(p / "STATE")
    with open_table(state_path, read_only=False) as tb:
        casa_calls.append(f"tb.open('{state_path}', nomodify=False)")

        # Clear existing rows if any (intent-less MSes may have empty STATE)
        existing_rows = tb.nrows()
        if existing_rows > 0:
            tb.removerows(list(range(existing_rows)))
            casa_calls.append(f"tb.removerows(range({existing_rows}))")

        for obs_mode_str, _row_idx in sorted(obs_modes.items(), key=lambda x: x[1]):
            tb.addrows(1)
            row = tb.nrows() - 1
            has_cal = any(i.startswith("CALIBRATE_") for i in obs_mode_str.split(";"))
            tb.putcell("OBS_MODE", row, obs_mode_str)
            tb.putcell("CAL", row, 1.0 if has_cal else 0.0)
            tb.putcell("SIG", row, True)
            tb.putcell("SUB_SCAN", row, 0)
            tb.putcell("FLAG_ROW", row, False)
            tb.putcell("REF", row, 0)
        casa_calls.append(f"tb.addrows + tb.putcell for {len(obs_modes)} state rows")

    n_state_rows = len(obs_modes)

    # ------------------------------------------------------------------
    # Step 7: Update STATE_ID in MAIN table
    # ------------------------------------------------------------------
    field_to_state: dict[int, int] = {}
    for m in intent_map:
        obs_mode = ";".join(sorted(m["intents"]))
        field_to_state[m["field_id"]] = obs_modes[obs_mode]

    with open_table(str(p), read_only=False) as tb:
        casa_calls.append(f"tb.open('{p}', nomodify=False)")

        field_ids = tb.getcol("FIELD_ID")
        casa_calls.append("tb.getcol('FIELD_ID')")

        state_ids = np.array(
            [field_to_state.get(int(fid), 0) for fid in field_ids],
            dtype=np.int32,
        )
        tb.putcol("STATE_ID", state_ids)
        casa_calls.append("tb.putcol('STATE_ID', state_id_array)")

    n_main_rows = len(field_ids)

    # ------------------------------------------------------------------
    # Step 8: Return result
    # ------------------------------------------------------------------
    data = {
        "n_fields": n_fields,
        "field_intent_map": [
            {
                "field_id": m["field_id"],
                "name": m["name"],
                "intents": field(m["intents"], flag="INFERRED", note=f"source: {m['source']}"),
                "source": m["source"],
            }
            for m in intent_map
        ],
        "n_unique_states": n_state_rows,
        "state_rows_written": n_state_rows,
        "main_rows_updated": int(n_main_rows),
        "execute": True,
        "pol_sources_available": pol_sources,
    }

    return response_envelope(
        tool_name=TOOL_NAME,
        ms_path=ms_path,
        data=data,
        warnings=warnings,
        casa_calls=casa_calls,
    )
