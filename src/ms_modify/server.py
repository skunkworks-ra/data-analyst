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
from ms_inspect.util.formatting import compact_fields
from ms_modify import (
    __version__,
    applycal,
    bandpass,
    flag_caltable,
    fluxscale,
    gaincal,
    initial_bandpass,
    initial_rflag,
    intents,
    polcal,
    postcal_flag,
    preflag,
    priorcals,
    rflag,
    setjy,
    setjy_polcal,
    tclean,
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
        return json.dumps(compact_fields(result), separators=(",", ":"), default=str)
    except RadioMSError as e:
        return json.dumps(e.to_dict(), separators=(",", ":"))


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool(
    name="ms_set_intents",
    description=(
        "Populate STATE subtable and STATE_ID for an MS lacking scan intents. "
        "Cross-matches field names against VLA calibrator catalogue. "
        "Hard-fails if ≥50% of fields already have intents."
    ),
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
    description=(
        "Three-step initial bandpass: phase gaincal → bandpass solve → applycal. "
        "Populates CORRECTED for subsequent RFI flagging. Writes init_gain.g + BP0.b."
    ),
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
    description=(
        "General-purpose rflag pass on CORRECTED or DATA column. "
        "For the residual (CORRECTED − MODEL) pass after initial bandpass use "
        "ms_apply_initial_rflag instead."
    ),
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
        default="",
        description=(
            "Catalogue epoch key for the polarization data. Leave empty to "
            "auto-select the epoch nearest the observation date (latest if "
            "unknown)."
        ),
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


class PolcalInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    ms_path: str = Field(..., description="Path to the Measurement Set.", min_length=1)
    field: str = Field(
        ...,
        description="CASA field selection for calibrator.",
        min_length=1,
    )
    caltable: str = Field(..., description="Output caltable path.", min_length=1)
    workdir: str = Field(..., description="Existing directory for script output.", min_length=1)
    poltype: str = Field(
        ...,
        description="'Df' (D-terms), 'Df+QU' (D-terms + source pol), or 'Xf' (position angle).",
    )
    solint: str = Field(default="inf", description="Solution interval (default 'inf').")
    combine: str = Field(default="scan", description="Data axes to combine (default 'scan').")
    refant: str = Field(default="", description="Reference antenna name.")
    gaintable: list[str] = Field(default_factory=list, description="Prior caltables to apply.")
    interp: list[str] = Field(default_factory=list, description="Interpolation mode per gaintable.")
    spwmap: list[list[int]] = Field(
        default_factory=list,
        description=(
            "Optional per-prior-table SPW map (list-of-lists aligned to gaintable), "
            "e.g. [[], [0,0,0,0]] to fan an spw-combined prior (VLA multiband-delay "
            "Kcross) across all SPWs. Empty → CASA identity (default per-SPW behaviour)."
        ),
    )
    parang: bool = Field(
        default=True,
        description="Apply parallactic angle correction (default True, critical for polcal).",
    )
    execute: bool = Field(
        default=False,
        description="If False (default), write script and return. If True, run in-process.",
    )


class ApplyInitialRflagInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    ms_path: str = Field(
        ...,
        description="Path to the MS (CORRECTED + MODEL must exist).",
        min_length=1,
    )
    workdir: str = Field(..., description="Existing directory for generated scripts.", min_length=1)
    field: str = Field(
        ...,
        description=(
            "REQUIRED. The field(s) to flag — scope to whichever field's CORRECTED column "
            "is genuinely calibrated at this point (e.g. the bandpass calibrator right after "
            "initial_bandpass, or all calibrators after the full solve). An all-field pass over "
            "uncalibrated fields flags ~90% of the data and is recoverable only by re-splitting."
        ),
        min_length=1,
    )
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


class PostcalFlagInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    ms_path: str = Field(
        ..., description="Path to the MS (CORRECTED populated on the selected fields).", min_length=1
    )
    workdir: str = Field(..., description="Existing directory for generated scripts.", min_length=1)
    field: str = Field(
        ...,
        description=(
            "REQUIRED. The phase calibrator and/or science target whose CORRECTED column "
            "is valid after the final applycal. An all-field pass over fields without valid "
            "CORRECTED flags almost everything."
        ),
        min_length=1,
    )
    keep_spw: str = Field(
        default="",
        description="SpWs being KEPT — tfcrop + rflag run on these to salvage localized RFI. Empty = all.",
    )
    drop_spw: str = Field(
        default="",
        description="Drop-tier SpWs — fully flagged via manual command. Empty = drop nothing.",
    )
    datacolumn: str = Field(default="corrected", description="Column to flag on (default 'corrected').")
    clip_sigma: float | None = Field(
        default=5.0,
        description="Per-SpW robust clip ceiling = median + clip_sigma*1.4826*MAD, computed per kept SpW. None disables.",
    )
    clipmax: float | None = Field(
        default=None,
        description="Flat |data| ceiling fallback, used only when clip_sigma is None.",
    )
    uvrange: str = Field(
        default="",
        description="Optional CASA uvrange applied to the clip only (e.g. '>2klambda') to protect short-spacing flux on extended sources.",
    )
    timedevscale: float = Field(default=5.0, description="rflag time deviation threshold.", gt=0.0)
    freqdevscale: float = Field(default=5.0, description="rflag frequency deviation threshold.", gt=0.0)
    timecutoff: float = Field(default=4.0, description="tfcrop time deviation threshold.", gt=0.0)
    freqcutoff: float = Field(default=4.0, description="tfcrop frequency deviation threshold.", gt=0.0)
    execute: bool = Field(
        default=False,
        description="If False (default), write scripts and return. If True, run flagdata(mode='list') in-process.",
    )


class FlagCaltableInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    caltable_path: str = Field(
        ..., description="Path to the CASA calibration table to autoflag.", min_length=1
    )
    workdir: str = Field(
        ..., description="Existing directory for the generated script.", min_length=1
    )
    sigma: float = Field(
        default=5.0,
        description=(
            "Threshold scale (default 5.0; 6.0 is more conservative). Maps to "
            "timecutoff/freqcutoff (tfcrop) or timedevscale/freqdevscale (rflag)."
        ),
        gt=0.0,
    )
    mode: str | None = Field(
        default=None,
        description=(
            "'rflag' or 'tfcrop'. Default None → auto-route from the VisCal type "
            "(B→tfcrop, G/T/D→rflag, K→refused)."
        ),
    )
    datacolumn: str = Field(
        default="CPARAM",
        description="Solution column to flag on. Default 'CPARAM' (complex: B/G/D). 'FPARAM' for real-valued.",
        min_length=1,
    )
    flagbackup: bool = Field(
        default=True,
        description="Save a .flagversions backup of the caltable before flagging (default True).",
    )
    execute: bool = Field(
        default=False,
        description=(
            "If False (default), write flag_caltable.py and return. If True, run "
            "summary→apply→summary in-process and report flagged_frac_before/after/delta."
        ),
    )


# ---------------------------------------------------------------------------
# Tools — Preflag
# ---------------------------------------------------------------------------


@mcp.tool(
    name="ms_apply_preflag",
    description=(
        "Deterministic pre-cal flagging (online + shadow + clip + tfcrop + polarization) "
        "in one flagdata(mode='list') pass, then split calibrators to calibrators.ms."
    ),
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
    description=(
        "Generate four gencal tables: gain_curves.gc, opacities.opac, requantizer.rq, "
        "antpos.ap. Deterministic — no model required. Verify with ms_verify_priorcals."
    ),
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
      3. requantizer.rq   — VLA WIDAR requantizer (attempted iff the SYSPOWER
                            subtable has rows; absent SYSPOWER = pre-WIDAR data)
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
    description=(
        "Set Perley-Butler 2017 Stokes I flux model for standard VLA calibrators. "
        "Warns on 3C84 (resolved), 3C138/3C48 (variable). "
        "Use ms_setjy_polcal for full polarization models."
    ),
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
    description=(
        "Set full polarization model (I + polindex + polangle) for a pol angle calibrator "
        "from Perley-Butler 2013 coefficients. Required before Df/Df+QU/Xf solves."
    ),
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
        params.epoch:                 Catalogue epoch (empty → auto-select by obs date).
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
        params.epoch or None,
        params.pol_freq_range_lo_ghz,
        params.pol_freq_range_hi_ghz,
        params.polindex_deg,
        params.polangle_deg,
        params.execute,
    )


@mcp.tool(
    name="ms_apply_initial_rflag",
    description=(
        "Single-pass rflag + tfcrop on residual (CORRECTED − MODEL) for the "
        "post-initial-bandpass RFI clean-up. Requires MODEL populated by setjy."
    ),
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
        params.field:        REQUIRED. Field(s) whose CORRECTED column is valid at this stage.
                             All-field passes over uncalibrated fields are unsafe (see field docs).
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
        params.field,
        params.timedevscale,
        params.freqdevscale,
        params.timecutoff,
        params.freqcutoff,
        params.execute,
    )


@mcp.tool(
    name="ms_postcal_flag",
    description=(
        "Post-calibration RFI flagging on the phase calibrator and science target. "
        "One atomic flagdata(mode='list') pass on CORRECTED: optional clip, then "
        "tfcrop + rflag on the kept SpWs (salvage localized RFI), then a manual flag "
        "of the drop-tier SpWs. Consumes the SpW triage from ms_spw_amp_severity."
    ),
    annotations={
        "title": "Post-Calibration RFI Flagging",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def ms_postcal_flag(params: PostcalFlagInput) -> str:
    """
    Post-calibration RFI flagging on target + phase calibrator CORRECTED.

    Extends flagging to the fields the pre-cal pipeline never cleaned and bakes
    the SpW-triage decision into the FLAG column. Generates postcal_flag_cmds.txt
    + postcal_flag.py; runs in-process when execute=True. flagbackup=True saves a
    versioned backup before flagging.

    Args:
        params.ms_path:      Path to the MS (CORRECTED on the selected fields).
        params.workdir:      Existing directory for generated scripts.
        params.field:        REQUIRED. Phase cal and/or target with valid CORRECTED.
        params.keep_spw:     SpWs to salvage (tfcrop + rflag). Empty = all.
        params.drop_spw:     Drop-tier SpWs to fully flag. Empty = none.
        params.datacolumn:   Column to flag on (default 'corrected').
        params.clipmax:      Optional |CORRECTED| ceiling applied first.
        params.timedevscale/freqdevscale: rflag thresholds (default 5.0).
        params.timecutoff/freqcutoff:     tfcrop thresholds (default 4.0).
        params.execute:      Generate scripts only (False) or run in-process (True).

    Returns:
        JSON with cmds_path, script_path, selections, and (if execute=True) flags_applied.
    """
    return _run_tool(
        postcal_flag.run,
        params.ms_path,
        params.workdir,
        params.field,
        params.keep_spw,
        params.drop_spw,
        params.datacolumn,
        params.clip_sigma,
        params.clipmax,
        params.uvrange,
        params.timedevscale,
        params.freqdevscale,
        params.timecutoff,
        params.freqcutoff,
        params.execute,
    )


@mcp.tool(
    name="ms_flag_caltable",
    description=(
        "Autoflag a caltable's solutions (rflag/tfcrop, auto-routed from VisCal type) "
        "to catch RFI-contaminated outliers that passed the solve-time SNR cut. Run "
        "after a table is created, before it is applied as a prior. Delay (K) tables "
        "are refused. Reports flagged fraction before/after."
    ),
    annotations={
        "title": "Flag Calibration Table Solutions",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def ms_flag_caltable(params: FlagCaltableInput) -> str:
    """
    Autoflag outlier solutions in a calibration table.

    Mode is auto-routed from the VisCal type unless overridden: B→tfcrop,
    G/T/D→rflag, K→refused. A single sigma (default 5.0) maps to the relevant
    flagdata thresholds. The flagged fraction is reported so the caller can
    decide whether to redo the solve (> 30% flagged) versus loosen sigma.

    Args:
        params.caltable_path: Path to the CASA calibration table.
        params.workdir:       Existing directory for the generated script.
        params.sigma:         Threshold scale (default 5.0; 6.0 more conservative).
        params.mode:          'rflag'/'tfcrop' override, or None to auto-route.
        params.datacolumn:    Solution column (default 'CPARAM').
        params.flagbackup:    Save a .flagversions backup first (default True).
        params.execute:       Generate script only (False) or run in-process (True).

    Returns:
        JSON with script_path, viscal_type, resolved mode, and (if execute=True)
        flagged_frac_before/after/delta.
    """
    return _run_tool(
        flag_caltable.run,
        params.caltable_path,
        params.workdir,
        params.sigma,
        params.mode,
        params.datacolumn,
        params.flagbackup,
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
        description="'G' for complex gains, 'K' for delays, 'KCROSS' for cross-hand delay.",
    )
    calmode: str = Field(
        default="ap",
        description="'p' phase-only, 'a' amp-only, 'ap' amplitude+phase.",
    )
    solint: str = Field(
        default="inf", description="Solution interval ('int', 'inf', or e.g. '60s')."
    )
    combine: str = Field(default="", description="Data axes to combine (e.g. 'scan').")
    refant: str = Field(default="", description="Reference antenna name.")
    minsnr: float = Field(default=3.0, description="Minimum SNR for a valid solution.", gt=0.0)
    minblperant: int = Field(default=4, description="Minimum baselines per antenna.", ge=1)
    solnorm: bool = Field(default=False, description="Normalise solutions to unit amplitude.")
    smodel: list[float] | None = Field(
        default=None,
        description="Scratch model [I, Q, U, V] for gaintype='KCROSS' (default None).",
    )
    gaintable: list[str] = Field(default_factory=list, description="Prior caltables to apply.")
    interp: list[str] = Field(default_factory=list, description="Interpolation mode per gaintable.")
    spwmap: list[list[int]] = Field(
        default_factory=list,
        description=(
            "Optional per-prior-table SPW map (list-of-lists aligned to gaintable) to "
            "fan an spw-combined prior across all SPWs. Empty → CASA identity. Only "
            "needed if a prior used combine='spw' (VLA multiband delay)."
        ),
    )
    parang: bool = Field(default=True, description="Apply parallactic angle correction.")
    execute: bool = Field(
        default=False,
        description="If False (default), write script and return. If True, run in-process.",
    )


class BandpassInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    ms_path: str = Field(..., description="Path to the Measurement Set.", min_length=1)
    field: str = Field(
        ..., description="CASA field selection for the bandpass calibrator.", min_length=1
    )
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
    fluxtable: str = Field(
        ..., description="Output path for the flux-scaled gain table.", min_length=1
    )
    reference: str = Field(
        ..., description="Field name of the primary flux calibrator.", min_length=1
    )
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
    field: str = Field(
        ..., description="CASA field selection to apply calibration to.", min_length=1
    )
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
    spwmap: list[list[int]] = Field(
        default_factory=list,
        description=(
            "Optional per-table SPW map (list-of-lists aligned to gaintable), e.g. "
            "[[], [0,0,0,0], []] to fan an spw-combined table across all SPWs while "
            "leaving per-SPW tables on identity. Empty → CASA identity (unchanged "
            "per-SPW behaviour). Only needed when a table used combine='spw' (VLA "
            "multiband delay)."
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
        default="calonly",
        description=(
            "'calonly' (default) applies calibration without flagging, leaving the FLAG "
            "column to post-cal RFI flagging (ms_postcal_flag, skill 13). 'calflagstrict' "
            "additionally flags data with missing/flagged solutions at apply time."
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
    description=(
        "Solve antenna-based gains. gaintype: 'G' (complex gain), 'K' (delay), "
        "'KCROSS' (cross-hand delay). calmode: 'p'/'a'/'ap'. Writes caltable; "
        "use ms_fluxscale or ms_applycal next."
    ),
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
        params.spwmap or None,
        params.parang,
        params.smodel,
        params.execute,
    )


@mcp.tool(
    name="ms_polcal",
    description=(
        "Polarization solve. poltype: 'Df' (leakage, known source pol), "
        "'Df+QU' (leakage + source Q/U together), 'Xf' (absolute position angle). "
        "Xf requires ms_setjy_polcal first."
    ),
    annotations={
        "title": "Polarisation Calibration",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def ms_polcal(params: PolcalInput) -> str:
    """
    Solve for polarisation calibration solutions.

    Supports three poltype modes:
      poltype='Df'    → D-term leakage calibration
      poltype='Df+QU' → D-term leakage + source Q,U (both unknowns solved together)
      poltype='Xf'    → Position angle calibration

    Args:
        params.ms_path:      Path to the Measurement Set.
        params.field:        CASA field selection for calibrator.
        params.caltable:     Output caltable path.
        params.workdir:      Existing directory for script output.
        params.poltype:      'Df', 'Df+QU', or 'Xf'.
        params.solint:       Solution interval (default 'inf').
        params.combine:      Data axes to combine (default 'scan').
        params.refant:       Reference antenna.
        params.gaintable:    Prior caltables to apply on-the-fly.
        params.interp:       Interpolation mode per gaintable.
        params.parang:       Apply parallactic angle correction (critical for polcal).
        params.execute:      Generate script only (False) or run in-process (True).

    Returns:
        JSON with script_path, caltable, poltype, field, solint, combine, refant.
    """
    return _run_tool(
        polcal.run,
        params.ms_path,
        params.field,
        params.caltable,
        params.workdir,
        params.poltype,
        params.solint,
        params.combine,
        params.refant,
        params.gaintable or None,
        params.interp or None,
        params.spwmap or None,
        params.parang,
        params.execute,
    )


@mcp.tool(
    name="ms_bandpass",
    description=(
        "Final bandpass solve after RFI flagging is complete. "
        "Distinct from ms_initial_bandpass. Applies priors (K + G0) on-the-fly via gaintable."
    ),
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
    description=(
        "Bootstrap flux scale from primary to secondary calibrators. "
        "Produces fluxtable for ms_applycal. Do not pass the input gain table to applycal."
    ),
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
    description=(
        "Apply calibration tables to a field and populate CORRECTED_DATA. "
        "Default applymode='calonly' leaves flagging to post-cal RFI flagging "
        "(ms_postcal_flag). calwt=False is correct for VLA."
    ),
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

    Uses applymode='calonly' by default — calibration is applied without
    flagging, so post-cal RFI flagging (ms_postcal_flag, skill 13) owns the FLAG
    column. Use 'calflagstrict' to flag missing-solution data at apply time.
    Set calwt=False for VLA data.

    Args:
        params.ms_path:    Path to the Measurement Set.
        params.field:      Field to apply calibration to.
        params.gaintable:  Ordered list of caltables (priorcals + K + B + fluxtable).
        params.workdir:    Existing directory for script output.
        params.gainfield:  Per-table field selection for solution rows.
        params.interp:     Per-table interpolation mode.
        params.calwt:      Calibrate weights (default False for VLA).
        params.applymode:  'calonly' (default) or 'calflagstrict'.
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
        params.spwmap or None,
        params.calwt,
        params.applymode,
        params.parang,
        params.flagbackup,
        params.execute,
    )


# ---------------------------------------------------------------------------
# Input model — Tclean
# ---------------------------------------------------------------------------


class TcleanInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    ms_path: str = Field(..., description="Path to the full Measurement Set.", min_length=1)
    imagename: str = Field(
        ...,
        description=(
            "Base path for all output image products (no suffix). "
            "Example: '/data/images/3c391_spw0'. "
            "tclean appends .image, .psf, .residual, etc."
        ),
        min_length=1,
    )
    field: str = Field(
        ...,
        description="CASA field selection for science target(s), e.g. '2~8' or '3C391_C1'.",
        min_length=1,
    )
    workdir: str = Field(
        ..., description="Existing directory for the generated script.", min_length=1
    )
    spw: str = Field(
        default="",
        description=(
            "CASA SPW selection (default '' = all SPWs). Use to exclude "
            "RFI-dominated SPWs, e.g. '0~8,10~15' to drop SPW 9."
        ),
    )
    stokes: str = Field(
        default="I",
        description="Stokes products to image. Default 'I'. Also accepts 'IV', 'IQUV', 'RR', 'LL', etc.",
    )
    specmode: str = Field(
        default="mfs",
        description=(
            "'mfs' for continuum (default), 'cube' for per-channel imaging, or "
            "'mvc' for wideband awp2 imaging (awp2 lacks conjbeams; plain 'mfs' "
            "needs several major cycles to converge flux normalization)."
        ),
    )
    deconvolver: str = Field(
        default="hogbom",
        description="'hogbom' (default first-pass) or 'mtmfs' for wideband (fractional BW > 20%).",
    )
    nterms: int | None = Field(
        default=None,
        description="Taylor terms for mtmfs deconvolver (pass 2 for mtmfs; omit for hogbom).",
        ge=1,
    )
    gridder: str = Field(
        default="standard",
        description="'standard', 'wproject' (W-term single pointing), or 'awp2' (mosaic, EVLA/ALMA).",
    )
    wprojplanes: int | None = Field(
        default=None,
        description=(
            "Number of W-projection planes. Omit when W-terms are negligible "
            "(Fresnel number >= 0.9). Valid for both 'wproject' and 'awp2' gridders. "
            "If omitted for those gridders, CASA silently defaults to 1 (no W-projection)."
        ),
        ge=1,
    )
    cfcache: str | None = Field(
        default=None,
        description=(
            "Convolution-function cache path. Only used by gridder='awproject' "
            "(awp2 has no cfcache). Without it, awproject recomputes CFs on every "
            "run — potentially hours."
        ),
    )
    cell: str = Field(
        default="1.0arcsec",
        description="Cell size, e.g. '2.5arcsec'. Derive from 1/(max_baseline_lambda * 3).",
    )
    imsize: list[int] = Field(
        default_factory=lambda: [512, 512],
        description=(
            "Image size in pixels [nx, ny]. Must be a composite number (2^a * 3^b * 5^c). "
            "Derive from primary beam FWHM / cell, rounded up."
        ),
        min_length=2,
        max_length=2,
    )
    weighting: str = Field(default="briggs", description="UV weighting scheme (default 'briggs').")
    robust: float = Field(
        default=0.5,
        description="Briggs robust parameter. -2 = uniform, +2 = natural (default 0.5).",
        ge=-2.0,
        le=2.0,
    )
    niter: int = Field(
        default=50000,
        description="Maximum clean iterations (default 50000). Use 1000 for quick diagnostic runs.",
        ge=0,
    )
    threshold: str = Field(
        default="1.0mJy",
        description="Clean stopping threshold, e.g. '0.5mJy'. Derive from 3 * radiometer RMS.",
    )
    savemodel: str = Field(
        default="modelcolumn",
        description="'modelcolumn' writes MODEL_DATA for self-cal (default). 'none' skips it.",
    )
    pblimit: float = Field(
        default=-0.01,
        description=(
            "Primary-beam gain cutoff. Default -0.01 (negative disables PB-based "
            "blanking, keeping low-gain regions and PB-sidelobe outliers visible). "
            "CASA's default 0.2 blanks everything below 20% PB."
        ),
    )
    execute: bool = Field(
        default=False,
        description=(
            "If False (default), write tclean script and return immediately. "
            "If True, run tclean in-process (intended for test data only — "
            "real mosaics can take hours)."
        ),
    )


# ---------------------------------------------------------------------------
# Tool — Tclean
# ---------------------------------------------------------------------------


@mcp.tool(
    name="ms_tclean",
    description=(
        "First-pass imaging: generate (or execute) a tclean script. "
        "pbcor=True always. savemodel='modelcolumn' for self-cal readiness. "
        "Validates CORRECTED_DATA exists in the MS."
    ),
    annotations={
        "title": "First-Pass Imaging",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def ms_tclean(params: TcleanInput) -> str:
    """
    Generate (and optionally execute) a first-pass tclean imaging script.

    Validates that CORRECTED_DATA exists in the MS. All imaging parameters
    are passed explicitly — use skill 11-imaging.md to derive them from
    Phase 1–2 tool outputs before calling this tool.

    pbcor=True is always set internally. The generated script cleans up
    any existing image products for this imagename before running, making
    it safely re-runnable.

    Args:
        params.ms_path:     Path to the full MS (not calibrators.ms).
        params.imagename:   Base path for image output (no suffix).
        params.field:       CASA field selection for science target(s).
        params.workdir:     Existing directory for the generated script.
        params.spw:         CASA SPW selection (default '' = all SPWs).
        params.stokes:      Stokes products (default 'I').
        params.specmode:    'mfs' or 'cube'.
        params.deconvolver: 'hogbom' or 'mtmfs'.
        params.nterms:      Taylor terms (pass 2 for mtmfs; omit for hogbom).
        params.gridder:     'standard', 'wproject', or 'awp2'.
        params.wprojplanes: W-projection planes (omit if Fresnel >= 0.9).
        params.cell:        Cell size string.
        params.imsize:      Image size [nx, ny].
        params.weighting:   UV weighting (default 'briggs').
        params.robust:      Briggs robust (default 0.5).
        params.niter:       Max iterations (default 50000).
        params.threshold:   Clean stopping threshold.
        params.savemodel:   'modelcolumn' for self-cal readiness (default).
        params.execute:     Generate script (False) or run in-process (True).

    Returns:
        JSON with script_path, imagename, and completed flag.
    """
    return _run_tool(
        tclean.run,
        params.ms_path,
        params.imagename,
        params.field,
        params.workdir,
        spw=params.spw,
        stokes=params.stokes,
        specmode=params.specmode,
        deconvolver=params.deconvolver,
        nterms=params.nterms,
        gridder=params.gridder,
        wprojplanes=params.wprojplanes,
        cfcache=params.cfcache,
        cell=params.cell,
        imsize=params.imsize,
        weighting=params.weighting,
        robust=params.robust,
        niter=params.niter,
        threshold=params.threshold,
        savemodel=params.savemodel,
        pblimit=params.pblimit,
        execute=params.execute,
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
