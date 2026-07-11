"""
tools/image_stats.py — ms_image_stats

Post-imaging quality metrics from a CASA native image.

Reads the restored (or pbcor) image and returns:
  - rms_jy:           robust RMS (1.4826 × MAD, noise estimate)
  - peak_jy:          peak pixel value
  - dynamic_range:    abs(peak) / rms
  - beam_major_arcsec, beam_minor_arcsec, beam_pa_deg: restoring beam

For a multi-plane image (frequency cube and/or multi-Stokes, e.g. an IQUV
polarization cube) it additionally returns `n_planes` and `planes` — a
per-(Stokes, channel) list of rms_jy / peak_jy / dynamic_range. The scalar
fields above remain whole-image summaries. Numbers only; interpretation
(fractional polarization, spectral flatness) belongs in the skill.

All parameters are read from the image header and pixel data;
no MS access is performed.
"""

from __future__ import annotations

from itertools import product
from pathlib import Path

from ms_inspect.util.casa_context import open_image
from ms_inspect.util.formatting import field as fmt_field
from ms_inspect.util.formatting import response_envelope

TOOL_NAME = "ms_image_stats"

_MAD_TO_SIGMA = 1.4826

# Peak-to-noise (dynamic range) verdict thresholds.
#   <= 5        : marginal detection, FAIL
#   > 5, < 10   : marginal detection, pass
#   >= 10       : detection, pass
_P2N_FAIL = 5.0
_P2N_MARGINAL = 10.0


def _classify_detection(dr: float | None) -> tuple[str, bool]:
    """(label, passed) from a peak-to-noise (dynamic range) value."""
    if dr is None:
        return "unknown", False
    if dr <= _P2N_FAIL:
        return "marginal", False
    if dr < _P2N_MARGINAL:
        return "marginal", True
    return "detection", True


def _plane_labels(
    remaining_axes: list[int],
    remaining_shape: list[int],
    stokes_pix_axis: int | None,
    spec_pix_axis: int | None,
    stokes_names: list[str] | None = None,
) -> list[dict]:
    """Label each (stokes, channel) plane in C-order over the non-spatial axes.

    `remaining_axes` are the image pixel axes left after collapsing the two
    spatial axes (e.g. [2, 3] for a [RA, Dec, Stokes, Freq] image), in image
    order. `remaining_shape` is their lengths in the same order. The returned
    list is aligned with itertools.product(*[range(n) for n in remaining_shape])
    — i.e. C-order, matching how a CASA statistics array flattens.

    stokes_pix_axis / spec_pix_axis are the image pixel-axis numbers for the
    Stokes and spectral coordinates (or None if absent). stokes_names, if given,
    maps a Stokes pixel index to its label ('I', 'Q', ...).
    """
    labels: list[dict] = []
    for combo in product(*[range(n) for n in remaining_shape]):
        entry: dict = {}
        for axis, idx in zip(remaining_axes, combo, strict=True):
            if axis == stokes_pix_axis:
                entry["stokes_index"] = idx
                if stokes_names is not None and idx < len(stokes_names):
                    entry["stokes"] = stokes_names[idx]
            elif axis == spec_pix_axis:
                entry["chan"] = idx
        labels.append(entry)
    return labels


def _find_pixel_axis(csys, kind: str) -> int | None:
    """Return the image pixel-axis number for a coordinate kind, or None.

    Wraps coordsys.findcoordinate(), whose casatools return shape is a record
    with a 'return' flag and a 'pixel' array. Defensive against API variation.
    """
    try:
        rec = csys.findcoordinate(kind)
    except Exception:
        return None
    if not isinstance(rec, dict):
        return None
    if not rec.get("return", True):
        return None
    pix = rec.get("pixel")
    if pix is None or len(pix) == 0:
        return None
    return int(pix[0])


