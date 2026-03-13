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
from ms_modify import __version__, intents

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
