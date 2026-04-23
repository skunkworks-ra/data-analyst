"""
tools/image_stats.py — ms_image_stats

Post-imaging quality metrics from a CASA native image.

Reads the restored (or pbcor) image and returns:
  - rms_jy:           robust RMS (1.4826 × MAD, noise estimate)
  - peak_jy:          peak pixel value
  - dynamic_range:    abs(peak) / rms
  - beam_major_arcsec, beam_minor_arcsec, beam_pa_deg: restoring beam

All parameters are read from the image header and pixel data;
no MS access is performed.
"""

from __future__ import annotations

from pathlib import Path

from ms_inspect.util.casa_context import open_image
from ms_inspect.util.formatting import field as fmt_field
from ms_inspect.util.formatting import response_envelope

TOOL_NAME = "ms_image_stats"

_MAD_TO_SIGMA = 1.4826


def _extract_beam(beam_info: dict) -> tuple[float | None, float | None, float | None]:
    """Return (major_arcsec, minor_arcsec, pa_deg) from ia.restoringbeam() output."""
    if "major" in beam_info:
        major = float(beam_info["major"]["value"])
        minor = float(beam_info["minor"]["value"])
        pa = float(beam_info["positionangle"]["value"])
        return major, minor, pa

    # Multi-beam image (cube or mtmfs): use channel 0 as representative.
    try:
        first = beam_info["beams"]["*0"]["*0"]
        return (
            float(first["major"]["value"]),
            float(first["minor"]["value"]),
            float(first["positionangle"]["value"]),
        )
    except (KeyError, TypeError):
        return None, None, None


def run(
    image_path: str,
    psf_path: str | None = None,
) -> dict:
    """
    Compute quality metrics for a CASA image.

    Uses ia.statistics(robust=True) for a MAD-based noise estimate that is
    insensitive to residual source flux in the image plane.

    Args:
        image_path: Path to the CASA image directory (e.g. imagename.image.pbcor).
        psf_path:   Optional path to the PSF image (imagename.psf).
                    If provided, the restoring beam is also read from the PSF
                    header as a cross-check; the primary beam is always taken
                    from image_path.

    Returns:
        Standard response envelope with rms_jy, peak_jy, dynamic_range,
        beam_major_arcsec, beam_minor_arcsec, beam_pa_deg.
    """
    casa_calls: list[str] = []
    warnings: list[str] = []

    # ------------------------------------------------------------------
    # Open primary image
    # ------------------------------------------------------------------
    with open_image(image_path) as ia:
        # Robust statistics: MAD-based noise estimate.
        stats_robust = ia.statistics(robust=True)
        casa_calls.append(f"ia.statistics(robust=True) on {Path(image_path).name}")

        mad_arr = stats_robust.get("medabsdevmed")
        if mad_arr is None or len(mad_arr) == 0:
            warnings.append(
                "ia.statistics(robust=True) did not return medabsdevmed; falling back to rms."
            )
            stats_simple = ia.statistics()
            rms_val = float(stats_simple["rms"][0])
            casa_calls.append("ia.statistics() fallback for rms")
        else:
            rms_val = _MAD_TO_SIGMA * float(mad_arr[0])

        # Peak: use simple statistics for max pixel value.
        stats_simple = ia.statistics()
        casa_calls.append(f"ia.statistics() on {Path(image_path).name}")
        peak_val = float(stats_simple["max"][0])

        # Beam from image header.
        try:
            beam_info = ia.restoringbeam()
            casa_calls.append("ia.restoringbeam()")
            beam_major, beam_minor, beam_pa = _extract_beam(beam_info)
        except Exception as exc:
            warnings.append(f"Could not read restoring beam from image: {exc}")
            beam_major = beam_minor = beam_pa = None

    # ------------------------------------------------------------------
    # Optional PSF beam cross-check
    # ------------------------------------------------------------------
    psf_beam_major: float | None = None
    if psf_path is not None:
        psf_p = Path(psf_path).expanduser().resolve()
        if not psf_p.exists():
            warnings.append(f"psf_path does not exist: {psf_path} — skipping PSF beam read.")
        else:
            try:
                with open_image(str(psf_p)) as ia_psf:
                    psf_beam_info = ia_psf.restoringbeam()
                    casa_calls.append(f"ia.restoringbeam() on {psf_p.name}")
                psf_beam_major, psf_beam_minor, psf_beam_pa = _extract_beam(psf_beam_info)
            except Exception as exc:
                warnings.append(f"Could not read beam from PSF image: {exc}")

    # ------------------------------------------------------------------
    # Dynamic range
    # ------------------------------------------------------------------
    if rms_val > 0:
        dynamic_range = abs(peak_val) / rms_val
    else:
        dynamic_range = None
        warnings.append("RMS is zero or negative; dynamic range not computable.")

    # ------------------------------------------------------------------
    # Build response
    # ------------------------------------------------------------------
    data: dict = {
        "image_path": fmt_field(str(Path(image_path).expanduser().resolve())),
        "rms_jy": fmt_field(round(rms_val, 9)),
        "peak_jy": fmt_field(round(peak_val, 9)),
        "dynamic_range": fmt_field(
            round(dynamic_range, 1) if dynamic_range is not None else None,
            flag="COMPLETE" if dynamic_range is not None else "UNAVAILABLE",
        ),
    }

    if beam_major is not None:
        data["beam_major_arcsec"] = fmt_field(round(beam_major, 4))
        data["beam_minor_arcsec"] = fmt_field(round(beam_minor, 4))
        data["beam_pa_deg"] = fmt_field(round(beam_pa, 2))
    else:
        data["beam_major_arcsec"] = fmt_field(
            None, flag="UNAVAILABLE", note="beam not found in image header"
        )
        data["beam_minor_arcsec"] = fmt_field(None, flag="UNAVAILABLE")
        data["beam_pa_deg"] = fmt_field(None, flag="UNAVAILABLE")

    if psf_path is not None and psf_beam_major is not None:
        data["psf_beam_major_arcsec"] = fmt_field(round(psf_beam_major, 4))
        data["psf_beam_minor_arcsec"] = fmt_field(round(psf_beam_minor, 4))
        data["psf_beam_pa_deg"] = fmt_field(round(psf_beam_pa, 2))

    return response_envelope(
        tool_name=TOOL_NAME,
        ms_path=image_path,
        data=data,
        warnings=warnings,
        casa_calls=casa_calls,
    )
