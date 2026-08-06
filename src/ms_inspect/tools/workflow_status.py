"""
tools/workflow_status.py — ms_workflow_status

Rolls up the state of an MS + workdir into a single next-step label.
Composer over filesystem + existing tool logic. No new CASA calls beyond
what verify_import / priorcals_check / caltables do.
"""

from __future__ import annotations

from pathlib import Path

from ms_inspect.util.casa_context import open_table, validate_ms_path
from ms_inspect.util.formatting import field, response_envelope

TOOL_NAME = "ms_workflow_status"


def run(ms_path: str, workdir: str) -> dict:
    p = validate_ms_path(ms_path)
    ms_str = str(p)
    wd = Path(workdir)
    casa_calls: list[str] = []
    warnings: list[str] = []

    # 1. MS valid
    ms_valid = (p / "table.info").exists()

    # 2. Intents populated (check STATE subtable)
    #
    # An absent STATE subtable legitimately means "set_intents has not run".
    # A STATE subtable that exists but cannot be read (lock, permissions,
    # corruption) means the probe failed, and must not be reported as
    # "not populated" — that would drive next_recommended_step to re-run
    # set_intents over data that may already have intents.
    intents_populated: bool | None = False
    intents_error: str | None = None
    if (p / "STATE").exists():
        try:
            with open_table(ms_str + "/STATE") as tb:
                casa_calls.append("tb.open(STATE)")
                intents_populated = tb.nrows() > 0
        except Exception as exc:
            intents_populated = None
            intents_error = f"{type(exc).__name__}: {exc}"

    # 3. Online flags file present (heuristic: any .flagonline.txt near MS)
    online_flag_candidates = list(p.parent.glob("*.flagonline.txt"))
    online_flags_present = len(online_flag_candidates) > 0

    # 4. calibrators.ms present
    calibrators_ms = wd / "calibrators.ms"
    calibrators_ms_present = calibrators_ms.exists() and (calibrators_ms / "table.info").exists()

    # 5. priorcals present
    priorcals_tables = ["gain_curves.gc", "opacities.opac"]  # required
    priorcals_present = [t for t in priorcals_tables if (wd / t).exists()]

    # 6. initial bandpass present
    init_gain = wd / "init_gain.g"
    bp0 = wd / "BP0.b"
    initial_bandpass_present = init_gain.exists() and bp0.exists()

    # 7. CORRECTED populated
    #
    # Probed on a NAMED MS, not on the `ms_path` argument. The ladder reaches
    # this branch only after calibrators.ms exists, and the step it gates
    # (initial rflag + applycal) operates on calibrators.ms — so calibrators.ms
    # is what "corrected" means here.
    #
    # Probing `ms_path` instead made the answer depend on which MS the caller
    # happened to pass: the same workdir returned a different
    # next_recommended_step for the raw MS and for calibrators.ms, with nothing
    # in the output recording which one was read. Deriving the path from workdir
    # removes the caller from the decision entirely.
    #
    # The MAIN table always exists when the MS is valid, so there is no "has not
    # happened yet" case here: any exception is a genuine read failure and is
    # reported as such rather than as an absent column.
    corrected_populated: bool | None = False
    corrected_error: str | None = None
    corrected_probed_path: str | None = None
    if calibrators_ms_present:
        corrected_probed_path = str(calibrators_ms)
        try:
            with open_table(corrected_probed_path) as tb:
                casa_calls.append(f"tb.open({corrected_probed_path}) for colnames")
                corrected_populated = "CORRECTED_DATA" in set(tb.colnames())
        except Exception as exc:
            corrected_populated = None
            corrected_error = f"{type(exc).__name__}: {exc}"

    # 8. Final caltables present
    final_tables = ["delay.K", "bandpass.B", "gain.G", "gain.fluxscaled"]
    final_caltables_present = [t for t in final_tables if (wd / t).exists()]

    # 9. First image present (heuristic)
    first_image_present = (
        len(list(wd.glob("*.image.pbcor"))) > 0 or len(list(wd.glob("*.image"))) > 0
    )

    # Derive next_recommended_step.
    #
    # A failed probe stops the derivation at that point rather than falling
    # through to the next branch: below a failed probe every subsequent
    # answer would be inferred from an unknown.
    if not ms_valid:
        next_step = "import_asdm"
    elif intents_populated is None:
        next_step = "probe_failed_intents"
    elif not intents_populated:
        next_step = "set_intents"
    elif not calibrators_ms_present:
        next_step = "apply_preflag"
    elif len(priorcals_present) < 2:
        next_step = "generate_priorcals"
    elif not initial_bandpass_present:
        next_step = "initial_bandpass"
    elif corrected_populated is None:
        next_step = "probe_failed_corrected"
    elif not corrected_populated:
        next_step = "apply_initial_rflag_then_applycal"
    elif len(final_caltables_present) < 3:
        next_step = "delay_bandpass_gain"
    elif not first_image_present:
        next_step = "first_image"
    else:
        next_step = "selfcal_or_done"

    if intents_error is not None:
        warnings.append(f"STATE subtable exists but could not be read: {intents_error}")
    if corrected_error is not None:
        warnings.append(f"MAIN table could not be read for column names: {corrected_error}")

    data = {
        "ms_valid": field(ms_valid),
        "intents_populated": (
            field(None, "UNAVAILABLE", note=f"STATE read failed: {intents_error}")
            if intents_populated is None
            else field(intents_populated)
        ),
        "online_flags_present": field(online_flags_present),
        "calibrators_ms_present": field(calibrators_ms_present),
        "priorcals_present": priorcals_present,
        "initial_bandpass_present": field(initial_bandpass_present),
        "corrected_populated": (
            field(None, "UNAVAILABLE", note=f"MAIN colnames read failed: {corrected_error}")
            if corrected_populated is None
            else field(
                corrected_populated,
                note=(
                    f"probed {corrected_probed_path}"
                    if corrected_probed_path is not None
                    else "not probed — calibrators.ms does not exist yet"
                ),
            )
        ),
        # Which MS each MS-reading probe actually opened. Without this the two
        # probes below are indistinguishable from each other in the output, and
        # a wrong answer looks identical to a right one.
        "probed": {
            "intents_from": ms_str,
            "corrected_from": corrected_probed_path,
        },
        "final_caltables_present": final_caltables_present,
        "first_image_present": field(first_image_present),
        "workdir": str(wd),
        "next_recommended_step": next_step,
    }
    return response_envelope(
        tool_name=TOOL_NAME,
        ms_path=ms_path,
        data=data,
        warnings=warnings,
        casa_calls=casa_calls,
    )
