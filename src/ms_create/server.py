"""
server.py — ms_create FastMCP entry point

Registers data ingestion tools for CASA Measurement Sets.
Transport is selected via RADIO_MCP_TRANSPORT environment variable.

All tools carry readOnlyHint: False — they create new files on disk.
"""

from __future__ import annotations

import json
import os

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from ms_create import __version__, import_asdm, reduction_log, sdm_summary
from ms_inspect.exceptions import RadioMSError

# ---------------------------------------------------------------------------
# Server initialisation
# ---------------------------------------------------------------------------
mcp = FastMCP(
    "radio_ms_create",
    instructions=(
        "Radio interferometric Measurement Set ingestion utilities. "
        "Tools in this server create new files on disk. "
        f"Version: {__version__}"
    ),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _run_tool(tool_fn, *args, **kwargs) -> str:
    try:
        result = tool_fn(*args, **kwargs)
        return json.dumps(result, indent=2)
    except RadioMSError as exc:
        return json.dumps(
            {
                "status": "error",
                "error_type": exc.error_type,
                "message": str(exc),
            },
            indent=2,
        )
    except Exception as exc:
        return json.dumps(
            {
                "status": "error",
                "error_type": "UNEXPECTED_ERROR",
                "message": str(exc),
            },
            indent=2,
        )


# ---------------------------------------------------------------------------
# Input models
# ---------------------------------------------------------------------------
class ImportASDMInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asdm_path: str = Field(..., description="Path to the raw ASDM directory.")
    workdir: str = Field(..., description="Existing output directory.")
    ms_name: str = Field(
        default="",
        description="Output MS filename. Defaults to <asdm_stem>.ms if empty.",
    )
    with_pointing_correction: bool = Field(
        default=False,
        description=(
            "Apply pointing correction during import. "
            "Significantly increases import time on large datasets. "
            "Default False; set True only if your science requires it."
        ),
    )
    execute: bool = Field(
        default=False,
        description=(
            "If False (default), write import_asdm.py to workdir and return. "
            "If True, run importasdm in-process."
        ),
    )


class ReductionLogInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str = Field(..., description="'append', 'render', or 'list'.")
    workdir: str = Field(..., description="Directory holding reduction_log.jsonl.")
    tool: str = Field(default="", description="(append) tool/call name that worked.")
    params: dict | None = Field(default=None, description="(append) exact working parameters.")
    outputs: dict | None = Field(default=None, description="(append) salient outputs to record.")
    rationale: str = Field(default="", description="(append) why this step was done.")
    skill_rule: str = Field(default="", description="(append) skill file / threshold cited.")
    status: str = Field(
        default="ok", description="(append) outcome tag; shuttle only working calls."
    )


class SDMSummaryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sdm_path: str = Field(
        ...,
        description=(
            "Path to the SDM directory (contains ASDM.xml) or a wrapper "
            "directory containing exactly one SDM."
        ),
    )


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
@mcp.tool(
    name="ms_sdm_summary",
    description=(
        "Inspect a raw ASDM/SDM directory BEFORE conversion: telescope, array "
        "configuration, band, per-SPW spectral setup with continuum-vs-line "
        "classification, HI-21cm coverage, correlation products, sources with "
        "coordinates and intents, scan-intent balance, time span, and max target "
        "elevation (VLA geometry). Read-only — parses ASDM XML only, touches no "
        "binary data and requires no casatools. Use this to decide what a dataset "
        "is and whether to convert it with ms_import_asdm."
    ),
    annotations={"readOnlyHint": True, "destructiveHint": False},
)
async def ms_sdm_summary(params: SDMSummaryInput) -> str:
    """
    Summarise a raw ASDM/SDM directory prior to conversion.

    Args:
        params.sdm_path: SDM directory or a wrapper containing one SDM.

    Returns:
        JSON envelope with telescope/config/band, spectral_windows,
        spectral_mode_inferred, fields, scan_intent_counts, and
        target_max_elevation_deg.
    """
    return _run_tool(sdm_summary.run, params.sdm_path)


@mcp.tool(
    name="ms_reduction_log",
    description=(
        "Working-calls ledger: shuttle KNOWN-GOOD calls into a per-reduction "
        "JSONL recipe as you go. action='append' records one validated call "
        "(tool, exact params, outputs, rationale, skill rule); 'render' emits the "
        "ordered recipe + a replay script; 'list' gives a compact step summary. "
        "Only shuttle calls that actually worked — failures stay out."
    ),
    annotations={"readOnlyHint": False, "destructiveHint": False},
)
async def ms_reduction_log(params: ReductionLogInput) -> str:
    """
    Append to / render / list the reduction working-calls ledger.

    Args:
        params.action:     'append', 'render', or 'list'.
        params.workdir:    Directory holding reduction_log.jsonl.
        params.tool:       (append) tool/call name that worked.
        params.params:     (append) exact working parameters.
        params.outputs:    (append) salient outputs to record.
        params.rationale:  (append) why this step was done.
        params.skill_rule: (append) skill file / threshold cited.
        params.status:     (append) outcome tag.

    Returns:
        JSON envelope: append → n_records; render → recipe + replay_script;
        list → step/tool/rationale summary.
    """
    return _run_tool(
        reduction_log.run,
        params.action,
        params.workdir,
        params.tool,
        params.params,
        params.outputs,
        params.rationale,
        params.skill_rule,
        params.status,
    )


@mcp.tool(
    name="ms_import_asdm",
    description=(
        "Convert a raw ASDM directory to a CASA Measurement Set. "
        "Cross-correlations only (ocorr_mode='co'). "
        "Online flags are saved to <ms_name>.flagonline.txt but NOT applied — "
        "pass online_flag_file to ms_apply_preflag to apply them in the "
        "pre-calibration flagging pass. "
        "By default writes import_asdm.py to workdir; set execute=True to run in-process."
    ),
    annotations={"readOnlyHint": False, "destructiveHint": False},
)
async def ms_import_asdm(params: ImportASDMInput) -> str:
    """
    Convert a raw ASDM to a CASA Measurement Set.

    Fixed parameters (not exposed):
      ocorr_mode='co'   — cross-correlations only
      savecmds=True     — write .flagonline.txt
      applyflags=False  — flags NOT applied during import

    Args:
        params.asdm_path:               Path to the ASDM directory.
        params.workdir:                 Existing output directory.
        params.ms_name:                 Output MS name (default: <asdm_stem>.ms).
        params.with_pointing_correction: Apply pointing correction (default False).
        params.execute:                 Generate script only (False) or run (True).

    Returns:
        JSON with script_path, ms_path, online_flag_file, and fixed parameters used.
    """
    return _run_tool(
        import_asdm.run,
        params.asdm_path,
        params.workdir,
        params.ms_name,
        params.with_pointing_correction,
        params.execute,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    transport = os.environ.get("RADIO_MCP_TRANSPORT", "stdio").lower()
    port = int(os.environ.get("RADIO_MCP_PORT", "8002"))

    if transport == "http":
        mcp.run(transport="streamable-http", host="127.0.0.1", port=port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
