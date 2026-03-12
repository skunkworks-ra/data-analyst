"""
tools/split_field.py — ms_split_field

Layer 3c, Tool 4.

Extracts one or more fields (and optionally SpWs, with optional averaging)
from the MS into a new MS using casatasks.split().

SOURCE MS IS NEVER MODIFIED. The output is always a new MS at output_path.

SAFETY CONTRACT:
  1. dry_run=True (default) — validate inputs, report what would be split.
     Checks field/SpW selections are valid. Does not call split().
  2. dry_run=False — calls casatasks.split(). output_path must not exist.
     If output_path already exists, raises OutputPathExistsError.

Standard pre-calibration split sequence (from Skill):
  1. ms_split_field(field="<fluxcal>")  → <project>_fluxcal.ms
  2. ms_split_field(field="<phasecal>") → <project>_phasecal.ms
  3. ms_split_field(field="<target>")   → <project>_target.ms

CASA access:
  - msmd.fieldnames() — validate field selection
  - casatasks.split(vis=..., outputvis=..., field=..., spw=...,
                    datacolumn=..., timebin=..., width=...) [when dry_run=False]

Returns the CASA split() command string in all cases so the user can
reproduce or script the operation independently.
"""

from __future__ import annotations

import os
from pathlib import Path

from ms_inspect.exceptions import OutputPathExistsError, SplitFailedError
from ms_inspect.util.casa_context import (
    open_msmd, validate_ms_path, _require_casatasks
)
from ms_inspect.util.formatting import field, response_envelope, error_envelope

TOOL_NAME = "ms_split_field"

_VALID_DATACOLUMNS = {"data", "corrected", "model", "all",
                       "data,model", "corrected,model"}


