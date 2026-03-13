"""
tools/apply_flags.py — ms_apply_flags

Layer 3b, Tool 3.

Applies a list of CASA flagdata command strings to the MS.

SAFETY CONTRACT:
  1. dry_run=True (default) — computes what would be flagged, touches nothing.
  2. dry_run=False — saves a flagmanager backup FIRST, then applies.
     If the backup fails, the operation is aborted (FlagBackupFailedError).
     The backup name is returned so the user can restore with:
       flagmanager(vis=..., mode='restore', versionname=<backup_name>)

WRITE OPERATION — this is the only Phase 1–3 tool that modifies the MS.

CASA access:
  dry_run=True:
    casatasks.flagdata(vis=..., mode='list', inpfile=[...], action='calculate')
  dry_run=False:
    casatasks.flagmanager(vis=..., mode='save', versionname=...)
    casatasks.flagdata(vis=..., mode='list', inpfile=[...])
    casatasks.flagdata(vis=..., mode='summary')  — post-apply audit
"""

from __future__ import annotations

import datetime

from ms_inspect.exceptions import FlagBackupFailedError
from ms_inspect.util.casa_context import _require_casatasks, validate_ms_path
from ms_inspect.util.formatting import field, response_envelope

TOOL_NAME = "ms_apply_flags"


