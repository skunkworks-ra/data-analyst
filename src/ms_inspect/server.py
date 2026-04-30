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

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from ms_inspect import __version__
from ms_inspect.exceptions import RadioMSError
from ms_inspect.tools import (
    antennas,
    calsol_plot,
    calsol_plot_library,
    calsol_stats,
    caltables,
    fields,
    flag_summary,
    flags,
    geometry,
    image_stats,
    observation,
    online_flags,
    pol_cal_feasibility,
    priorcals_check,
    refant,
    residual_stats,
    rfi,
    scans,
    shadowing,
    spectral,
    verify_import,
    workflow_status,
)

# ---------------------------------------------------------------------------
# Server initialisation
# ---------------------------------------------------------------------------
_mcp_host = os.environ.get("RADIO_MCP_HOST", "127.0.0.1")
_mcp_port = int(os.environ.get("RADIO_MCP_PORT", "8000"))

mcp = FastMCP(
    "radio_ms_mcp",
    instructions=(
        "Radio interferometric Measurement Set inspector (Phase 1 — Layer 1 & 2). "
        "Tools return structured measurements — no interpretation. "
        "Consult the SKILL.md document for interferometrist reasoning guidance. "
        f"Version: {__version__}"
    ),
    host=_mcp_host,
    port=_mcp_port,
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


class PolCalFeasibilityInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    ms_path: str = Field(..., description="Path to Measurement Set", min_length=1)
    pa_spread_threshold_deg: float = Field(
        default=60.0,
        description=(
            "Minimum parallactic angle spread (degrees) required for a reliable "
            "D-term leakage solution (default 60°). Lower values relax the criterion."
        ),
        ge=0.0,
        le=180.0,
    )


class RefAntInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    ms_path: str = Field(..., description="Path to Measurement Set", min_length=1)
    field: str = Field(
        default="",
        description=(
            "CASA field selection string for the flagging heuristic "
            "(e.g. '3C147'). Empty string = all fields."
        ),
    )
    use_geometry: bool = Field(
        default=True,
        description="Score antennas by distance from array centre.",
    )
    use_flagging: bool = Field(
        default=True,
        description="Score antennas by unflagged data fraction.",
    )


class VerifyCaltablesInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    ms_path: str = Field(..., description="Path to Measurement Set (for provenance).", min_length=1)
    init_gain_table: str = Field(..., description="Path to init_gain.g caltable.", min_length=1)
    bp_table: str = Field(..., description="Path to BP0.b caltable.", min_length=1)


class RfiChannelStatsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    ms_path: str = Field(..., description="Path to Measurement Set.", min_length=1)
    flag_threshold: float = Field(
        default=0.5,
        description="Channel flag fraction threshold (0–1) above which a channel is 'bad' (default 0.5).",
        ge=0.0,
        le=1.0,
    )
    min_bad_chan_run: int = Field(
        default=1,
        description="Minimum contiguous bad channels to report as a range (default 1).",
        ge=1,
    )


class FlagSummaryInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    ms_path: str = Field(..., description="Path to Measurement Set.", min_length=1)
    field: str = Field(default="", description="CASA field selection (empty = all).")
    spw: str = Field(default="", description="CASA SpW selection (empty = all).")
    include_per_scan: bool = Field(
        default=False,
        description=(
            "If True, include the full per-scan flag list. Default False returns a "
            "compact scan summary (min/max/mean + fully-flagged scan IDs only). "
            "Use True only when diagnosing a specific scan-level problem."
        ),
    )


class AntennaFlagFractionInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    ms_path: str = Field(..., description="Path to Measurement Set.", min_length=1)
    verbosity: str = Field(
        default="full",
        description="'full' (default) or 'compact'. Compact strips field() wrappers on per-antenna records.",
    )
    n_workers: int | None = Field(
        default=None,
        description=(
            "Worker count for parallel FLAG reads. If omitted, computed adaptively "
            "from row count (call ms_flag_preflight first to get the recommendation). "
            "Pass 1 to force single-process."
        ),
        ge=1,
        le=8,
    )


class OnlineFlagStatsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    flag_file: str = Field(
        ...,
        description="Path to the .flagonline.txt file produced by importasdm.",
        min_length=1,
    )


class VerifyImportInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    ms_path: str = Field(..., description="Expected path to the output MS directory.", min_length=1)
    online_flag_file: str = Field(
        ...,
        description="Expected path to the .flagonline.txt file.",
        min_length=1,
    )


class VerifyPriorcalsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    ms_path: str = Field(..., description="Path to the MS (for provenance).", min_length=1)
    workdir: str = Field(
        ...,
        description="Directory where ms_generate_priorcals wrote its tables.",
        min_length=1,
    )
    table_names: list[str] = Field(
        default_factory=list,
        description=(
            "Specific table filenames to check (default: all four standard tables). "
            "Example: ['gain_curves.gc', 'opacities.opac']"
        ),
    )


class ResidualStatsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    ms_path: str = Field(
        ..., description="Path to the MS (CORRECTED + MODEL must exist).", min_length=1
    )
    field_id: int = Field(
        ...,
        description="Integer FIELD_ID of the bandpass calibrator (use ms_field_list to find it).",
        ge=0,
    )
    max_rows: int = Field(
        default=500_000,
        description="Maximum rows to read; rows are sampled uniformly if larger (default 500 000).",
        ge=1,
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


class WorkflowStatusInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    ms_path: str = Field(..., description="Path to the MS.", min_length=1)
    workdir: str = Field(..., description="Workdir where caltables/images live.", min_length=1)


class GaincalSnrPredictInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    ms_path: str = Field(..., min_length=1)
    field: str = Field(..., min_length=1, description="Calibrator field name, e.g. '3C147'.")
    flux_jy: float | None = Field(
        default=None,
        gt=0.0,
        description=(
            "Stokes I flux density in Jy at the observation band. "
            "If None, the tool returns UNAVAILABLE — the bundled catalogue does "
            "not store numeric flux. Read flux_jy from ms_setjy output or a band-"
            "appropriate Perley-Butler lookup before calling this tool."
        ),
    )
    solint_seconds: float = Field(
        default=-1.0, description="Solution interval in seconds. -1 = use scan length."
    )
    snr_threshold: float = Field(default=3.0, ge=0.0)


class CalsolStatsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    caltable_path: str = Field(
        ...,
        description="Path to CASA calibration table directory (e.g. gain.g, BP0.b, delay.k)",
        min_length=1,
    )
    snr_min: float = Field(
        default=3.0, ge=0.0, description="SNR threshold for low_snr outliers (default 3.0)."
    )
    amp_sigma: float = Field(
        default=5.0, ge=0.0, description="Amplitude outlier threshold in sigma (default 5.0)."
    )
    verbosity: str = Field(
        default="full",
        description="'full' (default) or 'compact'. Compact strips field() wrappers.",
    )


class CalsolPlotInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    caltable_path: str = Field(
        ...,
        description="Path to CASA calibration table directory.",
        min_length=1,
    )
    output_dir: str = Field(
        ...,
        description="Directory to write {name}_stats.npz and {name}_dashboard.html.",
        min_length=1,
    )


class CalsolPlotLibraryInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    caltable_paths: list[str] = Field(
        ...,
        description=(
            "Ordered list of caltable paths to plot "
            "(e.g. [gain_curves.gc, delay.K, bandpass.B, gain.G, gain.fluxscaled])."
        ),
        min_length=1,
    )
    output_dir: str = Field(
        ...,
        description="Directory to write all dashboard HTML and NPZ files.",
        min_length=1,
    )


class ImageStatsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    image_path: str = Field(
        ...,
        description=(
            "Path to the CASA image directory to analyse "
            "(e.g. '/data/obs/target.image.pbcor'). "
            "Must be a CASA native-format image (produced by tclean)."
        ),
        min_length=1,
    )
    psf_path: str | None = Field(
        default=None,
        description=(
            "Optional path to the PSF image (e.g. 'target.psf'). "
            "If provided, the restoring beam is also read from the PSF header "
            "as a cross-check."
        ),
    )


# ---------------------------------------------------------------------------
# Tool error handling wrapper
# ---------------------------------------------------------------------------


def _run_tool_sync(tool_fn, *args, **kwargs) -> str:
    """Run tool_fn synchronously; called from a thread via _run_tool."""
    try:
        result = tool_fn(*args, **kwargs)
        return json.dumps(result, indent=2, default=str)
    except RadioMSError as e:
        return json.dumps(e.to_dict(), indent=2)


async def _run_tool(tool_fn, *args, **kwargs) -> str:
    """
    Execute a tool function off the event loop thread and return JSON-encoded result.

    Runs synchronous (potentially long-running) tool functions in a thread pool
    via asyncio.to_thread so they never block the MCP server's event loop.
    Catches RadioMSError and returns a well-formed error envelope.
    Unexpected exceptions are re-raised (let FastMCP handle them).
    """
    import asyncio
    return await asyncio.to_thread(_run_tool_sync, tool_fn, *args, **kwargs)


# ---------------------------------------------------------------------------
# Layer 1 — Orientation tools
# ---------------------------------------------------------------------------


@mcp.tool(
    name="ms_observation_info",
    description=(
        "Telescope identity, observer, project code, UTC time range, duration, "
        "HISTORY count. First call in any orientation workflow. "
        "Hard-fails INSUFFICIENT_METADATA if TELESCOPE_NAME is blank."
    ),
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
    return await _run_tool(observation.run, params.ms_path)


@mcp.tool(
    name="ms_field_list",
    description=(
        "Field inventory with J2000 coords, intents, and calibrator roles. "
        "Cross-matches against bundled VLA catalogue. Emits per-target "
        "nearest_phase_cal and separation_deg."
    ),
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
    return await _run_tool(fields.run, params.ms_path)


@mcp.tool(
    name="ms_scan_list",
    description=(
        "Time-ordered scan records with field, intents, integration time, SpW IDs. "
        "Use for temporal structure and scan-gap detection."
    ),
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
    return await _run_tool(scans.run_scan_list, params.ms_path)


@mcp.tool(
    name="ms_scan_intent_summary",
    description=(
        "Observing-time fractions per intent (CALIBRATE_FLUX, CALIBRATE_PHASE, "
        "OBSERVE_TARGET). Calibrator/target time-balance audit."
    ),
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
    return await _run_tool(scans.run_scan_intent_summary, params.ms_path)


@mcp.tool(
    name="ms_spectral_window_list",
    description=(
        "Per-SpW frequency, channel count, bandwidth, correlation products, band name. "
        "Emits suggested.center_channels_string and wide_channels_string for gaincal/bandpass."
    ),
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
    return await _run_tool(spectral.run_spectral_window_list, params.ms_path)


@mcp.tool(
    name="ms_correlator_config",
    description=(
        "Correlator dump time and polarization basis (circular/linear/stokes/mixed). "
        "Emits corrstring_casa ('RR,LL' or 'XX,YY') ready for CASA selection strings."
    ),
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
    return await _run_tool(spectral.run_correlator_config, params.ms_path)


# ---------------------------------------------------------------------------
# Layer 2 — Instrument Sanity tools
# ---------------------------------------------------------------------------


@mcp.tool(
    name="ms_antenna_list",
    description=(
        "Antenna inventory, ECEF positions, dish diameter, mount type. "
        "Emits recommended_minblperant scaled to array size. "
        "Hard-fails on numeric-only antenna names (broken UVFITS)."
    ),
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
    return await _run_tool(antennas.run_antenna_list, params.ms_path)


@mcp.tool(
    name="ms_baseline_lengths",
    description=(
        "Physical baseline length statistics (min/max/median metres) plus per-SpW "
        "expected synthesised beam and largest angular scale. Not UV coverage."
    ),
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
    return await _run_tool(antennas.run_baseline_lengths, params.ms_path, params.spw_centre_freqs_hz)


@mcp.tool(
    name="ms_elevation_vs_time",
    description=(
        "Per-scan elevation per field via astropy AltAz. "
        "Flags scans below threshold_deg (default 20°) for low-elevation warnings."
    ),
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
    return await _run_tool(geometry.run_elevation_vs_time, params.ms_path, params.threshold_deg)


@mcp.tool(
    name="ms_parallactic_angle_vs_time",
    description=(
        "Per-field parallactic angle range in sky-frame and feed-frame. "
        "Feeds ms_pol_cal_feasibility. VALIDATION PENDING for feed-frame values."
    ),
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
    return await _run_tool(geometry.run_parallactic_angle_vs_time, params.ms_path)


@mcp.tool(
    name="ms_shadowing_report",
    description=(
        "Antenna shadowing events from msmd.shadowedAntennas plus FLAG_CMD subtable. "
        "Structural check before pre-calibration flagging."
    ),
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
    return await _run_tool(shadowing.run, params.ms_path, params.tolerance_m)


@mcp.tool(
    name="ms_flag_preflight",
    description=(
        "Fast FLAG-column probe: row count, shape, data volume, runtime estimate, "
        "recommended n_workers. Call before ms_antenna_flag_fraction on any large MS."
    ),
    annotations={
        "title": "Flag Column Preflight Probe",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def ms_flag_preflight(params: MSPathInput) -> str:
    """
    Fast pre-flight probe for the FLAG column — no data read, completes in seconds.

    Call this BEFORE ms_antenna_flag_fraction to:
    - Estimate wall-clock runtime and data volume
    - Get the recommended worker count to pass as n_workers
    - Decide whether to warn the user before committing to a long read

    A warning is added to the response if estimated_runtime_min > 10.

    Args:
        params.ms_path: Path to the Measurement Set.

    Returns:
        JSON with n_rows, flag_col_shape, n_spw, data_volume_gb,
        estimated_runtime_s, estimated_runtime_min, recommended_workers,
        will_parallelize.
    """
    return await _run_tool(flags.run_preflight, params.ms_path)


@mcp.tool(
    name="ms_antenna_flag_fraction",
    description=(
        "Pre-existing flag fraction per antenna via parallel FLAG reads. "
        "Autocorrelations excluded. Slow on large MS — call ms_flag_preflight first."
    ),
    annotations={
        "title": "Antenna Flag Fraction",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def ms_antenna_flag_fraction(params: AntennaFlagFractionInput) -> str:
    """
    Layer 2, Tool 6: Compute pre-existing flag fractions per antenna.

    Reads the FLAG column using adaptive multiprocessing. Worker count is
    computed from row count (collapses to 1 for small MSs where fork overhead
    dominates) or overridden via n_workers. Call ms_flag_preflight first to
    get the recommended worker count and runtime estimate.

    Autocorrelations excluded. Also reports online flag command counts from
    FLAG_CMD subtable.

    Args:
        params.ms_path:   Path to the Measurement Set.
        params.n_workers: Worker count override (1–8). If omitted, adaptive.

    Returns:
        JSON with overall_flag_fraction, autocorrelations_excluded, n_workers_used,
        and per_antenna array of {antenna_id, name, flag_fraction, n_flagged_elements,
        n_total_elements, n_flag_commands_online}.
    """
    return await _run_tool(
        flags.run, params.ms_path, n_workers=params.n_workers, verbosity=params.verbosity
    )


# ---------------------------------------------------------------------------
# Reference antenna selection
# ---------------------------------------------------------------------------


@mcp.tool(
    name="ms_refant",
    description=(
        "Ranked reference antenna list. Combines geometry (distance from array centre) "
        "and flagging (unflagged fraction) heuristics. Returns top pick + full ranking."
    ),
    annotations={
        "title": "Reference Antenna Selection",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def ms_refant(params: RefAntInput) -> str:
    """
    Select the best reference antenna using geometry and flagging heuristics.

    Scores each antenna on two independent criteria (each normalised to
    [0, n_antennas]) and returns a full ranked list:

      Geometry score: antennas closest to the array centre score highest.
      Flagging score: antennas with the most unflagged data score highest.

    Combined score = geo_score + flag_score (when both enabled).

    Args:
        params.ms_path:      Path to the Measurement Set.
        params.field:        CASA field selection for flagging heuristic (default = all fields).
        params.use_geometry: Include geometry score (default True).
        params.use_flagging: Include flagging score (default True).

    Returns:
        JSON with refant (top-ranked antenna name), refant_list (full ranked list),
        and ranked array with per-antenna geo_score, flag_score, combined_score.
    """
    return await _run_tool(
        refant.run,
        params.ms_path,
        params.field,
        params.use_geometry,
        params.use_flagging,
    )


# ---------------------------------------------------------------------------
# Calibration verification + RFI + flag summary
# ---------------------------------------------------------------------------


@mcp.tool(
    name="ms_verify_caltables",
    description=(
        "Filesystem + CASA row-count check for init_gain.g and BP0.b produced by "
        "ms_initial_bandpass. No CASA solve — quick structural gate."
    ),
    annotations={
        "title": "Verify Calibration Tables",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def ms_verify_caltables(params: VerifyCaltablesInput) -> str:
    """
    Verify that caltables from ms_initial_bandpass exist and are structurally sound.

    Checks:
      1. Both paths exist as non-empty directories.
      2. init_gain.g has CPARAM column and > 0 rows.
      3. BP0.b has CPARAM or FPARAM column and > 0 rows.

    Run this after executing the script produced by ms_initial_bandpass.

    Args:
        params.ms_path:         Source MS (for provenance — not opened).
        params.init_gain_table: Path to init_gain.g.
        params.bp_table:        Path to BP0.b.

    Returns:
        JSON with caltables_valid, and per-table exists/n_rows/valid fields.
    """
    return await _run_tool(
        caltables.run,
        params.ms_path,
        params.init_gain_table,
        params.bp_table,
    )


@mcp.tool(
    name="ms_rfi_channel_stats",
    description=(
        "Per-channel flag fractions across SpWs to find persistent RFI bands. "
        "Annotated with known sources (GPS, GSM, Iridium, WiFi)."
    ),
    annotations={
        "title": "RFI Channel Statistics",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def ms_rfi_channel_stats(params: RfiChannelStatsInput) -> str:
    """
    Identify RFI-contaminated channels from the FLAG column.

    Reads FLAG + DATA_DESC_ID in parallel chunks. Returns per-SpW bad-channel
    ranges with frequency annotations from a bundled RFI catalogue
    (GPS, GSM, Iridium, WiFi, etc.).

    Args:
        params.ms_path:         Path to the Measurement Set.
        params.flag_threshold:  Flag fraction threshold (default 0.5).
        params.min_bad_chan_run: Minimum contiguous bad channels for a range (default 1).

    Returns:
        JSON with per_spw array of bad channel ranges and RFI candidate annotations.
    """
    return await _run_tool(rfi.run, params.ms_path, params.flag_threshold, params.min_bad_chan_run)


@mcp.tool(
    name="ms_flag_summary",
    description=(
        "flagdata(mode='summary') wrapper: per-field/SpW/antenna/scan flag fractions. "
        "Run before and after rflag to capture the delta."
    ),
    annotations={
        "title": "Flag Summary",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def ms_flag_summary(params: FlagSummaryInput) -> str:
    """
    Return a complete flag statistics summary for the MS.

    Calls casatasks.flagdata(mode='summary'). Returns per-field, per-scan,
    per-SpW, and per-antenna flag fractions. Use before and after ms_apply_rflag
    to capture the flag delta.

    Args:
        params.ms_path: Path to the Measurement Set.
        params.field:   CASA field selection (empty = all).
        params.spw:     CASA SpW selection (empty = all).

    Returns:
        JSON with total_flag_fraction, per_field, per_spw, per_antenna, per_scan.
    """
    return await _run_tool(
        flag_summary.run, params.ms_path, params.field, params.spw, params.include_per_scan
    )


# ---------------------------------------------------------------------------
# Polarisation calibration feasibility
# ---------------------------------------------------------------------------


@mcp.tool(
    name="ms_pol_cal_feasibility",
    description=(
        "Polarization-calibration go/no-go gate. Verdict: FULL, LEAKAGE_ONLY, "
        "DEGRADED, or NOT_FEASIBLE based on pol-cal presence and PA-spread threshold."
    ),
    annotations={
        "title": "Polarisation Calibration Feasibility",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def ms_pol_cal_feasibility(params: PolCalFeasibilityInput) -> str:
    """
    Assess whether full VLA polarisation calibration is feasible for this dataset.

    Cross-matches observed fields against the bundled VLA pol calibrator catalogue
    (3C286, 3C138, 3C48, 3C147, 3C84, and Category B secondaries), then computes
    the parallactic angle spread of the leakage calibrator across its observed scans.

    Verdict values:
      FULL         — pol angle cal + leakage cal with sufficient PA spread (≥ threshold)
      LEAKAGE_ONLY — no angle cal, but leakage cal present with sufficient spread
      DEGRADED     — angle cal available but flagged as variable or in active flare
      NOT_FEASIBLE — no pol cals found, or PA spread below threshold

    Args:
        params.ms_path:                Path to the Measurement Set.
        params.pa_spread_threshold_deg: PA spread threshold for D-term feasibility (default 60°).

    Returns:
        JSON with band_centre_ghz, pol_angle_calibrator (source, frac_pol, PA, stable_pa),
        leakage_calibrator (pa_spread, n_scans, meets_threshold), verdict, and blocker.
    """
    return await _run_tool(
        pol_cal_feasibility.run,
        params.ms_path,
        params.pa_spread_threshold_deg,
    )


# ---------------------------------------------------------------------------
# Pre-calibration inspect tools
# ---------------------------------------------------------------------------


@mcp.tool(
    name="ms_online_flag_stats",
    description=(
        "Parse .flagonline.txt from importasdm: n_commands, antennas flagged, "
        "reason breakdown, time range. No CASA dependency — pure text."
    ),
    annotations={
        "title": "Online Flag File Statistics",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def ms_online_flag_stats(params: OnlineFlagStatsInput) -> str:
    """
    Parse a .flagonline.txt and return summary statistics.

    No CASA dependency — pure text parsing. Reads the online flag file
    produced by importasdm and returns the total command count, antennas
    flagged, reason code breakdown, and first/last time range seen.

    Args:
        params.flag_file: Path to the .flagonline.txt file.

    Returns:
        JSON with n_commands, n_antennas_flagged, antennas_flagged,
        reason_breakdown, and time_range (first and last seen).
    """
    return await _run_tool(online_flags.run, params.flag_file)


@mcp.tool(
    name="ms_verify_priorcals",
    description=(
        "Check prior caltables after ms_generate_priorcals. "
        "Emits priorcals_list (paths) ready to pass into gaincal/bandpass/applycal."
    ),
    annotations={
        "title": "Verify Prior Calibration Tables",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def ms_verify_priorcals(params: VerifyPriorcalsInput) -> str:
    """
    Verify prior calibration tables from ms_generate_priorcals exist and are non-empty.

    Checks each expected table (gain_curves.gc, opacities.opac, requantizer.rq,
    antpos.ap by default) for filesystem presence and CASA table row count.
    No casatasks required.

    Args:
        params.ms_path:     Path to the MS (for provenance).
        params.workdir:     Directory where ms_generate_priorcals wrote its tables.
        params.table_names: Specific table filenames to check (default: all four).

    Returns:
        JSON with all_valid, n_checked, n_valid, n_missing, and per-table check results.
    """
    return await _run_tool(
        priorcals_check.run,
        params.ms_path,
        params.workdir,
        params.table_names or None,
    )


@mcp.tool(
    name="ms_residual_stats",
    description=(
        "CORRECTED − MODEL amplitude distribution per SpW. "
        "RFI-threshold guide for ms_apply_initial_rflag. Requires CORRECTED + MODEL."
    ),
    annotations={
        "title": "Residual Amplitude Statistics",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def ms_residual_stats(params: ResidualStatsInput) -> str:
    """
    Compute per-SPW amplitude statistics of CORRECTED − MODEL for a field.

    Use before ms_apply_initial_rflag to choose thresholds, and after to verify
    the residual distribution improved. Requires CORRECTED and MODEL columns
    (run initial_bandpass.py and setjy.py first).

    Args:
        params.ms_path:   Path to the MS.
        params.field_id:  Integer FIELD_ID of the bandpass calibrator.
        params.max_rows:  Maximum rows to read per field (default 500 000).

    Returns:
        JSON with per-spw median_amp, std_amp, p95_amp, n_unflagged, n_flagged.
    """
    return await _run_tool(
        residual_stats.run,
        params.ms_path,
        params.field_id,
        params.max_rows,
    )


@mcp.tool(
    name="ms_calsol_stats",
    description=(
        "Caltable (G/B/K) per-(antenna, SpW, field) SNR, amplitude, phase, flagged fraction. "
        "Emits outliers block (low_snr + amp_outliers) for go/no-go gates."
    ),
    annotations={
        "title": "Calibration Solution Statistics",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def ms_calsol_stats(params: CalsolStatsInput) -> str:
    """
    Inspect a CASA calibration table and return per-(antenna, SPW, field) diagnostics.

    Supports G Jones (complex gain), B Jones (bandpass), and K Jones (delay) tables.
    Returns flagged fractions, SNR, amplitude/phase stats, and — for B tables — the
    full per-channel amplitude array. Used by the skill to make go/no-go decisions
    after a calibration solve.

    Stats arrays have shape [n_ant, n_spw, n_field]. Use ant_names, spw_ids,
    field_ids, and field_names to map indices back to physical labels.

    Args:
        params.caltable_path: Path to the caltable directory.

    Returns:
        JSON with table_type, axis metadata, flagged_frac, snr_mean, amplitude/phase
        stats (G/B), delay_ns and delay_rms_ns (K), and scalar summaries.
    """
    return await _run_tool(
        calsol_stats.run,
        params.caltable_path,
        snr_min=params.snr_min,
        amp_sigma=params.amp_sigma,
        verbosity=params.verbosity,
    )


@mcp.tool(
    name="ms_calsol_plot",
    description=(
        "Bokeh HTML dashboard + NPZ export for a caltable. "
        "For human visual inspection; not used for automated decisions."
    ),
    annotations={
        "title": "Calibration Solution Dashboard",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def ms_calsol_plot(params: CalsolPlotInput) -> str:
    """
    Generate a Bokeh HTML dashboard and NPZ file from a CASA calibration table.

    Calls ms_calsol_stats internally, saves raw arrays to {name}_stats.npz, and
    writes a self-contained Bokeh dashboard to {name}_dashboard.html. Dashboard
    layout adapts to table type: G (amplitude/phase per antenna), B (bandpass
    spectrum per antenna), K (delay per antenna).

    Args:
        params.caltable_path: Path to the caltable directory.
        params.output_dir:    Directory to write output files.

    Returns:
        JSON with npz_path, html_path, table_type, and axis dimensions.
    """
    return await _run_tool(calsol_plot.run, params.caltable_path, params.output_dir)


@mcp.tool(
    name="ms_plot_caltable_library",
    description=(
        "Plot an explicit list of CASA calibration tables in one call. "
        "Produces a Bokeh HTML dashboard and NPZ per table. "
        "Partial success: a bad table records an error entry rather than aborting the batch."
    ),
    annotations={
        "title": "Plot Calibration Table Library",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def ms_plot_caltable_library(params: CalsolPlotLibraryInput) -> str:
    """
    Generate Bokeh HTML dashboards for an explicit list of caltables.

    Each table is plotted independently — a bad table (not found, wrong type,
    CASA error) records an error entry without aborting the rest of the batch.

    Args:
        params.caltable_paths: Ordered list of caltable paths to plot.
        params.output_dir:     Directory to write all HTML and NPZ files.

    Returns:
        JSON with a per-table plots list (status, html_path, npz_path,
        table_type, error), plus n_ok and n_error counts.
    """
    return await _run_tool(
        calsol_plot_library.run,
        params.caltable_paths,
        params.output_dir,
    )


@mcp.tool(
    name="ms_verify_import",
    description=(
        "Post-importasdm filesystem check: MS has table.info and .flagonline.txt is "
        "non-empty. Pure filesystem — no CASA. Gate before ms_apply_preflag."
    ),
    annotations={
        "title": "Verify ASDM Import",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def ms_verify_import(params: VerifyImportInput) -> str:
    """
    Verify that ms_import_asdm completed successfully.

    Checks that the output MS exists and contains a table.info file,
    and that the online flag file exists and is non-empty.
    No CASA dependency — pure filesystem checks.

    Args:
        params.ms_path:          Expected path to the output MS directory.
        params.online_flag_file: Expected path to the .flagonline.txt file.

    Returns:
        JSON with ms_exists, ms_valid, flag_file_exists,
        flag_file_n_commands, and ready_for_preflag.
    """
    return await _run_tool(verify_import.run, params.ms_path, params.online_flag_file)


@mcp.tool(
    name="ms_workflow_status",
    description=(
        "State probe over MS + workdir. Returns ms_valid, intents_populated, "
        "calibrators_ms_present, priorcals_present, initial_bandpass_present, "
        "corrected_populated, final_caltables_present, first_image_present, and "
        "next_recommended_step (categorical label)."
    ),
    annotations={
        "title": "Workflow Status",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def ms_workflow_status(params: WorkflowStatusInput) -> str:
    """State probe over MS + workdir for pipeline resumption."""
    return await _run_tool(workflow_status.run, params.ms_path, params.workdir)


@mcp.tool(
    name="ms_gaincal_snr_predict",
    description=(
        "Predictive SNR for gaincal(solint='inf'). Per-SpW SNR using SEFD table "
        "for VLA/MeerKAT/uGMRT. Returns n_spw_below_threshold and recommendation_hint."
    ),
    annotations={
        "title": "Gaincal SNR Prediction",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def ms_gaincal_snr_predict(params: GaincalSnrPredictInput) -> str:
    """Predictive SNR for gaincal solint selection."""
    from ms_inspect.tools import gaincal_snr_predict

    return await _run_tool(
        gaincal_snr_predict.run,
        params.ms_path,
        params.field,
        params.solint_seconds,
        params.snr_threshold,
        flux_jy=params.flux_jy,
    )


@mcp.tool(
    name="ms_image_stats",
    description=(
        "Image quality metrics from a CASA image: MAD-based robust RMS, peak, "
        "dynamic range, restoring beam. Run after tclean to assess first-pass quality."
    ),
    annotations={
        "title": "Image Statistics",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def ms_image_stats(params: ImageStatsInput) -> str:
    """
    Phase 3, Tool 1: Quality metrics for a CASA image produced by tclean.

    Computes a robust RMS (MAD-based, insensitive to residual source flux),
    peak pixel value, dynamic range, and restoring beam parameters.

    Used by the imaging skill (11-imaging.md Step 9) to check whether the
    first-pass image meets quality gates before proceeding to self-calibration.

    Args:
        params.image_path: Path to the CASA image (e.g. imagename.image.pbcor).
        params.psf_path:   Optional path to the PSF image for beam cross-check.

    Returns:
        JSON envelope with rms_jy, peak_jy, dynamic_range,
        beam_major_arcsec, beam_minor_arcsec, beam_pa_deg.
        If psf_path provided, also psf_beam_major_arcsec etc.
    """
    return await _run_tool(image_stats.run, params.image_path, params.psf_path)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    transport = os.environ.get("RADIO_MCP_TRANSPORT", "stdio").lower()
    port = int(os.environ.get("RADIO_MCP_PORT", "8000"))

    if transport == "http":
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
