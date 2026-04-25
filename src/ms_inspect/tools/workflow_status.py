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
    intents_populated = False
    try:
        with open_table(ms_str + "/STATE") as tb:
            casa_calls.append("tb.open(STATE)")
            intents_populated = tb.nrows() > 0
    except Exception:
        pass

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

    # 7. CORRECTED populated (check MS main table column)
    corrected_populated = False
    try:
        with open_table(ms_str) as tb:
            casa_calls.append("tb.open(MAIN) for colnames")
            corrected_populated = "CORRECTED_DATA" in set(tb.colnames())
    except Exception:
        pass

    # 8. Final caltables present
    final_tables = ["delay.K", "bandpass.B", "gain.G", "gain.fluxscaled"]
    final_caltables_present = [t for t in final_tables if (wd / t).exists()]

    # 9. First image present (heuristic)
    first_image_present = (
        len(list(wd.glob("*.image.pbcor"))) > 0 or len(list(wd.glob("*.image"))) > 0
    )

    # Derive next_recommended_step
    if not ms_valid:
        next_step = "import_asdm"
    elif not intents_populated:
        next_step = "set_intents"
    elif not calibrators_ms_present:
        next_step = "apply_preflag"
    elif len(priorcals_present) < 2:
        next_step = "generate_priorcals"
    elif not initial_bandpass_present:
        next_step = "initial_bandpass"
    elif not corrected_populated:
        next_step = "apply_initial_rflag_then_applycal"
    elif len(final_caltables_present) < 3:
        next_step = "delay_bandpass_gain"
    elif not first_image_present:
        next_step = "first_image"
    else:
        next_step = "selfcal_or_done"

    data = {
        "ms_valid": field(ms_valid),
        "intents_populated": field(intents_populated),
        "online_flags_present": field(online_flags_present),
        "calibrators_ms_present": field(calibrators_ms_present),
        "priorcals_present": priorcals_present,
        "initial_bandpass_present": field(initial_bandpass_present),
        "corrected_populated": field(corrected_populated),
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
