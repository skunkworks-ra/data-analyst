"""
server.py — radio_ms_mcp FastMCP entry point

Registers all 12 Phase 1 tools (Layer 1 + Layer 2) with FastMCP.
Transport is selected via RADIO_MCP_TRANSPORT environment variable:

    RADIO_MCP_TRANSPORT=stdio  (default) — for Claude Desktop / local use
    RADIO_MCP_TRANSPORT=http             — for HPC / remote access
    RADIO_MCP_PORT=8000                  — HTTP port (default 8000)

All tools follow the contract defined in DESIGN.md §7:
- Return a standard JSON envelope (status, completeness_summary, data, warnings, provenance)
- Raise typed exceptions from exceptions.py on hard failures
- Never interpret, suggest, or chain — that is the Skill's job
"""

from __future__ import annotations

import json
import os
import sys

from mcp.server.fastmcp import FastMCP, Context
from pydantic import BaseModel, Field, ConfigDict

from ms_inspect import __version__
from ms_inspect.exceptions import RadioMSError
from ms_inspect.tools import (
    antennas,
    fields,
    flags,
    geometry,
    observation,
    scans,
    shadowing,
    spectral,
)

# ---------------------------------------------------------------------------
# Server initialisation
# ---------------------------------------------------------------------------
mcp = FastMCP(
    "radio_ms_mcp",
    instructions=(
        "Radio interferometric Measurement Set inspector (Phase 1 — Layer 1 & 2). "
        "Tools return structured measurements — no interpretation. "
        "Consult the SKILL.md document for interferometrist reasoning guidance. "
        f"Version: {__version__}"
    ),
)


# ---------------------------------------------------------------------------
# Input models
# ---------------------------------------------------------------------------

class MSPathInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    ms_path: str = Field(
        ...,
        description=(
            "Absolute path to the CASA Measurement Set directory. "
            "Example: '/data/obs/2017_VLA_Lband.ms'"
        ),
        min_length=1,
    )


class ElevationInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    ms_path: str = Field(..., description="Path to Measurement Set", min_length=1)
    threshold_deg: float = Field(
        default=20.0,
        description="Elevation warning threshold in degrees (default 20°)",
        ge=0.0,
        le=90.0,
    )


class ShadowingInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    ms_path: str = Field(..., description="Path to Measurement Set", min_length=1)
    tolerance_m: float = Field(
        default=0.0,
        description=(
            "Shadowing tolerance in metres. 0.0 = strict (any overlap counts). "
            "Positive values require the antenna to be shadowed by more than "
            "tolerance_m before it is reported."
        ),
        ge=0.0,
    )


class BaselineLengthInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    ms_path: str = Field(..., description="Path to Measurement Set", min_length=1)
    spw_centre_freqs_hz: list[float] | None = Field(
        default=None,
        description=(
            "Optional list of SpW centre frequencies in Hz for kλ / arcsec conversion. "
            "If not provided, frequencies are read from the MS spectral window table."
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
# Layer 1 — Orientation tools
# ---------------------------------------------------------------------------

@mcp.tool(
    name="ms_observation_info",
    annotations={
        "title": "Observation Info",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def ms_observation_info(params: MSPathInput) -> str:
    """
    Layer 1, Tool 1: Retrieve observation-level metadata.

    Returns telescope name, observer, project code, UTC time range,
    total duration in seconds, and HISTORY entry count.

    Raises INSUFFICIENT_METADATA if TELESCOPE_NAME is missing or unrecognised.

    Args:
        params.ms_path: Path to the Measurement Set.

    Returns:
        JSON envelope with fields: telescope_name, observer, project_code,
        obs_start_utc, obs_end_utc, total_duration_s, total_duration_human,
        history_entries. Each field carries a completeness flag.
    """
    return _run_tool(observation.run, params.ms_path)


@mcp.tool(
    name="ms_field_list",
    annotations={
        "title": "Field List",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def ms_field_list(params: MSPathInput) -> str:
    """
    Layer 1, Tool 2: List all observed fields with J2000 coordinates and calibration roles.

    Cross-matches field names against the bundled calibrator catalogue to identify
    flux calibrators, bandpass calibrators, and resolved sources.

    When scan intents are absent (<50% coverage), falls back to heuristic
    intent inference from field names. Inferred intents are tagged INFERRED.

    Args:
        params.ms_path: Path to the Measurement Set.

    Returns:
        JSON array of field records: field_id, name, ra/dec in deg and HMS/DMS,
        intents, calibrator_match, calibrator_role, flux_standard, resolved_source.
    """
    return _run_tool(fields.run, params.ms_path)


@mcp.tool(
    name="ms_scan_list",
    annotations={
        "title": "Scan List",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def ms_scan_list(params: MSPathInput) -> str:
    """
    Layer 1, Tool 3: Return the time-ordered list of all scans.

    Each scan record includes: scan number, field name, intents, start/end UTC,
    duration in seconds, integration time, and spectral window IDs.

    Warns on large scan number gaps (possible missing data).

    Args:
        params.ms_path: Path to the Measurement Set.

    Returns:
        JSON with n_scans, n_fields, and ordered array of scan records.
    """
    return _run_tool(scans.run_scan_list, params.ms_path)


@mcp.tool(
    name="ms_scan_intent_summary",
    annotations={
        "title": "Scan Intent Summary",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def ms_scan_intent_summary(params: MSPathInput) -> str:
    """
    Layer 1, Tool 4: Summarise how total observing time is distributed across intents.

    Returns total duration and per-intent fractions (CALIBRATE_FLUX, CALIBRATE_BANDPASS,
    CALIBRATE_PHASE, OBSERVE_TARGET, etc.).

    If intents are absent, groups by field name instead and warns.

    Args:
        params.ms_path: Path to the Measurement Set.

    Returns:
        JSON with total_duration_s, total_duration_human, n_intents, intent_completeness,
        and by_intent array of {intent, total_s, fraction, human}.
    """
    return _run_tool(scans.run_scan_intent_summary, params.ms_path)


@mcp.tool(
    name="ms_spectral_window_list",
    annotations={
        "title": "Spectral Window List",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def ms_spectral_window_list(params: MSPathInput) -> str:
    """
    Layer 1, Tool 5: Return frequency and channel structure for all spectral windows.

    For each SpW returns: centre frequency, bandwidth, channel count, channel width,
    frequency range, correlation products (XX/YY, RR/LL, etc.), and band name
    (e.g. 'L-band (1-2 GHz)' for VLA).

    Band name requires telescope name from OBSERVATION subtable.
    Warns on single-channel (frequency-averaged) SpWs and non-uniform channel widths.

    Args:
        params.ms_path: Path to the Measurement Set.

    Returns:
        JSON array of SpW records with completeness flags.
    """
    return _run_tool(spectral.run_spectral_window_list, params.ms_path)


@mcp.tool(
    name="ms_correlator_config",
    annotations={
        "title": "Correlator Configuration",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def ms_correlator_config(params: MSPathInput) -> str:
    """
    Layer 1, Tool 6: Return correlator dump time and polarization basis.

    Returns: dump_time_s (integration time per visibility), polarization_basis
    (circular/linear/stokes/mixed), full_stokes flag, and counts of fields/scans/SpWs.

    Args:
        params.ms_path: Path to the Measurement Set.

    Returns:
        JSON with dump_time_s, polarization_basis, correlation_products, full_stokes,
        n_pol_setups, n_fields, n_scans, n_spw.
    """
    return _run_tool(spectral.run_correlator_config, params.ms_path)


# ---------------------------------------------------------------------------
# Layer 2 — Instrument Sanity tools
# ---------------------------------------------------------------------------

@mcp.tool(
    name="ms_antenna_list",
    annotations={
        "title": "Antenna List",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def ms_antenna_list(params: MSPathInput) -> str:
    """
    Layer 2, Tool 1: Return antenna inventory with ECEF positions and completeness check.

    Returns per-antenna: name, station, ECEF XYZ position (metres), dish diameter,
    mount type. Also computes array geodetic centre and cross-checks antenna IDs
    against the MAIN table.

    Raises INSUFFICIENT_METADATA if antenna names are purely numeric (UVFITS artefact)
    or if antenna IDs in the MAIN table are absent from the ANTENNA subtable.

    Args:
        params.ms_path: Path to the Measurement Set.

    Returns:
        JSON with n_antennas, n_baselines_cross, array_centre coords, and antenna array.
    """
    return _run_tool(antennas.run_antenna_list, params.ms_path)


@mcp.tool(
    name="ms_baseline_lengths",
    annotations={
        "title": "Baseline Lengths",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def ms_baseline_lengths(params: BaselineLengthInput) -> str:
    """
    Layer 2, Tool 2: Compute physical baseline length statistics and derived angular scales.

    Lengths are from ANTENNA ECEF positions (maximum possible baselines).
    UV coverage from actual projected baselines is a Layer 3 tool.

    Returns: min/max/median/mean baseline in metres, shortest/longest baseline antenna
    pairs, and per-SpW derived quantities: max baseline in kλ, synthesised beam
    resolution (arcsec, θ ≈ λ/B_max), and largest angular scale (arcsec, θ ≈ λ/B_min).

    Args:
        params.ms_path:              Path to the Measurement Set.
        params.spw_centre_freqs_hz:  Optional list of SpW frequencies in Hz.

    Returns:
        JSON with baseline statistics and per_spw_derived array.
    """
    return _run_tool(antennas.run_baseline_lengths, params.ms_path, params.spw_centre_freqs_hz)


@mcp.tool(
    name="ms_elevation_vs_time",
    annotations={
        "title": "Elevation vs Time",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def ms_elevation_vs_time(params: ElevationInput) -> str:
    """
    Layer 2, Tool 3: Return per-scan elevation statistics for each field.

    Computed using astropy AltAz frame from field J2000 coordinates,
    array geodetic position (from ANTENNA ECEF mean), and scan time ranges.

    Warns on any scan where elevation drops below threshold_deg (default 20°).
    Fields with invalid coordinates (§3.4) return UNAVAILABLE for elevation.

    Args:
        params.ms_path:       Path to the Measurement Set.
        params.threshold_deg: Low-elevation warning threshold (default 20°).

    Returns:
        JSON with per-field scan elevation records: el_start, el_mid, el_end,
        el_min, below_threshold flag.
    """
    return _run_tool(geometry.run_elevation_vs_time, params.ms_path, params.threshold_deg)


@mcp.tool(
    name="ms_parallactic_angle_vs_time",
    annotations={
        "title": "Parallactic Angle vs Time",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def ms_parallactic_angle_vs_time(params: MSPathInput) -> str:
    """
    Layer 2, Tool 4: Return parallactic angle range per field (sky-frame and feed-frame).

    CONVENTION (DESIGN.md §6.4):
    - pa_sky_deg:  astropy sky-frame PA, North through East
    - pa_feed_deg: feed-frame PA = pa_sky - 90° for ALT-AZ mounts (CASA convention)

    Both are returned. VALIDATION STATUS: PENDING — cross-check against
    casatools.measures for a reference VLA observation is required before
    using pa_feed for D-term calibration.

    For equatorial mounts (WSRT etc.), PA is constant — flagged accordingly.

    Args:
        params.ms_path: Path to the Measurement Set.

    Returns:
        JSON with per-field pa_sky_start/end/range, pa_feed_start/end/range,
        convention_offset_deg, convention_note, validation_status.
    """
    return _run_tool(geometry.run_parallactic_angle_vs_time, params.ms_path)


@mcp.tool(
    name="ms_shadowing_report",
    annotations={
        "title": "Shadowing Report",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def ms_shadowing_report(params: ShadowingInput) -> str:
    """
    Layer 2, Tool 5: Detect antenna shadowing events.

    Uses msmd.shadowedAntennas() (CASA 6.x). Falls back to reporting only
    FLAG_CMD entries if the method is unavailable.

    Also checks FLAG_CMD subtable for pre-existing online shadow flags.

    Args:
        params.ms_path:     Path to the Measurement Set.
        params.tolerance_m: Shadowing tolerance in metres (default 0.0).

    Returns:
        JSON with shadowing_detected, n_shadow_events, total_shadowed_seconds,
        method (with completeness flag), shadowed_events array, and
        flag_cmd_shadow_entries.
    """
    return _run_tool(shadowing.run, params.ms_path, params.tolerance_m)


@mcp.tool(
    name="ms_antenna_flag_fraction",
    annotations={
        "title": "Antenna Flag Fraction",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def ms_antenna_flag_fraction(params: MSPathInput) -> str:
    """
    Layer 2, Tool 6: Compute pre-existing flag fractions per antenna.

    Reads the FLAG column using parallel multiprocessing (N workers, default 4,
    configurable via RADIO_MCP_WORKERS env var). Autocorrelations excluded.

    Also reports online flag command counts from FLAG_CMD subtable.

    Note: This tool can be slow for large MSs (> 50 GB). Progress is reported
    via MCP context if available.

    Args:
        params.ms_path: Path to the Measurement Set.

    Returns:
        JSON with overall_flag_fraction, autocorrelations_excluded, n_workers_used,
        and per_antenna array of {antenna_id, name, flag_fraction, n_flagged_elements,
        n_total_elements, n_flag_commands_online}.
    """
    return _run_tool(flags.run, params.ms_path)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    transport = os.environ.get("RADIO_MCP_TRANSPORT", "stdio").lower()
    port = int(os.environ.get("RADIO_MCP_PORT", "8000"))

    if transport == "http":
        mcp.run(transport="streamable-http", host="127.0.0.1", port=port)
    else:
        # stdio — default, for Claude Desktop and local use
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
