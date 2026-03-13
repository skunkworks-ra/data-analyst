"""
initial_bandpass.py — ms_initial_bandpass

Produces an initial coarse bandpass calibration on a calibrator-only MS
and populates the CORRECTED column so that rflag can proceed.

CASA call sequence (adapted from evla_pipe/stages/initial_bp.py):

  Step 1 — gaincal(solint='int', calmode='p')  → init_gain.g
  Step 2 — bandpass(solint='inf', combine='scan', fillgaps=62) → BP0.b
  Step 3 — applycal(all fields)                → CORRECTED column written

Hard fails if either caltable is not produced on disk.
No dry_run mode — the output is new files on a derived MS; the original MS
is never touched.
"""

from __future__ import annotations

import os
from pathlib import Path

from ms_inspect.util.casa_context import validate_ms_path
from ms_inspect.util.formatting import field as fmt_field
from ms_inspect.util.formatting import response_envelope
from ms_modify.exceptions import InitialBandpassFailedError

TOOL_NAME = "ms_initial_bandpass"

# Fixed parameters matching evla_pipe defaults — not exposed to caller
_MINSNR = 3.0
_FILLGAPS = 62
_SOLNORM = False


def _table_exists(path: str) -> bool:
    """Return True if a caltable directory exists and is non-empty."""
    p = Path(path)
    return p.exists() and p.is_dir() and any(p.iterdir())


