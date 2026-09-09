"""
setjy.py — ms_setjy

Sets flux density models for the catalogued flux calibrators in an MS, each
with the standard that applies AT ITS OWN OBSERVING FREQUENCY.

Logic:
  1. Read field names, and each field's observed frequency span, from the MS.
  2. Cross-match against the bundled calibrator catalogue.
  3. Resolve a standard PER FIELD via calibrators.resolve_flux_standard().
  4. Write one setjy() call per field, each with its own standard.
  5. Warn if 3C84 (resolved), 3C138, or 3C48 (variable/partially polarized).

Why per field: an MS can need two standards at once. The ALMA case is exactly
that — Ceres on a solar-system model plus a quasar on Perley-Butler — and a
single run-level standard cannot express it. The same tool previously applied
'Perley-Butler 2017' to every flux field regardless of band, which silently
mis-scales any field observed outside that model's 0.05-50 GHz validity.

The resolution lives in ms_inspect.util.calibrators, NOT here, because
ms_field_list reports the same answer. If the two drifted, the tool that acts
would be the one that is wrong.

Does NOT set polarization angle models (see CALPOL.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ms_inspect.util.calibrators import lookup, resolve_flux_standard
from ms_inspect.util.casa_context import open_msmd, open_table, validate_ms_path
from ms_inspect.util.formatting import field as fmt_field
from ms_inspect.util.formatting import response_envelope
from ms_inspect.util.frequencies import field_frequencies
from ms_inspect.util.stage_log import record_stage

TOOL_NAME = "ms_setjy"

# Calibrators requiring special treatment
_RESOLVED_WARN = {"3C84", "3C286", "3C147", "3C48"}  # may be resolved
_VARIABLE_WARN = {"3C84", "3C138", "3C48"}  # variable or partially pol
# Empty means "resolve per field from the observing frequency". A non-empty
# value is a deliberate whole-run OVERRIDE and skips the frequency gate.
_RESOLVE_PER_FIELD = ""


def _get_field_names(ms_str: str) -> list[str]:
    """Read field names from the FIELD subtable."""
    with open_table(ms_str + "/FIELD") as tb:
        # Coerce numpy str_ to plain str: repr(np.str_(...)) renders as
        # "np.str_('...')" under numpy >= 2, which is a NameError in the
        # generated script (no numpy import there).
        return [str(n) for n in tb.getcol("NAME")]


def _get_field_frequencies(ms_str: str, n_fields: int) -> list[dict] | None:
    """
    Observed frequency span per field, or None if msmd cannot be opened.

    None is NOT an error. Every field then resolves as INFERRED — the standard
    is still reported but the frequency gate did not run — which is the honest
    answer and lets the tool keep working on an MS whose metadata is thin.
    """
    try:
        with open_msmd(ms_str) as msmd:
            return field_frequencies(msmd, n_fields)
    except Exception:
        return None


def _build_setjy_block(field_name: str, standard: str, usescratch: bool) -> str:
    """Return a single setjy() call string for the script."""
    return (
        f"setjy(\n"
        f"    vis=ms_path,\n"
        f"    field={field_name!r},\n"
        f"    standard={standard!r},\n"
        f"    usescratch={usescratch},\n"
        f")"
    )


def _build_manual_block(field_name: str, spec: dict, usescratch: bool) -> str:
    """
    Return a setjy(standard='manual') call for a source CASA cannot model.

    Only the keys the caller supplied are emitted. Nothing is defaulted: a
    fabricated spectral index or reference frequency would be indistinguishable
    from a measured one in the generated script.
    """
    lines = [
        "setjy(",
        "    vis=ms_path,",
        f"    field={field_name!r},",
        "    standard='manual',",
    ]
    for key in ("fluxdensity", "spix", "reffreq"):
        if key in spec:
            lines.append(f"    {key}={spec[key]!r},")
    lines.append(f"    usescratch={usescratch},")
    lines.append(")")
    return "\n".join(lines)


@dataclass
class _FieldPlan:
    """One resolved field: which setjy call it gets, and why."""

    name: str
    mode: str  # 'standard' | 'manual'
    standard: str | None = None
    manual: dict | None = None
    note: str = ""
    range_checked: bool = False


def _build_script(
    ms_str: str,
    workdir: str,
    plans: list[_FieldPlan],
    usescratch: bool,
    warnings_inline: list[str],
) -> str:
    """Return a self-contained setjy Python script."""
    from ms_inspect.util.stage_log import RECORD_STAGE_SNIPPET as record
    from ms_inspect.util.stage_log import TABLE_PROBE_SNIPPET as probe

    warn_block = ""
    if warnings_inline:
        warn_lines = "\n".join(f"# WARNING: {w}" for w in warnings_inline)
        warn_block = warn_lines + "\n\n"

    blocks = []
    for plan in plans:
        # The note travels into the script. Someone reading setjy.py six months
        # from now needs to see WHY this field got this standard, and the
        # response envelope will not be there.
        header = f"# {plan.name}: {plan.note}" if plan.note else f"# {plan.name}"
        if plan.mode == "manual":
            body = _build_manual_block(plan.name, plan.manual or {}, usescratch)
        else:
            body = _build_setjy_block(plan.name, plan.standard or "", usescratch)
        blocks.append(f"{header}\n{body}")
    setjy_blocks = "\n\n".join(blocks)

    no_flux_block = ""
    if not plans:
        no_flux_block = (
            "# No field resolved to a usable flux standard.\n"
            "# Check the tool response: a field can be skipped because it is not a\n"
            "# flux calibrator, or because it was observed outside its model's\n"
            "# validity range. Those are different problems.\n"
        )

    return f"""\
