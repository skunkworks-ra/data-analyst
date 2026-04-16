"""
util/polcal_setjy_fit.py — Polynomial coefficient fitting for setjy(standard='manual').

Fits three models to tabulated calibrator data:

  1. Stokes I spectral index — log-polynomial:
       S(f) = S_ref * (f/f_ref)^(alpha + beta*log10(f/f_ref))
       → spix = [alpha, beta]   (CASA setjy convention)

  2. Polarization fraction — polynomial in (f - f_ref)/f_ref:
       P(f) = c_0 + c_1*x + c_2*x^2 + ...   where x = (f - f_ref)/f_ref
       → polindex = [c_0, c_1, ...]

  3. Polarization angle — same form, in radians:
       X(f) = c_0 + c_1*x + c_2*x^2 + ...
       → polangle = [c_0, c_1, ...]

COEFFICIENT ORDERING — critical correctness note:
  CASA setjy polindex/polangle expect ASCENDING power order: [c_0, c_1, c_2, ...].
  numpy.polyfit returns DESCENDING order:                    [c_n, ..., c_1, c_0].
  This module uses numpy.polynomial.polynomial.polyfit throughout, which natively
  returns ASCENDING order. Do NOT use numpy.polyfit here without reversing.

No CASA dependency. Requires numpy only.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# ---------------------------------------------------------------------------
# Output type
# ---------------------------------------------------------------------------


@dataclass
class SetjyPolParams:
    """All polynomial coefficients needed for setjy(standard='manual').

    Pass directly to CASA:
        setjy(
            vis=ms,
            field=field,
            standard='manual',
            fluxdensity=[flux_jy, 0, 0, 0],
            spix=spix,
            reffreq=f"{reffreq_ghz}GHz",
            polindex=polindex,
            polangle=polangle,
            scalebychan=True,
            usescratch=True,
        )
    """

    reffreq_ghz: float
    flux_jy: float  # Stokes I at reffreq
    spix: list[float]  # [alpha, beta] log-polynomial spectral index
    polindex: list[float]  # ascending polynomial in (f-fref)/fref  (fraction, 0-1)
    polangle: list[float]  # ascending polynomial in (f-fref)/fref  (radians)


# ---------------------------------------------------------------------------
# Individual fit functions
# ---------------------------------------------------------------------------


def fit_stokes_i(
    freq_ghz: np.ndarray,
    flux_jy: np.ndarray,
    reffreq_ghz: float,
) -> tuple[float, list[float]]:
    """Fit S(f) = S_ref * (f/f_ref)^(alpha + beta*log10(f/f_ref)).

    Linearises in log-log space:
        log10(S) = log10(S_ref) + alpha*x + beta*x^2   where x = log10(f/f_ref)

    Returns (flux_at_reffreq_jy, [alpha, beta]).
    """
    x = np.log10(freq_ghz / reffreq_ghz)
    y = np.log10(flux_jy)
    # Design matrix for [log10(S_ref), alpha, beta]
    A = np.column_stack([np.ones_like(x), x, x**2])
    coeffs, _, _, _ = np.linalg.lstsq(A, y, rcond=None)
    log_s_ref, alpha, beta = coeffs
    return float(10.0**log_s_ref), [float(alpha), float(beta)]


def fit_polindex(
    freq_ghz: np.ndarray,
    polfrac: np.ndarray,
    reffreq_ghz: float,
    deg: int = 3,
) -> list[float]:
    """Fit P(f) as a polynomial of degree `deg` in x = (f - f_ref)/f_ref.

    Returns coefficients [c_0, c_1, ...] in ASCENDING order (CASA convention).
    Uses numpy.polynomial.polynomial.polyfit which returns ascending order natively.
    """
    x = (freq_ghz - reffreq_ghz) / reffreq_ghz
    coeffs = np.polynomial.polynomial.polyfit(x, polfrac, deg)
    return [float(c) for c in coeffs]


def fit_polangle(
    freq_ghz: np.ndarray,
    polangle_rad: np.ndarray,
    reffreq_ghz: float,
    deg: int = 4,
) -> list[float]:
    """Fit X(f) as a polynomial of degree `deg` in x = (f - f_ref)/f_ref.

    Angle must be in radians. Returns [c_0, c_1, ...] ASCENDING (CASA convention).
    """
    x = (freq_ghz - reffreq_ghz) / reffreq_ghz
    coeffs = np.polynomial.polynomial.polyfit(x, polangle_rad, deg)
    return [float(c) for c in coeffs]


# ---------------------------------------------------------------------------
# Main entry point — raw arrays
# ---------------------------------------------------------------------------


def fit_setjy_params(
    freq_ghz: list[float] | np.ndarray,
    flux_jy: list[float | None] | np.ndarray,
    polfrac: list[float | None] | np.ndarray,
    polangle_deg: list[float | None] | np.ndarray,
    reffreq_ghz: float,
    flux_freq_range_ghz: tuple[float, float] | None = None,
    pol_freq_range_ghz: tuple[float, float] | None = None,
    spix_deg: int = 2,
    polindex_deg: int = 3,
    polangle_poly_deg: int = 4,
) -> SetjyPolParams:
    """Fit all three polynomial models from tabulated calibrator measurements.

    Args:
        freq_ghz:             Frequency nodes (GHz), any order.
        flux_jy:              Stokes I flux at each node (Jy). None entries excluded.
        polfrac:              Linear pol fraction, 0–1 scale (not percent).
                              None entries excluded from pol fits.
        polangle_deg:         Pol angle in degrees. None entries excluded from
                              polangle fit (e.g. RM-wrapped L-band nodes for 3C48).
        reffreq_ghz:          Reference frequency for polynomial expansions.
        flux_freq_range_ghz:  Optional (lo, hi) GHz band to restrict Stokes I fit.
        pol_freq_range_ghz:   Optional (lo, hi) GHz band to restrict pol fits.
        spix_deg:             Degree of log-polynomial for Stokes I (default 2).
        polindex_deg:         Degree of polindex polynomial (default 3).
        polangle_poly_deg:    Degree of polangle polynomial (default 4).

    Returns:
        SetjyPolParams with coefficients ready for setjy(standard='manual').
    """
    freq = np.array(freq_ghz, dtype=float)

    def _to_float_array(seq: list) -> np.ndarray:
        return np.array([v if v is not None else float("nan") for v in seq], dtype=float)

    # --- Stokes I ---
    flux = _to_float_array(flux_jy)
    mask_i = ~np.isnan(flux)
    if flux_freq_range_ghz is not None:
        lo, hi = flux_freq_range_ghz
        mask_i &= (freq >= lo) & (freq <= hi)
    if mask_i.sum() < 3:
        raise ValueError(f"Stokes I fit needs ≥3 valid nodes; got {mask_i.sum()} after filtering.")
    flux_at_ref, spix = fit_stokes_i(freq[mask_i], flux[mask_i], reffreq_ghz)

    # --- Pol fraction ---
    pf = _to_float_array(polfrac)
    mask_p = ~np.isnan(pf)
    if pol_freq_range_ghz is not None:
        lo, hi = pol_freq_range_ghz
        mask_p &= (freq >= lo) & (freq <= hi)
    if mask_p.sum() < polindex_deg + 1:
        raise ValueError(
            f"polindex fit (deg {polindex_deg}) needs ≥{polindex_deg + 1} valid nodes; "
            f"got {mask_p.sum()}."
        )
    polindex_coeffs = fit_polindex(freq[mask_p], pf[mask_p], reffreq_ghz, deg=polindex_deg)

    # --- Pol angle ---
    pa_deg_arr = _to_float_array(polangle_deg)
    pa_rad = np.where(np.isnan(pa_deg_arr), float("nan"), np.radians(pa_deg_arr))
    mask_a = ~np.isnan(pa_rad)
    if pol_freq_range_ghz is not None:
        lo, hi = pol_freq_range_ghz
        mask_a &= (freq >= lo) & (freq <= hi)
    if mask_a.sum() < polangle_poly_deg + 1:
        raise ValueError(
            f"polangle fit (deg {polangle_poly_deg}) needs ≥{polangle_poly_deg + 1} valid nodes; "
            f"got {mask_a.sum()}."
        )
    polangle_coeffs = fit_polangle(freq[mask_a], pa_rad[mask_a], reffreq_ghz, deg=polangle_poly_deg)

    return SetjyPolParams(
        reffreq_ghz=reffreq_ghz,
        flux_jy=flux_at_ref,
        spix=spix,
        polindex=polindex_coeffs,
        polangle=polangle_coeffs,
    )


# ---------------------------------------------------------------------------
# Convenience entry point — catalogue lookup
# ---------------------------------------------------------------------------


def fit_from_catalogue(
    calibrator_name: str,
    reffreq_ghz: float,
    epoch: str = "perley_butler_2013",
    flux_freq_range_ghz: tuple[float, float] | None = None,
    pol_freq_range_ghz: tuple[float, float] | None = None,
    polindex_deg: int = 3,
    polangle_deg: int = 4,
) -> SetjyPolParams:
    """Look up a calibrator in pol_calibrators.py and fit polynomial coefficients.

    Args:
        calibrator_name:     Any recognised name/alias (e.g. '3C48', 'J0137+3309').
        reffreq_ghz:         Reference frequency for the polynomial expansion.
        epoch:               Epoch key in the catalogue (default 'perley_butler_2013').
        flux_freq_range_ghz: Restrict Stokes I fit to this (lo, hi) GHz range.
        pol_freq_range_ghz:  Restrict pol fits to this (lo, hi) GHz range.
        polindex_deg:        Polynomial degree for pol fraction (default 3).
        polangle_deg:        Polynomial degree for pol angle (default 4).

    Returns:
        SetjyPolParams ready for setjy(standard='manual').

    Raises:
        KeyError:   calibrator not in catalogue, or epoch not present.
        ValueError: insufficient nodes for the requested polynomial degrees.

    Example — 3C48 at S-band (VLA, 2–4 GHz), reffreq 3.0 GHz:
        params = fit_from_catalogue(
            "3C48",
            reffreq_ghz=3.0,
            pol_freq_range_ghz=(2.0, 9.0),
        )
        # params.polindex[0] ≈ 0.022  (pol fraction at 3 GHz)
        # params.polangle[0] ≈ -1.688 (radians, pol angle at 3 GHz)
    """
    from ms_inspect.util.pol_calibrators import lookup_pol

    entry = lookup_pol(calibrator_name)
    if entry is None:
        raise KeyError(f"Calibrator {calibrator_name!r} not found in pol catalogue.")
    rows = entry.epochs.get(epoch)
    if not rows:
        raise KeyError(
            f"Epoch {epoch!r} not present for {calibrator_name!r}. "
            f"Available epochs: {list(entry.epochs)}"
        )

    rows_sorted = sorted(rows, key=lambda r: r.freq_ghz)
    freq_ghz = [r.freq_ghz for r in rows_sorted]
    flux_jy = [r.flux_jy for r in rows_sorted]
    polfrac = [r.frac_pol_pct / 100.0 if r.frac_pol_pct is not None else None for r in rows_sorted]
    polangle = [r.pol_angle_deg for r in rows_sorted]

    return fit_setjy_params(
        freq_ghz=freq_ghz,
        flux_jy=flux_jy,
        polfrac=polfrac,
        polangle_deg=polangle,
        reffreq_ghz=reffreq_ghz,
        flux_freq_range_ghz=flux_freq_range_ghz,
        pol_freq_range_ghz=pol_freq_range_ghz,
        polindex_deg=polindex_deg,
        polangle_poly_deg=polangle_deg,
    )
