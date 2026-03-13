"""
server.py — ms_modify FastMCP entry point

Registers write/modification tools for CASA Measurement Sets.
Transport is selected via RADIO_MCP_TRANSPORT environment variable.

All tools carry readOnlyHint: False — they modify the MS.
"""

from __future__ import annotations

import json
import os

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from ms_inspect.exceptions import RadioMSError
from ms_modify import __version__, initial_bandpass, intents

# ---------------------------------------------------------------------------
# Server initialisation
# ---------------------------------------------------------------------------
mcp = FastMCP(
    "radio_ms_modify",
    instructions=(
        "Radio interferometric Measurement Set modification utilities. "
        "Tools in this server write to the MS — use with care. "
        f"Version: {__version__}"
    ),
)


# ---------------------------------------------------------------------------
# Input models
# ---------------------------------------------------------------------------

class SetIntentsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    ms_path: str = Field(
        ...,
        description=(
            "Absolute path to the CASA Measurement Set directory. "
            "Example: '/data/obs/2017_VLA_Lband.ms'"
        ),
        min_length=1,
    )
    dry_run: bool = Field(
        default=False,
        description=(
            "If true, compute and return the intent mapping without writing "
            "anything to the MS. Use this to preview before committing."
        ),
    )


class InitialBandpassInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    ms_path: str = Field(..., description="Path to cal_only.ms.", min_length=1)
    bp_field: str = Field(
        ...,
        description="CASA field selection string for the bandpass calibrator (e.g. '3C147').",
        min_length=1,
    )
    ref_ant: str = Field(
        ...,
        description="Reference antenna name from ms_refant output (e.g. 'ea17').",
        min_length=1,
    )
    workdir: str = Field(
        ...,
        description="Existing directory to write caltables into.",
        min_length=1,
    )
    bp_scan: str = Field(
        default="",
        description="CASA scan selection string (empty = all scans).",
    )
    all_spw: str = Field(
        default="",
        description="CASA SpW selection string (empty = all SpWs).",
    )
    priorcals: list[str] = Field(
        default_factory=list,
        description="Prior calibration tables to pre-apply (e.g. requantiser, Tsys).",
    )
    min_bl_per_ant: int = Field(
        default=4,
        description="minblperant for gaincal and bandpass (default 4).",
        ge=1,
    )
    uvrange: str = Field(
        default="",
        description=(
            "UV range restriction (e.g. '>1klambda'). "
            "Set for 3C84 to exclude extended emission."
        ),
    )


# ---------------------------------------------------------------------------
# Tool error handling wrapper
# ---------------------------------------------------------------------------

def _run_tool(tool_fn, *args, **kwargs) -> str:
    """
    Execute a tool function and return JSON-encoded result.
    Catches RadioMSError and returns a well-formed error envelope.
    Unexpected exceptions are re-raised (let FastMCP handle them).
    """
    try:
        result = tool_fn(*args, **kwargs)
        return json.dumps(result, indent=2, default=str)
    except RadioMSError as e:
        return json.dumps(e.to_dict(), indent=2)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool(
    name="ms_set_intents",
    annotations={
        "title": "Set Intents",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def ms_set_intents(params: SetIntentsInput) -> str:
    """
    Populate scan intent metadata in a Measurement Set that lacks intents.

    Matches field names against the bundled calibrator catalogue and the VLA
    calibrator database (positional cross-match) to assign intents:
    - Primary catalogue match → CALIBRATE_FLUX / CALIBRATE_BANDPASS
    - VLA calibrator positional match → CALIBRATE_PHASE
    - No match → OBSERVE_TARGET

    Writes the STATE subtable (OBS_MODE, CAL, SIG, SUB_SCAN, FLAG_ROW, REF)
    and updates the STATE_ID column in the MAIN table.

    Raises INTENTS_ALREADY_POPULATED if ≥50% of fields already have intents.

    Use dry_run=true to preview the mapping without writing.

    Args:
        params.ms_path: Path to the Measurement Set.
        params.dry_run: If true, preview only — no writes.

    Returns:
        JSON envelope with field_intent_map, n_unique_states,
        state_rows_written, main_rows_updated, dry_run flag.
    """
    return _run_tool(intents.set_intents, params.ms_path, dry_run=params.dry_run)


@mcp.tool(
    name="ms_initial_bandpass",
    annotations={
        "title": "Initial Bandpass Calibration",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def ms_initial_bandpass(params: InitialBandpassInput) -> str:
    """
    Solve an initial coarse bandpass on a calibrator MS and populate CORRECTED.

    Three-step sequence (adapted from evla_pipe/stages/initial_bp.py):
      1. gaincal(solint='int', calmode='p') → workdir/init_gain.g
      2. bandpass(solint='inf', combine='scan', fillgaps=62) → workdir/BP0.b
      3. applycal(all fields, calwt=False) → CORRECTED column populated

    Hard fails (INITIAL_BANDPASS_FAILED) if either caltable is not produced.
    After this tool completes, rflag can be run on the CORRECTED column.

    Args:
        params.ms_path:        Path to cal_only.ms.
        params.bp_field:       Bandpass calibrator field selection.
        params.ref_ant:        Reference antenna (from ms_refant).
        params.workdir:        Existing directory for caltable output.
        params.bp_scan:        Scan selection (default: all).
        params.all_spw:        SpW selection (default: all).
        params.priorcals:      Prior caltables to pre-apply.
        params.min_bl_per_ant: minblperant (default 4).
        params.uvrange:        UV range restriction for extended calibrators.

    Returns:
        JSON with init_gain_table, bp_table, corrected_written, ref_ant,
        bp_field, solint_phase, solint_bp, fillgaps.
    """
    return _run_tool(
        initial_bandpass.run,
        params.ms_path,
        params.bp_field,
        params.ref_ant,
        params.workdir,
        params.bp_scan,
        params.all_spw,
        params.priorcals,
        params.min_bl_per_ant,
        params.uvrange,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    transport = os.environ.get("RADIO_MCP_TRANSPORT", "stdio").lower()
    port = int(os.environ.get("RADIO_MCP_PORT", "8001"))

    if transport == "http":
        mcp.run(transport="streamable-http", host="127.0.0.1", port=port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
