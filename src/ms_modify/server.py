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
from ms_modify import (
    __version__,
    applycal,
    bandpass,
    fluxscale,
    gaincal,
    initial_bandpass,
    initial_rflag,
    intents,
    preflag,
    priorcals,
    rflag,
    setjy,
    setjy_polcal,
)

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
    execute: bool = Field(
        default=False,
        description=(
            "If False (default), compute the intent mapping and return a preview. "
            "If workdir is provided, also write workdir/set_intents.py. "
            "If True, write the STATE subtable and update MAIN STATE_ID."
        ),
    )
    workdir: str = Field(
        default="",
        description=(
            "Directory for the generated set_intents.py script. "
            "Only used when execute=False. Empty = skip script generation."
        ),
    )
    dry_run: bool | None = Field(
        default=None,
        description="Deprecated alias for not execute. Use execute instead.",
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
            "UV range restriction (e.g. '>1klambda'). Set for 3C84 to exclude extended emission."
        ),
    )
    execute: bool = Field(
        default=False,
        description=(
            "If False (default), write the calibration script to workdir and return "
            "immediately. The user runs the script externally. "
            "If True, execute the script in-process (may take several minutes)."
        ),
    )


class ApplyRflagInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    ms_path: str = Field(
        ..., description="Path to the MS (CORRECTED column must exist).", min_length=1
    )
    workdir: str = Field(
        ...,
        description="Existing directory for the generated script and flag backups.",
        min_length=1,
    )
    field: str = Field(default="", description="CASA field selection (empty = all).")
    spw: str = Field(default="", description="CASA SpW selection (empty = all).")
    datacolumn: str = Field(
        default="corrected", description="Column to flag on (default 'corrected')."
    )
    timedevscale: float = Field(
        default=5.0, description="rflag time deviation scale threshold (default 5.0).", gt=0.0
    )
    freqdevscale: float = Field(
        default=5.0, description="rflag frequency deviation scale threshold (default 5.0).", gt=0.0
    )
    execute: bool = Field(
        default=False,
        description=(
            "If False (default), write apply_rflag.py to workdir and return immediately. "
            "If True, run rflag in-process (may take several minutes). "
            "A flag backup named 'before_rflag' is always saved before applying."
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
    return _run_tool(
        intents.set_intents,
        params.ms_path,
        dry_run=params.dry_run,
        execute=params.execute,
        workdir=params.workdir,
    )


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
        params.execute,
    )


@mcp.tool(
    name="ms_apply_rflag",
    annotations={
        "title": "Apply rflag RFI Flagging",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def ms_apply_rflag(params: ApplyRflagInput) -> str:
    """
    Generate (and optionally execute) an rflag script on the CORRECTED column.

    When execute=False (default), writes workdir/apply_rflag.py and returns.
    When execute=True, saves a flag backup named 'before_rflag' then applies rflag.

    Use ms_rfi_channel_stats first to identify contaminated channels, then
    ms_flag_summary before and after to capture the flag delta.

    Args:
        params.ms_path:      Path to MS (CORRECTED column must exist).
        params.workdir:      Existing directory for script + flag backups.
        params.field:        CASA field selection (empty = all).
        params.spw:          CASA SpW selection (empty = all).
        params.datacolumn:   Column to flag on (default 'corrected').
        params.timedevscale: Time deviation threshold (default 5.0).
        params.freqdevscale: Frequency deviation threshold (default 5.0).
        params.execute:      Generate only (False) or run in-process (True).

    Returns:
        JSON with script_path, datacolumn, scale parameters, and (if execute=True)
        flags_applied flag.
    """
    return _run_tool(
        rflag.run,
        params.ms_path,
        params.workdir,
        params.field,
        params.spw,
        params.datacolumn,
        params.timedevscale,
        params.freqdevscale,
        params.execute,
    )


# ---------------------------------------------------------------------------
# Input models — Preflag
# ---------------------------------------------------------------------------


class ApplyPreflagInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    ms_path: str = Field(..., description="Path to the full MS.", min_length=1)
    workdir: str = Field(..., description="Existing output directory.", min_length=1)
    cal_fields: str = Field(
        ...,
        description="CASA field selection string for calibrators (e.g. '3C147,3C286').",
        min_length=1,
    )
    online_flag_file: str = Field(
        default="",
        description="Path to .flagonline.txt from importasdm (empty = skip).",
    )
    shadow_tolerance_m: float = Field(
        default=0.0,
        description="Shadow tolerance in metres (default 0.0).",
        ge=0.0,
    )
    do_tfcrop: bool = Field(
        default=True,
        description="Apply conservative tfcrop pass (default True).",
    )
    execute: bool = Field(
        default=False,
        description=(
            "If False (default), write preflag_cmds.txt + preflag.py and return. "
            "If True, run flagdata(mode='list') + split in-process."
        ),
    )


class GeneratePriorcalsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    ms_path: str = Field(
        ..., description="Path to the MS (calibrators.ms or full MS).", min_length=1
    )
    workdir: str = Field(..., description="Existing directory for caltable output.", min_length=1)
    execute: bool = Field(
        default=False,
        description=(
            "If False (default), write priorcals.py and return. "
            "If True, run gencal in-process for all four tables."
        ),
    )


class SetjyInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    ms_path: str = Field(..., description="Path to calibrators.ms (or full MS).", min_length=1)
    workdir: str = Field(..., description="Existing directory for setjy.py script.", min_length=1)
    standard: str = Field(
        default="Perley-Butler 2017",
        description="Flux standard to use (default 'Perley-Butler 2017').",
    )
    execute: bool = Field(
        default=False,
        description=(
            "If False (default), write setjy.py and return. "
            "If True, run setjy in-process for each flux field."
        ),
    )


class SetjyPolcalInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    ms_path: str = Field(..., description="Path to the Measurement Set.", min_length=1)
    field: str = Field(
        ...,
        description=(
            "CASA field selection string for the polarization angle calibrator "
            "(e.g. '3C48', 'J0137+3309')."
        ),
        min_length=1,
    )
    workdir: str = Field(
        ...,
        description="Existing directory for the generated setjy_polcal.py script.",
        min_length=1,
    )
    reffreq_ghz: float = Field(
        ...,
        description=(
            "Reference frequency in GHz for the polynomial expansion (e.g. 3.0 for S-band centre)."
        ),
        gt=0.0,
    )
    calibrator_name: str = Field(
        default="",
        description=(
            "Catalogue name to look up (e.g. '3C48'). Defaults to field if empty. "
            "Use this when the field name in the MS differs from the catalogue name."
        ),
    )
    epoch: str = Field(
        default="perley_butler_2013",
        description="Catalogue epoch key for the polarization data.",
    )
    pol_freq_range_lo_ghz: float | None = Field(
        default=None,
        description="Lower bound (GHz) to restrict polindex and polangle fits.",
        gt=0.0,
    )
    pol_freq_range_hi_ghz: float | None = Field(
        default=None,
        description="Upper bound (GHz) to restrict polindex and polangle fits.",
        gt=0.0,
    )
    polindex_deg: int = Field(
        default=3,
        description="Polynomial degree for polindex (default 3).",
        ge=1,
    )
    polangle_deg: int = Field(
        default=4,
        description="Polynomial degree for polangle (default 4).",
        ge=1,
    )
    execute: bool = Field(
        default=False,
        description=(
            "If False (default), write setjy_polcal.py and return. "
            "If True, run setjy(standard='manual') in-process."
        ),
    )


class ApplyInitialRflagInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    ms_path: str = Field(
        ...,
        description="Path to the MS (CORRECTED + MODEL must exist).",
        min_length=1,
    )
    workdir: str = Field(..., description="Existing directory for generated scripts.", min_length=1)
    timedevscale: float = Field(
        default=5.0,
        description="rflag time deviation threshold (default 5.0).",
        gt=0.0,
    )
    freqdevscale: float = Field(
        default=5.0,
        description="rflag frequency deviation threshold (default 5.0).",
        gt=0.0,
    )
    timecutoff: float = Field(
        default=4.0,
        description="tfcrop time deviation threshold (default 4.0).",
        gt=0.0,
    )
    freqcutoff: float = Field(
        default=4.0,
        description="tfcrop frequency deviation threshold (default 4.0).",
        gt=0.0,
    )
    execute: bool = Field(
        default=False,
        description=(
            "If False (default), write initial_rflag_cmds.txt + initial_rflag.py and return. "
            "If True, run flagdata(mode='list') in-process."
        ),
    )


# ---------------------------------------------------------------------------
# Tools — Preflag
# ---------------------------------------------------------------------------


@mcp.tool(
    name="ms_apply_preflag",
    annotations={
        "title": "Apply Pre-Calibration Flags",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def ms_apply_preflag(params: ApplyPreflagInput) -> str:
    """
    Apply deterministic pre-calibration flags and split calibrators to a separate MS.

    Combines online flags, shadow, zero-clip, tfcrop, and polarization extension
    in a single flagdata(mode='list') pass for efficiency and auditability.
    After flagging, calibrator fields are split to workdir/calibrators.ms with
    keepflags=False.

    Args:
        params.ms_path:          Path to the full MS.
        params.workdir:          Existing output directory.
        params.cal_fields:       CASA field selection for calibrators.
        params.online_flag_file: Path to .flagonline.txt (empty = skip).
        params.shadow_tolerance_m: Shadow tolerance in metres.
        params.do_tfcrop:        Apply conservative tfcrop (default True).
        params.execute:          Generate scripts only (False) or run in-process (True).

    Returns:
        JSON with cmds_path, script_path, n_flag_commands, and (if execute=True) cal_ms.
    """
    return _run_tool(
        preflag.run,
        params.ms_path,
        params.workdir,
        params.cal_fields,
        params.online_flag_file,
        params.shadow_tolerance_m,
        params.do_tfcrop,
        params.execute,
    )


@mcp.tool(
    name="ms_generate_priorcals",
    annotations={
        "title": "Generate Prior Calibration Tables",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def ms_generate_priorcals(params: GeneratePriorcalsInput) -> str:
    """
    Generate the four deterministic prior calibration tables (gc, opac, rq, antpos).

    Tables generated via gencal in order:
      1. gain_curves.gc   — VLA elevation gain curves
      2. opacities.opac   — per-SPW zenith opacity
      3. requantizer.rq   — post-2011 VLA requantizer (skipped for pre-WIDAR data)
      4. antpos.ap        — antenna position corrections (skipped if empty)

    The returned 'priorcals' list is the canonical input to ms_initial_bandpass.

    Args:
        params.ms_path:  Path to the MS.
        params.workdir:  Existing directory for caltable output.
        params.execute:  Generate script only (False) or run gencal in-process (True).

    Returns:
        JSON with script_path, and (if execute=True) priorcals list and skipped list.
    """
    return _run_tool(
        priorcals.run,
        params.ms_path,
        params.workdir,
        params.execute,
    )


@mcp.tool(
    name="ms_setjy",
    annotations={
        "title": "Set Flux Density Models",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def ms_setjy(params: SetjyInput) -> str:
    """
    Set flux density models for standard VLA calibrators in the MS.

    Cross-matches observed fields against the bundled calibrator catalogue,
    then generates (or runs) setjy() for each flux standard found, using the
    Perley-Butler 2017 standard. Warns if 3C84 (resolved) or 3C138/3C48
    (variable/partially polarized) are present.

    Does NOT set polarization angle models (see CALPOL.md tools).

    Args:
        params.ms_path:   Path to calibrators.ms.
        params.workdir:   Existing directory for setjy.py script.
        params.standard:  Flux standard (default 'Perley-Butler 2017').
        params.execute:   Generate script only (False) or run setjy in-process (True).

    Returns:
        JSON with flux_fields, skipped_fields, warnings, and script_path.
    """
    return _run_tool(
        setjy.run,
        params.ms_path,
        params.workdir,
        params.standard,
        params.execute,
    )


@mcp.tool(
    name="ms_setjy_polcal",
    annotations={
        "title": "Set Polarization Calibrator Model",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def ms_setjy_polcal(params: SetjyPolcalInput) -> str:
    """
    Set the full polarization model for a polarization angle calibrator.

    Fits three polynomial models from the Perley & Butler (2013) catalogue:
      - Stokes I spectral index (log-polynomial → spix)
      - Pol fraction vs frequency (ascending polynomial → polindex)
      - Pol angle vs frequency in radians (ascending polynomial → polangle)

    All polynomials use x = (f - f_ref)/f_ref, ASCENDING coefficient order
    [c0, c1, ...] as required by CASA setjy(standard='manual').

    Writes workdir/setjy_polcal.py. Run it with CASA to populate the MODEL
    column with the full polarized flux density model before solving Kcross,
    D-terms, or position angle calibration tables.

    Args:
        params.ms_path:               Path to the Measurement Set.
        params.field:                 CASA field selection for the polcal source.
        params.workdir:               Existing directory for the generated script.
        params.reffreq_ghz:           Reference frequency in GHz.
        params.calibrator_name:       Catalogue lookup name (defaults to field).
        params.epoch:                 Catalogue epoch (default 'perley_butler_2013').
        params.pol_freq_range_lo_ghz: Lower GHz bound to restrict pol fits.
        params.pol_freq_range_hi_ghz: Upper GHz bound to restrict pol fits.
        params.polindex_deg:          Polynomial degree for polindex (default 3).
        params.polangle_deg:          Polynomial degree for polangle (default 4).
        params.execute:               Generate script only (False) or run in-process (True).

    Returns:
        JSON with script_path, calibrator, reffreq_ghz, flux_jy, polindex,
        polangle, polindex_c0 (pol fraction at reffreq), polangle_c0_rad
        (pol angle in radians at reffreq).
    """
    return _run_tool(
        setjy_polcal.run,
        params.ms_path,
        params.field,
        params.workdir,
        params.reffreq_ghz,
        params.calibrator_name or None,
        params.epoch,
        params.pol_freq_range_lo_ghz,
        params.pol_freq_range_hi_ghz,
        params.polindex_deg,
        params.polangle_deg,
        params.execute,
    )


@mcp.tool(
    name="ms_apply_initial_rflag",
    annotations={
        "title": "Apply Initial RFI Flagging on Residuals",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def ms_apply_initial_rflag(params: ApplyInitialRflagInput) -> str:
    """
    Run rflag + tfcrop on the residual column (CORRECTED − MODEL) in one pass.

    After ms_initial_bandpass populates CORRECTED, this tool flags RFI using
    both rflag and tfcrop on the residual signal in a single atomic
    flagdata(mode='list') call. flagbackup=True saves a versioned copy before flagging.

    Args:
        params.ms_path:      Path to the MS (CORRECTED + MODEL must exist).
        params.workdir:      Existing directory for generated scripts.
        params.timedevscale: rflag time deviation threshold (default 5.0).
        params.freqdevscale: rflag frequency deviation threshold (default 5.0).
        params.timecutoff:   tfcrop time deviation threshold (default 4.0).
        params.freqcutoff:   tfcrop frequency deviation threshold (default 4.0).
        params.execute:      Generate scripts only (False) or run in-process (True).

    Returns:
        JSON with cmds_path, script_path, thresholds, and (if execute=True) flags_applied.
    """
    return _run_tool(
        initial_rflag.run,
        params.ms_path,
        params.workdir,
        params.timedevscale,
        params.freqdevscale,
        params.timecutoff,
        params.freqcutoff,
        params.execute,
    )


# ---------------------------------------------------------------------------
# Input models — Gaincal / Bandpass / Fluxscale / Applycal
# ---------------------------------------------------------------------------


class GaincalInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    ms_path: str = Field(..., description="Path to the Measurement Set.", min_length=1)
    field: str = Field(
        ...,
        description="CASA field selection (e.g. '3C147' or '3C147,J0555+3948').",
        min_length=1,
    )
    spw: str = Field(default="", description="CASA SpW/channel selection (e.g. '0:5~58').")
    caltable: str = Field(..., description="Output caltable path.", min_length=1)
    workdir: str = Field(..., description="Existing directory for script output.", min_length=1)
    gaintype: str = Field(
        default="G",
        description="'G' for complex gains, 'K' for delays.",
    )
    calmode: str = Field(
        default="ap",
        description="'p' phase-only, 'a' amp-only, 'ap' amplitude+phase.",
    )
    solint: str = Field(default="inf", description="Solution interval ('int', 'inf', or e.g. '60s').")
    combine: str = Field(default="", description="Data axes to combine (e.g. 'scan').")
    refant: str = Field(default="", description="Reference antenna name.")
    minsnr: float = Field(default=3.0, description="Minimum SNR for a valid solution.", gt=0.0)
    minblperant: int = Field(default=4, description="Minimum baselines per antenna.", ge=1)
    solnorm: bool = Field(default=False, description="Normalise solutions to unit amplitude.")
    gaintable: list[str] = Field(default_factory=list, description="Prior caltables to apply.")
    interp: list[str] = Field(default_factory=list, description="Interpolation mode per gaintable.")
    parang: bool = Field(default=True, description="Apply parallactic angle correction.")
    execute: bool = Field(
        default=False,
        description="If False (default), write script and return. If True, run in-process.",
    )


class BandpassInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    ms_path: str = Field(..., description="Path to the Measurement Set.", min_length=1)
    field: str = Field(..., description="CASA field selection for the bandpass calibrator.", min_length=1)
    spw: str = Field(default="", description="CASA SpW selection (empty = all).")
    caltable: str = Field(..., description="Output caltable path.", min_length=1)
    workdir: str = Field(..., description="Existing directory for script output.", min_length=1)
    solint: str = Field(default="inf", description="Solution interval (default 'inf').")
    combine: str = Field(default="scan", description="Data axes to combine (default 'scan').")
    refant: str = Field(default="", description="Reference antenna name.")
    minsnr: float = Field(default=3.0, description="Minimum SNR threshold.", gt=0.0)
    minblperant: int = Field(default=4, description="Minimum baselines per antenna.", ge=1)
    fillgaps: int = Field(
        default=0,
        description=(
            "Fill flagged channels up to this width by interpolation. "
            "Default 0 (no filling) for the final bandpass. "
            "Use 62 only for the initial coarse bandpass (ms_initial_bandpass)."
        ),
        ge=0,
    )
    solnorm: bool = Field(default=False, description="Normalise solutions to unit amplitude.")
    gaintable: list[str] = Field(default_factory=list, description="Prior caltables to apply.")
    interp: list[str] = Field(default_factory=list, description="Interpolation mode per gaintable.")
    parang: bool = Field(default=True, description="Apply parallactic angle correction.")
    execute: bool = Field(
        default=False,
        description="If False (default), write script and return. If True, run in-process.",
    )


class FluxscaleInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    ms_path: str = Field(..., description="Path to the Measurement Set.", min_length=1)
    caltable: str = Field(
        ...,
        description="Input gain table containing solutions for all calibrators.",
        min_length=1,
    )
    fluxtable: str = Field(..., description="Output path for the flux-scaled gain table.", min_length=1)
    reference: str = Field(..., description="Field name of the primary flux calibrator.", min_length=1)
    transfer: list[str] = Field(
        ...,
        description="List of field names whose flux densities are to be derived.",
        min_length=1,
    )
    workdir: str = Field(..., description="Existing directory for script output.", min_length=1)
    incremental: bool = Field(
        default=False,
        description=(
            "If False (default), fluxtable replaces caltable at applycal. "
            "If True, fluxtable is used in addition to caltable."
        ),
    )
    execute: bool = Field(
        default=False,
        description="If False (default), write script and return. If True, run in-process.",
    )


class ApplycalInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    ms_path: str = Field(..., description="Path to the Measurement Set.", min_length=1)
    field: str = Field(..., description="CASA field selection to apply calibration to.", min_length=1)
    gaintable: list[str] = Field(..., description="Ordered list of caltable paths to apply.")
    workdir: str = Field(..., description="Existing directory for script output.", min_length=1)
    gainfield: list[str] = Field(
        default_factory=list,
        description=(
            "Per-table field selection for which solutions to use. "
            "Default: all solutions in each table. "
            "Set to the calibrator field name for the gain/fluxtable entry."
        ),
    )
    interp: list[str] = Field(
        default_factory=list,
        description=(
            "Per-table interpolation mode. "
            "Use 'nearest' for calibrators, 'linear' for target, "
            "'nearest,nearestflag' for delay (K) tables."
        ),
    )
    calwt: bool = Field(
        default=False,
        description=(
            "Calibrate the weights. Default False — VLA weights are not properly "
            "normalised; use statwt before imaging instead."
        ),
    )
    applymode: str = Field(
        default="calflagstrict",
        description=(
            "'calflagstrict' flags data with missing solutions (recommended). "
            "'calonly' applies without additional flagging."
        ),
    )
    parang: bool = Field(default=True, description="Apply parallactic angle correction.")
    flagbackup: bool = Field(
        default=False,
        description="Save a flag backup before applying. Set True for the first applycal call.",
    )
    execute: bool = Field(
        default=False,
        description="If False (default), write script and return. If True, run in-process.",
    )


# ---------------------------------------------------------------------------
# Tools — Gaincal / Bandpass / Fluxscale / Applycal
# ---------------------------------------------------------------------------


@mcp.tool(
    name="ms_gaincal",
    annotations={
        "title": "Gain Calibration",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def ms_gaincal(params: GaincalInput) -> str:
    """
    Solve for antenna-based gain calibration solutions.

    Covers three use cases via gaintype and calmode:
      gaintype='K'             → delay solutions (one per antenna per SPW)
      gaintype='G', calmode='p'  → phase-only (initial phase prior to bandpass)
      gaintype='G', calmode='ap' → amplitude+phase (final gain calibration)

    Always sets parang=True. Writes a self-contained script to workdir when
    execute=False (default). Hard fails if the caltable is not produced.

    Args:
        params.ms_path:      Path to the Measurement Set.
        params.field:        CASA field selection (all cal fields for gain solve).
        params.spw:          SpW/channel selection.
        params.caltable:     Output caltable path.
        params.workdir:      Existing directory for script output.
        params.gaintype:     'G' or 'K'.
        params.calmode:      'p', 'a', or 'ap'.
        params.solint:       Solution interval.
        params.combine:      Data axes to combine (use 'scan' for delay solve).
        params.refant:       Reference antenna.
        params.gaintable:    Prior caltables to apply on-the-fly.
        params.interp:       Interpolation mode per gaintable.
        params.execute:      Generate script only (False) or run in-process (True).

    Returns:
        JSON with script_path, caltable, gaintype, calmode, solint, refant.
    """
    return _run_tool(
        gaincal.run,
        params.ms_path,
        params.field,
        params.spw,
        params.caltable,
        params.workdir,
        params.gaintype,
        params.calmode,
        params.solint,
        params.combine,
        params.refant,
        params.minsnr,
        params.minblperant,
        params.solnorm,
        params.gaintable,
        params.interp,
        params.parang,
        params.execute,
    )


@mcp.tool(
    name="ms_bandpass",
    annotations={
        "title": "Bandpass Calibration",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def ms_bandpass(params: BandpassInput) -> str:
    """
    Solve for the complex bandpass calibration (channel-by-channel gains).

    This is the final bandpass solve run after RFI flagging is complete.
    Distinct from ms_initial_bandpass which is used to generate CORRECTED
    for rflag. Use gaintable to apply the prior delay (K) and initial
    phase (G0) solutions on-the-fly during the solve.

    Args:
        params.ms_path:      Path to the Measurement Set.
        params.field:        Bandpass calibrator field selection.
        params.spw:          SpW selection (empty = all).
        params.caltable:     Output caltable path.
        params.workdir:      Existing directory for script output.
        params.solint:       Solution interval (default 'inf').
        params.combine:      Data axes to combine (default 'scan').
        params.refant:       Reference antenna.
        params.fillgaps:     Fill flagged channels up to this width (default 0).
        params.gaintable:    Prior caltables (include G0 and K).
        params.interp:       Interpolation mode per gaintable.
        params.execute:      Generate script only (False) or run in-process (True).

    Returns:
        JSON with script_path, caltable, solint, combine, refant, fillgaps.
    """
    return _run_tool(
        bandpass.run,
        params.ms_path,
        params.field,
        params.spw,
        params.caltable,
        params.workdir,
        params.solint,
        params.combine,
        params.refant,
        params.minsnr,
        params.minblperant,
        params.fillgaps,
        params.solnorm,
        params.gaintable,
        params.interp,
        params.parang,
        params.execute,
    )


@mcp.tool(
    name="ms_fluxscale",
    annotations={
        "title": "Flux Scale Bootstrap",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def ms_fluxscale(params: FluxscaleInput) -> str:
    """
    Bootstrap the flux density scale from a primary to secondary calibrators.

    Reads the gain table produced by ms_gaincal (containing solutions for all
    calibrator fields), computes the amplitude ratio between reference and
    transfer fields, and writes a new fluxtable with properly-scaled solutions.
    Use the fluxtable (not the original gain table) in ms_applycal.

    Args:
        params.ms_path:    Path to the Measurement Set.
        params.caltable:   Input gain table with all calibrator solutions.
        params.fluxtable:  Output flux-scaled gain table path.
        params.reference:  Primary flux calibrator field name.
        params.transfer:   Secondary calibrator field names to rescale.
        params.workdir:    Existing directory for script output.
        params.incremental: If False (default), fluxtable replaces caltable.
        params.execute:    Generate script only (False) or run in-process (True).

    Returns:
        JSON with script_path, fluxtable, derived_flux_jy per field per SPW.
    """
    return _run_tool(
        fluxscale.run,
        params.ms_path,
        params.caltable,
        params.fluxtable,
        params.reference,
        params.transfer,
        params.workdir,
        params.incremental,
        params.execute,
    )


@mcp.tool(
    name="ms_applycal",
    annotations={
        "title": "Apply Calibration",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def ms_applycal(params: ApplycalInput) -> str:
    """
    Apply calibration tables to a field and write CORRECTED_DATA.

    Call once per field category with appropriate gainfield and interp:
      Flux cal:   gainfield=[..., flux_field],  interp=[..., 'nearest']
      Phase cal:  gainfield=[..., phase_field], interp=[..., 'nearest']
      Target:     gainfield=[..., phase_field], interp=[..., 'linear']

    Uses applymode='calflagstrict' by default — data with missing solutions
    are flagged rather than left uncorrected. Set calwt=False for VLA data.

    Args:
        params.ms_path:    Path to the Measurement Set.
        params.field:      Field to apply calibration to.
        params.gaintable:  Ordered list of caltables (priorcals + K + B + fluxtable).
        params.workdir:    Existing directory for script output.
        params.gainfield:  Per-table field selection for solution rows.
        params.interp:     Per-table interpolation mode.
        params.calwt:      Calibrate weights (default False for VLA).
        params.applymode:  'calflagstrict' (default) or 'calonly'.
        params.parang:     Parallactic angle correction (default True).
        params.flagbackup: Save flag backup first (default False).
        params.execute:    Generate script only (False) or run in-process (True).

    Returns:
        JSON with script_path, corrected_written, field, n_tables, applymode.
    """
    return _run_tool(
        applycal.run,
        params.ms_path,
        params.field,
        params.gaintable,
        params.workdir,
        params.gainfield or None,
        params.interp or None,
        params.calwt,
        params.applymode,
        params.parang,
        params.flagbackup,
        params.execute,
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