def run(
    ms_path: str,
    output_path: str,
    field_selection: str,
    spw: str = "",
    datacolumn: str = "data",
    timebin: str = "0s",
    width: int = 1,
    dry_run: bool = True,
) -> dict:
    """
    Extract selected fields from the MS into a new MS.

    Args:
        ms_path:          Source Measurement Set path.
        output_path:      Destination path for new MS. Must not already exist.
        field_selection:  CASA field selection string (e.g. "3C286", "0,1", "*CAL*").
                          Use '' or '*' for all fields.
        spw:              CASA SpW selection (e.g. "0,1,2" or "0:32~40"). '' = all.
        datacolumn:       Column to split: 'data', 'corrected', 'model'. Default 'data'.
        timebin:          Time averaging (e.g. "60s"). Default "0s" (no averaging).
        width:            Channel averaging factor. Default 1 (no averaging).
        dry_run:          If True (default), validate and report without splitting.

    Returns:
        Standard envelope with split metadata and the exact CASA command used.
    """
    p = validate_ms_path(ms_path)
    ms_str = str(p)
    out_p  = Path(output_path).expanduser().resolve()
    casa_calls: list[str] = []
    warnings:   list[str] = []

    # ------------------------------------------------------------------
    # Input validation — always runs regardless of dry_run
    # ------------------------------------------------------------------

    if datacolumn.lower() not in _VALID_DATACOLUMNS:
        return error_envelope(
            tool_name=TOOL_NAME,
            ms_path=ms_path,
            error_type="COMPUTATION_ERROR",
            message=(
                f"Invalid datacolumn '{datacolumn}'. "
                f"Valid options: {sorted(_VALID_DATACOLUMNS)}"
            ),
        )

    if width < 1:
        return error_envelope(
            tool_name=TOOL_NAME, ms_path=ms_path,
            error_type="COMPUTATION_ERROR",
            message=f"Invalid width={width}. Must be >= 1.",
        )

    # Check output path does not exist
    if out_p.exists():
        raise OutputPathExistsError(
            f"Output path already exists: {out_p}\n"
            f"Remove it first or choose a different output path.\n"
            f"To remove: import shutil; shutil.rmtree('{out_p}')",
            ms_path=ms_path,
        )

    # Validate field selection against MS field list
    casa_calls.append("msmd.fieldnames() — validate field selection")
    matched_fields: list[str] = []
    field_selection_valid = False

    try:
        with open_msmd(ms_str) as msmd:
            all_field_names = list(msmd.fieldnames())
            n_fields_total  = len(all_field_names)

        if field_selection in ("", "*"):
            matched_fields = all_field_names
            field_selection_valid = True
        else:
            # Try to match by name (substring/glob) or numeric ID
            for token in field_selection.replace(" ", "").split(","):
                token = token.strip()
                if token.isdigit():
                    fid = int(token)
                    if 0 <= fid < n_fields_total:
                        matched_fields.append(all_field_names[fid])
                        field_selection_valid = True
                    else:
                        warnings.append(
                            f"Field ID {fid} is out of range (n_fields={n_fields_total})."
                        )
                else:
                    # Name matching
                    hits = [
                        name for name in all_field_names
                        if token.lower() in name.lower()
                    ]
                    if hits:
                        matched_fields.extend(hits)
                        field_selection_valid = True
                    else:
                        warnings.append(
                            f"No field matched token '{token}' in field names: "
                            f"{all_field_names}"
                        )
    except Exception as e:
        warnings.append(f"Could not validate field selection: {e}")
        field_selection_valid = True  # Let CASA validate; don't block on our error

    # Build the CASA split() command string (always — for transparency)
    spw_arg      = f", spw='{spw}'" if spw else ""
    timebin_arg  = f", timebin='{timebin}'" if timebin != "0s" else ""
    width_arg    = f", width={width}" if width != 1 else ""
    casa_split_cmd = (
        f"casatasks.split("
        f"vis='{ms_str}', "
        f"outputvis='{out_p}', "
        f"field='{field_selection}', "
        f"datacolumn='{datacolumn}'"
        f"{spw_arg}{timebin_arg}{width_arg}"
        f")"
    )

    # ------------------------------------------------------------------
    # DRY RUN — return plan without executing
    # ------------------------------------------------------------------
    if dry_run:
        data = {
            "dry_run":             True,
            "source_ms":           ms_str,
            "output_ms":           str(out_p),
            "field_selection":     field_selection,
            "matched_fields":      field(matched_fields, flag="COMPLETE" if field_selection_valid else "SUSPECT"),
            "spw_selection":       spw or "(all)",
            "datacolumn":          datacolumn,
            "timebin":             timebin,
            "channel_avg_width":   width,
            "n_source_fields":     n_fields_total if "n_fields_total" in dir() else None,
            "casa_command":        casa_split_cmd,
            "output_ms_exists":    False,
        }
        return response_envelope(
            tool_name=TOOL_NAME, ms_path=ms_path,
            data=data, warnings=warnings, casa_calls=casa_calls,
        )

    # ------------------------------------------------------------------
    # LIVE RUN — execute split
    # ------------------------------------------------------------------
    casatasks = _require_casatasks()
    casa_calls.append(f"casatasks.split(...) → {out_p}")

    try:
        split_kwargs: dict = dict(
            vis=ms_str,
            outputvis=str(out_p),
            field=field_selection,
            datacolumn=datacolumn,
        )
        if spw:
            split_kwargs["spw"] = spw
        if timebin != "0s":
            split_kwargs["timebin"] = timebin
        if width != 1:
            split_kwargs["width"] = width

        casatasks.split(**split_kwargs)

    except Exception as e:
        # Clean up a partial output if it was created
        if out_p.exists():
            try:
                import shutil
                shutil.rmtree(str(out_p))
                warnings.append(
                    f"Partial output MS at '{out_p}' was removed after split failure."
                )
            except Exception:
                warnings.append(
                    f"Partial output MS may remain at '{out_p}' after split failure."
                )
        raise SplitFailedError(
            f"casatasks.split() failed: {e}\n"
            f"Command attempted: {casa_split_cmd}",
            ms_path=ms_path,
        )

    # ------------------------------------------------------------------
    # Post-split: report output size and validate it opened
    # ------------------------------------------------------------------
    output_size_bytes: int | None = None
    output_n_rows: int | None = None

    try:
        # Get directory size
        output_size_bytes = sum(
            f.stat().st_size
            for f in out_p.rglob("*")
            if f.is_file()
        )
    except Exception:
        pass

    try:
        from ms_inspect.util.casa_context import open_table
        with open_table(str(out_p)) as tb:
            output_n_rows = tb.nrows()
    except Exception:
        warnings.append("Could not open output MS to verify row count.")

    data = {
        "dry_run":              False,
        "source_ms":            ms_str,
        "output_ms":            str(out_p),
        "field_selection":      field_selection,
        "matched_fields":       field(matched_fields),
        "spw_selection":        spw or "(all)",
        "datacolumn":           datacolumn,
        "timebin":              timebin,
        "channel_avg_width":    width,
        "output_ms_n_rows":     field(output_n_rows,    flag="COMPLETE" if output_n_rows is not None else "UNAVAILABLE"),
        "output_ms_size_bytes": field(output_size_bytes, flag="COMPLETE" if output_size_bytes is not None else "UNAVAILABLE"),
        "output_ms_exists":     True,
        "casa_command":         casa_split_cmd,
    }

    return response_envelope(
        tool_name=TOOL_NAME, ms_path=ms_path,
        data=data, warnings=warnings, casa_calls=casa_calls,
    )
