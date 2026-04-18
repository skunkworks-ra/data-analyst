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

from ms_create import __version__, import_asdm
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


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
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