def run(
    ms_path: str,
    flag_commands: list[str],
    dry_run: bool = True,
    backup_name: str | None = None,
) -> dict:
    """
    Apply a list of CASA flagdata command strings to the MS.

    Args:
        ms_path:        Path to Measurement Set.
        flag_commands:  List of flagdata command strings, e.g.:
                          ["mode='manual' antenna='ea06'",
                           "mode='manual' spw='0:32~40'",
                           "mode='shadow'"]
        dry_run:        If True (default), calculate what would be flagged
                        without modifying the MS. Set to False to apply.
        backup_name:    Flag backup version name for flagmanager.
                        Auto-generated from timestamp if None.
                        Only used when dry_run=False.

    Returns:
        Standard envelope with flag change statistics.
        When dry_run=True: post_flag_fraction and backup_name are null.
        When dry_run=False: backup saved, flags applied, post_flag_fraction reported.
    """
    p = validate_ms_path(ms_path)
    ms_str = str(p)
    casa_calls: list[str] = []
    warnings: list[str] = []

    if not flag_commands:
        warnings.append("Empty flag_commands list — nothing to apply.")
        return response_envelope(
            tool_name=TOOL_NAME,
            ms_path=ms_path,
            data={
                "dry_run": dry_run,
                "n_commands": 0,
                "commands": [],
                "pre_flag_fraction": field(None, "UNAVAILABLE"),
                "post_flag_fraction": field(None, "UNAVAILABLE"),
                "delta_flag_fraction": field(None, "UNAVAILABLE"),
                "backup_name": None,
            },
            warnings=warnings,
            casa_calls=casa_calls,
        )

    # ------------------------------------------------------------------
    casatasks = _require_casatasks()

    # Pre-flag summary (always — establishes baseline)
    # ------------------------------------------------------------------
    casa_calls.append("casatasks.flagdata(vis=..., mode='summary') [pre-apply]")
    try:
        pre_summary = casatasks.flagdata(vis=ms_str, mode="summary")
        pre_total = pre_summary.get("total", {})
        pre_flagged = int(pre_total.get("flagged", 0))
        pre_count = int(pre_total.get("total", 0))
        pre_frac = pre_flagged / pre_count if pre_count > 0 else 0.0
        pre_frac_field = field(round(pre_frac, 6))
    except Exception as e:
        warnings.append(f"Pre-apply flag summary failed: {e}")
        pre_frac_field = field(None, "UNAVAILABLE")
        pre_frac = 0.0

    # ------------------------------------------------------------------
    # DRY RUN — calculate without modifying
    # ------------------------------------------------------------------
    if dry_run:
        casa_calls.append(
            "casatasks.flagdata(vis=..., mode='list', inpfile=[...], action='calculate')"
        )
        try:
            calc_result = casatasks.flagdata(
                vis=ms_str,
                mode="list",
                inpfile=flag_commands,
                action="calculate",
            )
            # flagdata returns a dict; the 'summary' key contains stats
            calc_summary = calc_result if isinstance(calc_result, dict) else {}
            calc_total = calc_summary.get("total", {})
            would_flag = int(calc_total.get("flagged", 0))
            would_total = int(calc_total.get("total", 0))
            would_frac = would_flag / would_total if would_total > 0 else 0.0
        except Exception as e:
            warnings.append(f"Dry-run calculation failed: {e}. Cannot estimate flag delta.")
            would_frac = 0.0

        data = {
            "dry_run": True,
            "n_commands": len(flag_commands),
            "commands": flag_commands,
            "pre_flag_fraction": pre_frac_field,
            "would_flag_fraction": field(
                round(would_frac, 6), note="Estimated post-flag fraction if applied"
            ),
            "delta_flag_fraction": field(
                round(would_frac - pre_frac, 6), note="Additional fraction that would be flagged"
            ),
            "post_flag_fraction": field(None, "UNAVAILABLE", note="Not computed in dry_run mode"),
            "backup_name": None,
            "restore_command": None,
        }
        return response_envelope(
            tool_name=TOOL_NAME,
            ms_path=ms_path,
            data=data,
            warnings=warnings,
            casa_calls=casa_calls,
        )

    # ------------------------------------------------------------------
    # LIVE RUN — backup first, then apply
    # ------------------------------------------------------------------

    # Generate backup name if not provided
    if backup_name is None:
        ts = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%S")
        backup_name = f"ms_inspect_pre_apply_{ts}"

    # Step 1: Save flag backup — ABORT if this fails
    casa_calls.append(f"casatasks.flagmanager(vis=..., mode='save', versionname='{backup_name}')")
    try:
        casatasks.flagmanager(vis=ms_str, mode="save", versionname=backup_name)
    except Exception as e:
        raise FlagBackupFailedError(
            f"flagmanager failed to save backup '{backup_name}': {e}\n"
            f"Flag application ABORTED — no changes made to the MS.\n"
            f"Check that the MS is not locked by another process.",
            ms_path=ms_path,
        ) from e

    # Step 2: Apply flags
    casa_calls.append(
        f"casatasks.flagdata(vis=..., mode='list', inpfile=[{len(flag_commands)} commands])"
    )
    try:
        casatasks.flagdata(vis=ms_str, mode="list", inpfile=flag_commands)
    except Exception as e:
        warnings.append(
            f"flagdata application raised: {e}. "
            f"Partial flags may have been applied. "
            f"Restore backup with: "
            f"flagmanager(vis='{ms_str}', mode='restore', versionname='{backup_name}')"
        )

    # Step 3: Post-apply summary
    casa_calls.append("casatasks.flagdata(vis=..., mode='summary') [post-apply]")
    try:
        post_summary = casatasks.flagdata(vis=ms_str, mode="summary")
        post_total = post_summary.get("total", {})
        post_flagged = int(post_total.get("flagged", 0))
        post_count = int(post_total.get("total", 0))
        post_frac = post_flagged / post_count if post_count > 0 else 0.0
        post_frac_field = field(round(post_frac, 6))
        delta_frac_field = field(
            round(post_frac - pre_frac, 6), note="Additional fraction flagged by this operation"
        )
    except Exception as e:
        warnings.append(f"Post-apply flag summary failed: {e}")
        post_frac_field = field(None, "UNAVAILABLE")
        delta_frac_field = field(None, "UNAVAILABLE")

    restore_cmd = (
        f"casatasks.flagmanager(vis='{ms_str}', mode='restore', versionname='{backup_name}')"
    )

    data = {
        "dry_run": False,
        "n_commands": len(flag_commands),
        "commands": flag_commands,
        "pre_flag_fraction": pre_frac_field,
        "post_flag_fraction": post_frac_field,
        "delta_flag_fraction": delta_frac_field,
        "backup_name": backup_name,
        "restore_command": restore_cmd,
    }

    return response_envelope(
        tool_name=TOOL_NAME,
        ms_path=ms_path,
        data=data,
        warnings=warnings,
        casa_calls=casa_calls,
    )