def run(
    ms_path: str,
    bp_field: str,
    ref_ant: str,
    workdir: str,
    bp_scan: str = "",
    all_spw: str = "",
    priorcals: list[str] | None = None,
    min_bl_per_ant: int = 4,
    uvrange: str = "",
) -> dict:
    """
    Solve an initial coarse bandpass and populate the CORRECTED column.

    Args:
        ms_path:        Path to cal_only.ms.
        bp_field:       CASA field selection string for bandpass calibrator.
        ref_ant:        Reference antenna name (from ms_refant output).
        workdir:        Directory to write caltables into (must exist).
        bp_scan:        CASA scan selection string (empty = all scans).
        all_spw:        CASA SpW selection string (empty = all SpWs).
        priorcals:      Prior calibration tables to pre-apply (e.g. requantiser, Tsys).
        min_bl_per_ant: minblperant for gaincal and bandpass (default 4).
        uvrange:        UV range restriction (set for 3C84 to exclude extended emission).

    Returns:
        Standard response envelope with init_gain_table, bp_table,
        corrected_written, and calibration parameters.
    """
    p = validate_ms_path(ms_path)
    ms_str = str(p)
    casa_calls: list[str] = []
    warnings: list[str] = []

    if priorcals is None:
        priorcals = []

    # ------------------------------------------------------------------
    # Validate workdir and priorcals
    # ------------------------------------------------------------------
    workdir_path = Path(workdir)
    if not workdir_path.exists():
        from ms_inspect.exceptions import ComputationError
        raise ComputationError(
            f"workdir does not exist: {workdir}. Create it before calling this tool.",
            ms_path=ms_path,
        )

    for pc in priorcals:
        if not Path(pc).exists():
            from ms_inspect.exceptions import ComputationError
            raise ComputationError(
                f"Prior calibration table not found: {pc}",
                ms_path=ms_path,
            )

    # ------------------------------------------------------------------
    # Import casatasks
    # ------------------------------------------------------------------
    try:
        from casatasks import gaincal, bandpass, applycal  # type: ignore[import]
    except ImportError:
        from ms_inspect.exceptions import CASANotAvailableError
        raise CASANotAvailableError(
            "casatasks is not installed or cannot be imported.",
            ms_path=ms_path,
        )

    # ------------------------------------------------------------------
    # Step 1 — gaincal (phase, per-integration)
    # ------------------------------------------------------------------
    init_gain_table = str(workdir_path / "init_gain.g")

    gaincal_kwargs: dict = dict(
        vis=ms_str,
        caltable=init_gain_table,
        field=bp_field,
        spw=all_spw,
        scan=bp_scan,
        solint="int",
        refant=ref_ant,
        minblperant=min_bl_per_ant,
        minsnr=_MINSNR,
        gaintype="G",
        calmode="p",
        solnorm=_SOLNORM,
        gaintable=priorcals,
    )
    if uvrange:
        gaincal_kwargs["uvrange"] = uvrange

    casa_calls.append(
        f"casatasks.gaincal(field='{bp_field}', solint='int', calmode='p', "
        f"refant='{ref_ant}') → init_gain.g"
    )

    try:
        gaincal(**gaincal_kwargs)
    except Exception as e:
        raise InitialBandpassFailedError(
            f"gaincal failed with exception: {e}\n"
            f"Command: gaincal(vis='{ms_str}', caltable='{init_gain_table}', "
            f"field='{bp_field}', solint='int', refant='{ref_ant}')",
            ms_path=ms_path,
        ) from e

    if not _table_exists(init_gain_table):
        raise InitialBandpassFailedError(
            f"gaincal did not produce init_gain.g at '{init_gain_table}'. "
            "Possible causes: too few unflagged baselines, wrong field/scan selection, "
            f"or refant '{ref_ant}' not present in the MS.",
            ms_path=ms_path,
        )

    # ------------------------------------------------------------------
    # Step 2 — bandpass (solint=inf, combine='scan')
    # ------------------------------------------------------------------
    bp_table = str(workdir_path / "BP0.b")

    bp_gaintable = priorcals + [init_gain_table]

    bandpass_kwargs: dict = dict(
        vis=ms_str,
        caltable=bp_table,
        field=bp_field,
        spw=all_spw,
        scan=bp_scan,
        solint="inf",
        combine="scan",
        refant=ref_ant,
        minblperant=min_bl_per_ant,
        minsnr=_MINSNR,
        bandtype="B",
        fillgaps=_FILLGAPS,
        solnorm=_SOLNORM,
        gaintable=bp_gaintable,
    )
    if uvrange:
        bandpass_kwargs["uvrange"] = uvrange

    casa_calls.append(
        f"casatasks.bandpass(field='{bp_field}', solint='inf', combine='scan', "
        f"fillgaps={_FILLGAPS}, refant='{ref_ant}') → BP0.b"
    )

    try:
        bandpass(**bandpass_kwargs)
    except Exception as e:
        raise InitialBandpassFailedError(
            f"bandpass failed with exception: {e}\n"
            f"Command: bandpass(vis='{ms_str}', caltable='{bp_table}', "
            f"field='{bp_field}', solint='inf', combine='scan', refant='{ref_ant}')",
            ms_path=ms_path,
        ) from e

    if not _table_exists(bp_table):
        raise InitialBandpassFailedError(
            f"bandpass did not produce BP0.b at '{bp_table}'. "
            "Possible causes: too few unflagged solutions, wrong field/scan selection, "
            f"or init_gain.g was produced but contained no valid solutions.",
            ms_path=ms_path,
        )

    # ------------------------------------------------------------------
    # Step 3 — applycal (all fields)
    # ------------------------------------------------------------------
    applycal_gaintable = priorcals + [init_gain_table, bp_table]
    n_tables = len(applycal_gaintable)

    casa_calls.append(
        f"casatasks.applycal(field='', gaintable=[...{n_tables} tables], "
        f"calwt=[False]*{n_tables}) → CORRECTED column populated"
    )

    try:
        applycal(
            vis=ms_str,
            field="",
            spw=all_spw,
            gaintable=applycal_gaintable,
            calwt=[False] * n_tables,
            flagbackup=False,
        )
    except Exception as e:
        warnings.append(
            f"applycal raised an exception: {e}. "
            "CORRECTED column may not be fully populated."
        )
        corrected_written = False
    else:
        corrected_written = True

    # ------------------------------------------------------------------
    # Build response
    # ------------------------------------------------------------------
    data = {
        "init_gain_table": fmt_field(init_gain_table),
        "bp_table": fmt_field(bp_table),
        "corrected_written": fmt_field(corrected_written),
        "n_prior_tables": len(priorcals),
        "ref_ant": ref_ant,
        "bp_field": bp_field,
        "solint_phase": "int",
        "solint_bp": "inf",
        "fillgaps": _FILLGAPS,
        "min_bl_per_ant": min_bl_per_ant,
    }
    if uvrange:
        data["uvrange"] = uvrange

    return response_envelope(
        tool_name=TOOL_NAME,
        ms_path=ms_path,
        data=data,
        warnings=warnings,
        casa_calls=casa_calls,
    )