def _per_plane_stats(ia, casa_calls: list[str], warnings: list[str]) -> list[dict] | None:
    """Per-(Stokes, channel) rms/peak/DR for a multi-plane image.

    Returns None for a single-plane image (the scalar path handles it) or when
    the image shape / statistics cannot be resolved.
    """
    try:
        shape = [int(n) for n in ia.shape()]
    except Exception:
        return None
    if len(shape) <= 2:
        return None
    n_planes = 1
    for n in shape[2:]:
        n_planes *= n
    if n_planes <= 1:
        return None

    remaining_axes = list(range(2, len(shape)))
    remaining_shape = [shape[a] for a in remaining_axes]

    stokes_pix_axis = spec_pix_axis = None
    stokes_names: list[str] | None = None
    try:
        csys = ia.coordsys()
        casa_calls.append("ia.coordsys() [plane axis identification]")
        stokes_pix_axis = _find_pixel_axis(csys, "stokes")
        spec_pix_axis = _find_pixel_axis(csys, "spectral")
        try:
            sn = csys.stokes()
            stokes_names = [str(s) for s in sn] if sn else None
        except Exception:
            stokes_names = None
    except Exception as exc:
        warnings.append(f"Could not read coordinate system for plane labels: {exc}")

    try:
        sr = ia.statistics(axes=[0, 1], robust=True)
        ss = ia.statistics(axes=[0, 1])
        casa_calls.append("ia.statistics(axes=[0,1]) [per-plane]")
    except Exception as exc:
        warnings.append(f"Per-plane statistics failed: {exc}")
        return None

    import numpy as np

    mad = np.asarray(sr.get("medabsdevmed"), dtype=float).reshape(-1)
    mx = np.asarray(ss.get("max"), dtype=float).reshape(-1)
    if mad.size != n_planes or mx.size != n_planes:
        warnings.append(
            f"Per-plane statistics array size ({mad.size}/{mx.size}) does not match "
            f"plane count ({n_planes}); skipping per-plane breakdown."
        )
        return None

    labels = _plane_labels(
        remaining_axes, remaining_shape, stokes_pix_axis, spec_pix_axis, stokes_names
    )
    planes: list[dict] = []
    for lab, mad_v, max_v in zip(labels, mad, mx, strict=True):
        rms_v = _MAD_TO_SIGMA * float(mad_v)
        peak_v = float(max_v)
        dr = abs(peak_v) / rms_v if rms_v > 0 else None
        det_label, det_pass = _classify_detection(dr)
        entry = dict(lab)
        entry["rms_jy"] = round(rms_v, 9)
        entry["peak_jy"] = round(peak_v, 9)
        entry["dynamic_range"] = round(dr, 1) if dr is not None else None
        entry["detection"] = det_label
        entry["detection_pass"] = det_pass
        planes.append(entry)
    return planes


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

        # Per-plane breakdown for cubes / multi-Stokes images (None otherwise).
        planes = _per_plane_stats(ia, casa_calls, warnings)

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
    det_label, det_pass = _classify_detection(dynamic_range)
    if dynamic_range is not None and not det_pass:
        warnings.append(
            f"peak-to-noise {round(dynamic_range, 1)} <= {_P2N_FAIL:g}: marginal "
            "detection, FAIL. The peak is consistent with a residual/sidelobe "
            "spike, not a source — do not report this as a detection."
        )

    data: dict = {
        "image_path": fmt_field(str(Path(image_path).expanduser().resolve())),
        "rms_jy": fmt_field(round(rms_val, 9)),
        "peak_jy": fmt_field(round(peak_val, 9)),
        "dynamic_range": fmt_field(
            round(dynamic_range, 1) if dynamic_range is not None else None,
            flag="COMPLETE" if dynamic_range is not None else "UNAVAILABLE",
        ),
        "detection": fmt_field(det_label),
        "detection_pass": fmt_field(
            det_pass, flag="COMPLETE" if dynamic_range is not None else "UNAVAILABLE"
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

    # Per-plane breakdown (cube / multi-Stokes). The scalar fields above are
    # whole-image summaries; `planes` carries per-(Stokes, channel) values.
    if planes is not None:
        data["n_planes"] = fmt_field(len(planes))
        data["planes"] = fmt_field(planes)

    return response_envelope(
        tool_name=TOOL_NAME,
        ms_path=image_path,
        data=data,
        warnings=warnings,
        casa_calls=casa_calls,
    )
