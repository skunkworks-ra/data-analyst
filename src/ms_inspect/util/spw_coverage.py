"""
util/spw_coverage.py — Solve-vs-target spectral-window coverage guardrail.

A calibration solve (bandpass / delay / gain) produces a caltable that is later
applied to the science target and the phase-transfer calibrators. If the field
solved on does not carry the spectral windows those downstream fields need, the
applied solution flags the uncovered SpWs — and the failure typically surfaces
several stages later (e.g. as fluxscale "Cannot find solutions for transfer
field(s)") rather than at solve time.

The pathological case (observed on AB1345 / G55.7+3.4): a single source recorded
under two field IDs with *disjoint* SpWs — 3C286 as field 0 (SpWs 0,1) and field
1 (SpWs 2-9) — where the target lives only in 2-9. Solving the bandpass on field
0 silently flagged everything downstream.

This module surfaces the cal-field-vs-target SpW sets at solve time. The set math
(`evaluate_coverage`) is pure and CASA-free; `check_spw_coverage` is the thin
msmd wrapper used by the ms_modify solve tools.

Policy:
  * SpW-coverage mismatches are WARN-only (returned to the caller's warnings[]).
  * If the target/transfer fields cannot be inferred from intents and the caller
    passed no explicit target_fields, the wrapper STOPS (raises ComputationError)
    rather than guessing — the agent surfaces it to the user.
  * If msmd cannot be opened at all (no CASA, stub MS, script-generation on a
    path without metadata), the check degrades silently to no warnings.
"""

from __future__ import annotations

import logging

from ms_inspect.exceptions import ComputationError
from ms_inspect.util.casa_context import open_msmd

logger = logging.getLogger(__name__)

# CASA intent substrings that mark a field whose data the solve will be applied
# to: the science target and the phase calibrator (whose gains transfer to the
# target). Matched case-insensitively against msmd.intentsforfield().
_TARGET_INTENT_TOKENS = ("TARGET",)  # OBSERVE_TARGET#ON_SOURCE
_TRANSFER_INTENT_TOKENS = ("CALIBRATE_PHASE",)  # phase ref transferred to target


# ---------------------------------------------------------------------------
# Pure set math — no CASA
# ---------------------------------------------------------------------------


def evaluate_coverage(
    solve_spws: set[int],
    target_spws: set[int],
    selected_spws: set[int] | None,
    solve_label: str,
    target_label: str,
) -> list[str]:
    """Compare solve-field SpW coverage against the target's SpWs.

    Args:
        solve_spws:    SpWs the solve field(s) actually carry (no spw selection
                       applied yet).
        target_spws:   Union of SpWs over the science-target / transfer fields.
        selected_spws: Top-level SpW IDs from an explicit `spw` selection, or
                       None when the caller selected all SpWs.
        solve_label:   Human label for the solve field(s) (for the message).
        target_label:  Human label for the target/transfer field(s).

    Returns:
        List of warning strings (possibly empty). Never raises.
    """
    warnings: list[str] = []

    effective = solve_spws if selected_spws is None else (solve_spws & selected_spws)

    if not effective:
        warnings.append(
            f"SpW selection leaves no SpWs to solve on field(s) {solve_label} "
            f"(field carries {sorted(solve_spws)}, selection kept "
            f"{sorted(selected_spws) if selected_spws is not None else 'all'}). "
            "The solve will produce no usable solutions."
        )
        return warnings

    uncovered = target_spws - effective
    if uncovered:
        warnings.append(
            f"SpW coverage gap: solve field(s) {solve_label} cover SpWs "
            f"{sorted(effective)}, but target/transfer field(s) {target_label} need "
            f"SpWs {sorted(target_spws)}. SpWs {sorted(uncovered)} are uncovered — "
            "applying these solutions will flag those SpWs on the downstream fields. "
            "Solve on a field that shares the target's SpWs."
        )

    if selected_spws is not None:
        # The solve field DOES carry target SpWs, but the explicit spw selection
        # dropped some of them. Distinct from the disjoint-field case above.
        excluded = (solve_spws & target_spws) - selected_spws
        if excluded:
            warnings.append(
                f"Explicit spw selection excludes SpWs {sorted(excluded)} that the "
                f"target needs and field(s) {solve_label} carry; widen the spw "
                "selection to cover them."
            )

    return warnings


# ---------------------------------------------------------------------------
# msmd wrapper
# ---------------------------------------------------------------------------