#!/usr/bin/env python
\"\"\"
Auto-generated by ms_setjy (ms_modify).
Run with: python setjy.py
\"\"\"
from casatasks import setjy

{record}
{probe}

ms_path = {ms_str!r}

{warn_block}{no_flux_block}{setjy_blocks}
# setjy() returns None, so completion is measured rather than trusted: the
# column it exists to populate is either there or not. This does not prove
# the flux SCALE is right, only that a model was written.
_model = "MODEL_DATA" in _table_colnames(ms_path)
_record_stage({workdir!r}, "setjy", ms_path, {{"model_data": _model, "usescratch": {usescratch!r}}})
print("setjy complete.")
"""


def run(
    ms_path: str,
    workdir: str,
    standard: str = _RESOLVE_PER_FIELD,
    manual_flux: dict | None = None,
    usescratch: bool = True,
    exclude_fields: str = "",
    execute: bool = False,
) -> dict:
    """
    Set flux density models for standard calibrators in the MS.

    Args:
        ms_path:    Path to calibrators.ms (or full MS).
        workdir:    Existing output directory for setjy.py script.
        standard:   Whole-run OVERRIDE. Default '' resolves a standard per
                    field from that field's own observing frequency, which is
                    what an MS needing two standards requires. A non-empty
                    value forces one standard on every flux field AND SKIPS THE
                    FREQUENCY CHECK — use it only when you know better than the
                    catalogue.
        manual_flux: Explicit flux for sources CASA has no model for, as
                    {field_name: {'fluxdensity': [I, Q, U, V], 'spix': ...,
                    'reffreq': ...}}. Only the keys given are emitted; nothing
                    is defaulted. Without an entry such a field is SKIPPED with
                    a warning naming what is missing — never silently routed to
                    some other standard.
        exclude_fields: Comma-separated field NAMES to omit from the Stokes-I
                    setjy pass, even if they are catalogued flux standards. Pass
                    the pol-angle calibrator here when it overlaps a flux/BP cal:
                    its full polarized model is set by ms_setjy_polcal
                    (usescratch=True), and a plain Stokes-I setjy on that field
                    would overwrite the polarization (MODEL is last-writer-wins
                    per field). Excluded fields still get a consistent physical
                    MODEL_DATA from ms_setjy_polcal, so usescratch consistency
                    is preserved.
        usescratch: If True (default), fill the physical MODEL_DATA column (so
                    ms_residual_stats and polarization calibration work). If
                    False, write a virtual model (no MODEL_DATA column).
                    Must be consistent across ALL setjy calls on one MS: if
                    polarization calibration is in scope, ms_setjy_polcal forces
                    usescratch=True (virtual models fail on source models with
                    non-zero RM — a known CASA bug), so the flux/bandpass cals
                    must use usescratch=True here too. Mixing the two leaves the
                    virtual-model fields at MODEL_DATA=1 Jy and corrupts the
                    flux scale (fluxscale comes out order-of-magnitude low).
        execute:    If False (default), write setjy.py and return.
                    If True, run setjy in-process for each flux field.

    Returns:
        Standard response envelope with flux_fields, skipped_fields,
        warnings (3C84/3C138/3C48 advisory), and script_path.
    """
    p = validate_ms_path(ms_path)
    ms_str = str(p)
    casa_calls: list[str] = []
    warnings: list[str] = []

    workdir_path = Path(workdir)
    if not workdir_path.exists():
        from ms_inspect.exceptions import ComputationError

        raise ComputationError(
            f"workdir does not exist: {workdir}. Create it before calling this tool.",
            ms_path=ms_path,
        )

    # Read field names from MS
    try:
        field_names = _get_field_names(ms_str)
        casa_calls.append("tb.open(FIELD) → getcol(NAME)")
    except Exception as exc:
        from ms_inspect.exceptions import ComputationError

        raise ComputationError(
            f"Could not read FIELD subtable: {exc}",
            ms_path=ms_path,
        ) from exc

    # Per-field observing frequency. None means msmd was unreadable, which is
    # not fatal: every field then resolves INFERRED (standard reported, gate
    # not run) instead of the tool failing.
    freqs = _get_field_frequencies(ms_str, len(field_names))
    if freqs is None:
        casa_calls.append("msmd frequency read FAILED — frequency gate did not run")
        warnings.append(
            "Could not read per-field observing frequencies from this MS. Flux "
            "standards were taken from the catalogue WITHOUT checking them against "
            "the observing band. Verify each standard covers its field's frequency."
        )
    else:
        casa_calls.append("msmd.spwsforfield + msmd.chanfreqs for each field")

    # Cross-match against catalogue — only keep flux calibrators
    manual_flux = manual_flux or {}
    plans: list[_FieldPlan] = []
    skipped_fields: list[str] = []
    # Kept separate from skipped_fields on purpose. "Not a flux calibrator" and
    # "a flux calibrator we could not scale" are different facts, and merging
    # them hides the second behind the first.
    skipped_no_standard: list[dict] = []
    excluded_fields: list[str] = []
    inline_warnings: list[str] = []
    resolution: list[dict] = []

    exclude_set = {n.strip() for n in exclude_fields.split(",") if n.strip()}
    override = bool(standard)

    for fid, fname in enumerate(field_names):
        entry = lookup(fname)
        if fname in exclude_set:
            # Caller-requested skip: the field's model is set elsewhere
            # (ms_setjy_polcal). Do not write a Stokes-I model over it.
            excluded_fields.append(fname)
            continue
        if entry is None or "flux" not in entry.role:
            skipped_fields.append(fname)
            continue

        # Advisory warnings for specific sources
        if entry.canonical_name in _VARIABLE_WARN:
            msg = (
                f"{fname} ({entry.canonical_name}) is variable or partially "
                "polarized at frequencies below 4 GHz. Verify flux model validity "
                "for your band before using these solutions."
            )
            warnings.append(msg)
            inline_warnings.append(msg)
        if entry.resolved:
            msg = (
                f"{fname} ({entry.canonical_name}) is resolved on long baselines. "
                "If your array has baselines > safe UV limit, use a component model "
                "instead of a point-source model."
            )
            warnings.append(msg)
            inline_warnings.append(msg)

        fq = freqs[fid] if freqs is not None and fid < len(freqs) else None
        min_ghz = fq["min_ghz"] if fq else None
        max_ghz = fq["max_ghz"] if fq else None

        if override:
            # The caller named a standard. Honour it verbatim and say plainly
            # that the frequency check was skipped, rather than half-applying
            # a gate the caller asked to bypass.
            plans.append(
                _FieldPlan(
                    name=fname,
                    mode="standard",
                    standard=standard,
                    note=f"whole-run override; frequency not checked ({standard})",
                )
            )
            resolution.append(
                {
                    "field": fname,
                    "standard": standard,
                    "flag": "INFERRED",
                    "range_checked": False,
                    "note": "Caller-supplied whole-run override; frequency gate skipped.",
                }
            )
            continue

        res = resolve_flux_standard(entry, min_ghz, max_ghz)
        resolution.append(
            {
                "field": fname,
                "standard": res.standard,
                "flag": res.flag,
                "range_checked": res.range_checked,
                "note": res.note,
            }
        )

        if res.standard is not None:
            plans.append(
                _FieldPlan(
                    name=fname,
                    mode="standard",
                    standard=res.standard,
                    note=res.note,
                    range_checked=res.range_checked,
                )
            )
            if res.flag == "INFERRED":
                warnings.append(f"{fname}: {res.note}")
                inline_warnings.append(f"{fname}: {res.note}")
            continue

        # No standard. Either CASA has no model for the source (manual flux) or
        # the field was observed outside the model's range (a real problem).
        if res.needs_manual_flux and fname in manual_flux:
            plans.append(
                _FieldPlan(
                    name=fname,
                    mode="manual",
                    manual=manual_flux[fname],
                    note="caller-supplied manual flux; CASA has no model for this source",
                )
            )
            continue

        if res.needs_manual_flux:
            msg = (
                f"{fname}: CASA has no flux standard for this source. SKIPPED. Pass "
                f"manual_flux={{'{fname}': {{'fluxdensity': [I, Q, U, V], 'spix': ..., "
                "'reffreq': ...}} to set it explicitly. It was NOT given another standard."
            )
        else:
            msg = f"{fname}: SKIPPED. {res.note}"
        warnings.append(msg)
        inline_warnings.append(msg)
        skipped_no_standard.append({"field": fname, "reason": res.note})

    # A manual_flux entry naming a field that never needed one is a mistake
    # worth surfacing — most likely a typo in the field name.
    planned_manual = {p.name for p in plans if p.mode == "manual"}
    unused_manual = sorted(set(manual_flux) - planned_manual)
    if unused_manual:
        warnings.append(
            f"manual_flux entries not used: {unused_manual}. Either the field is not "
            "in this MS, or it already resolves to a CASA standard. Check the names "
            "against ms_field_list."
        )

    flux_fields = [p.name for p in plans]

    script_path = str(workdir_path / "setjy.py")
    script_content = _build_script(ms_str, str(workdir_path), plans, usescratch, inline_warnings)
    Path(script_path).write_text(script_content)
    casa_calls.append(f"write_script → {script_path}")

    base_data: dict = {
        "script_path": fmt_field(script_path),
        "flux_fields": fmt_field(flux_fields),
        "skipped_fields": fmt_field(skipped_fields),
        "skipped_no_standard": fmt_field(skipped_no_standard),
        "excluded_fields": fmt_field(excluded_fields),
        "flux_standard_resolution": fmt_field(resolution),
        "standard": standard,
        "standard_mode": "override" if override else "per_field",
        "n_range_checked": sum(1 for r in resolution if r["range_checked"]),
        "usescratch": usescratch,
        "n_flux_fields": len(flux_fields),
    }

    # Warn if a requested exclusion name never appeared in the MS (likely a typo).
    unmatched_excludes = sorted(exclude_set - set(excluded_fields))
    if unmatched_excludes:
        warnings.append(
            f"exclude_fields names not found in the MS FIELD table: {unmatched_excludes}. "
            "Check the spelling against ms_field_list."
        )

    if not execute:
        if not flux_fields:
            warnings.append(
                "No field resolved to a usable flux standard. This is not always a "
                "naming problem: check skipped_no_standard, which lists flux "
                "calibrators that were found but could not be scaled."
            )
        else:
            warnings.append(
                f"Script written to {script_path}. Run it externally to set flux models."
            )
        return response_envelope(
            tool_name=TOOL_NAME,
            ms_path=ms_path,
            data=base_data,
            warnings=warnings,
            casa_calls=casa_calls,
        )

    # execute=True: run setjy in-process
    try:
        from casatasks import setjy  # type: ignore[import]
    except ImportError:
        from ms_inspect.exceptions import CASANotAvailableError

        raise CASANotAvailableError(
            "casatasks is not installed or cannot be imported.",
            ms_path=ms_path,
        ) from None

    # Drives off the SAME plans as the script. Two code paths that resolved
    # the standard separately would be free to disagree, and execute=True is
    # the path that actually writes MODEL_DATA.
    fields_done: list[str] = []
    for plan in plans:
        if plan.mode == "manual":
            spec = plan.manual or {}
            kwargs = {k: spec[k] for k in ("fluxdensity", "spix", "reffreq") if k in spec}
            casa_calls.append(
                f"casatasks.setjy(field='{plan.name}', standard='manual', "
                f"{kwargs}, usescratch={usescratch})"
            )
            try:
                setjy(
                    vis=ms_str,
                    field=plan.name,
                    standard="manual",
                    usescratch=usescratch,
                    **kwargs,
                )
                fields_done.append(plan.name)
            except Exception as exc:
                warnings.append(f"setjy(field='{plan.name}', standard='manual') failed: {exc}")
            continue

        casa_calls.append(
            f"casatasks.setjy(field='{plan.name}', standard='{plan.standard}', "
            f"usescratch={usescratch})"
        )
        try:
            setjy(
                vis=ms_str,
                field=plan.name,
                standard=plan.standard,
                usescratch=usescratch,
            )
            fields_done.append(plan.name)
        except Exception as exc:
            warnings.append(f"setjy(field='{plan.name}') failed: {exc}")

    base_data["fields_done"] = fmt_field(fields_done)
    with open_table(ms_str) as tb:
        model_present = "MODEL_DATA" in set(tb.colnames())
    record_stage(
        str(workdir_path),
        "setjy",
        ms_str,
        {
            "model_data": model_present,
            "usescratch": usescratch,
            "n_fields_done": len(fields_done),
        },
    )
    return response_envelope(
        tool_name=TOOL_NAME,
        ms_path=ms_path,
        data=base_data,
        warnings=warnings,
        casa_calls=casa_calls,
    )