def _resolve_field_ids(msmd, field_sel: str, nfields: int) -> set[int]:
    """Resolve a CASA field selection (names and/or IDs) to a set of field IDs."""
    sel = (field_sel or "").strip()
    if not sel or sel == "*":
        return set(range(nfields))
    ids: set[int] = set()
    for tok in sel.split(","):
        tok = tok.strip()
        if not tok:
            continue
        if tok.isdigit():
            ids.add(int(tok))
        else:
            try:
                ids.update(int(i) for i in msmd.fieldsforname(tok))
            except Exception:
                logger.debug("fieldsforname(%r) failed; ignoring token", tok)
    return ids


def _parse_spw_ids(spw_sel: str) -> set[int] | None:
    """Parse top-level SpW IDs from a CASA spw selection string.

    Returns None for an empty selection (= all SpWs). Channel suffixes (':...')
    are dropped; 'a~b' ranges are expanded. Best-effort — unparseable tokens are
    skipped (a guardrail, not a CASA selection engine).
    """
    sel = (spw_sel or "").strip()
    if not sel or sel == "*":
        return None
    ids: set[int] = set()
    for tok in sel.split(","):
        tok = tok.split(":")[0].strip()  # drop channel selection
        if not tok or tok == "*":
            continue
        if "~" in tok:
            try:
                lo, hi = (int(x) for x in tok.split("~", 1))
                ids.update(range(lo, hi + 1))
            except ValueError:
                continue
        elif tok.isdigit():
            ids.add(int(tok))
    return ids or None


def _intent_targets(msmd, all_ids: set[int], solve_ids: set[int]) -> set[int]:
    """Infer target/transfer field IDs from intents, excluding the solve fields."""
    tokens = tuple(t.upper() for t in (_TARGET_INTENT_TOKENS + _TRANSFER_INTENT_TOKENS))
    targets: set[int] = set()
    for fid in all_ids:
        try:
            intents = msmd.intentsforfield(fid)
        except Exception:
            continue
        joined = " ".join(str(i).upper() for i in intents)
        if any(tok in joined for tok in tokens):
            targets.add(fid)
    return targets - solve_ids


def check_spw_coverage(
    ms_path: str,
    solve_field: str,
    spw_sel: str,
    target_fields: str = "",
) -> list[str]:
    """Warn when the solve field's SpWs don't cover the target's SpWs.

    Args:
        ms_path:       MS the solve runs on.
        solve_field:   CASA field selection being solved on.
        spw_sel:       CASA spw selection for the solve ('' = all).
        target_fields: Optional explicit CASA field selection for the
                       science-target / transfer fields. Empty = infer from
                       intents.

    Returns:
        Warning strings for the caller's warnings[] (possibly empty).

    Raises:
        ComputationError: target/transfer fields could not be inferred from
            intents and no explicit target_fields was given (STOP-and-ask).
    """
    try:
        with open_msmd(ms_path) as msmd:
            nfields = int(msmd.nfields())
            all_ids = set(range(nfields))
            solve_ids = _resolve_field_ids(msmd, solve_field, nfields)

            if target_fields and target_fields.strip():
                target_ids = _resolve_field_ids(msmd, target_fields, nfields) - solve_ids
                inferred = False
            else:
                target_ids = _intent_targets(msmd, all_ids, solve_ids)
                inferred = True

            if not target_ids:
                if inferred:
                    raise ComputationError(
                        "Cannot infer the science-target / transfer fields from "
                        "intents (no OBSERVE_TARGET or CALIBRATE_PHASE found, or they "
                        "coincide with the solve field). Cannot verify SpW coverage — "
                        "pass target_fields explicitly (e.g. the science target and "
                        "phase-calibrator field names).",
                        ms_path=ms_path,
                    )
                # Explicit target_fields resolved to nothing useful → nothing to check.
                return []

            def _spws(ids: set[int]) -> set[int]:
                out: set[int] = set()
                for fid in ids:
                    try:
                        out.update(int(s) for s in msmd.spwsforfield(fid))
                    except Exception:
                        continue
                return out

            solve_spws = _spws(solve_ids)
            target_spws = _spws(target_ids)
            selected_spws = _parse_spw_ids(spw_sel)
            solve_label = solve_field or "(all)"
            target_label = (
                target_fields.strip()
                if (target_fields and target_fields.strip())
                else f"{sorted(target_ids)} (inferred from intents)"
            )
    except ComputationError:
        raise
    except Exception:
        # No CASA, stub MS, or metadata unavailable — degrade to no check.
        logger.debug("SpW coverage check skipped: msmd unavailable for %s", ms_path)
        return []

    return evaluate_coverage(
        solve_spws, target_spws, selected_spws, solve_label, target_label
    )
