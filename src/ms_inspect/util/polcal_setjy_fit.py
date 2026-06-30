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

import logging
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)

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
    deg: int = 2,
) -> tuple[float, list[float]]:
    """Fit S(f) = S_ref * (f/f_ref)^(spix[0] + spix[1]*log10(f/f_ref) + ...).

    Linearises in log-log space:
        log10(S) = log10(S_ref) + spix[0]*x + spix[1]*x^2 + ...   x = log10(f/f_ref)

    The fit degree is capped at the number of nodes minus one, so it uses as many
    spix terms as the (in-band) data supports: with 2 nodes it returns just the
    spectral index [spix[0]]; with 3+ nodes it can also return curvature, etc.

    Returns (flux_at_reffreq_jy, spix) where spix has length = effective degree.
    """
    n = len(freq_ghz)
    eff_deg = max(1, min(deg, n - 1))
    x = np.log10(freq_ghz / reffreq_ghz)
    y = np.log10(flux_jy)
    # Design matrix columns: [1, x, x^2, ...] up to eff_deg
    A = np.column_stack([x**k for k in range(eff_deg + 1)])
    coeffs, _, _, _ = np.linalg.lstsq(A, y, rcond=None)
    log_s_ref = coeffs[0]
    spix = [float(c) for c in coeffs[1:]]
    return float(10.0**log_s_ref), spix


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

    # Each model is fit independently over its in-band nodes. The polynomial
    # degree is capped at (n_nodes - 1) so a band with only 2 nodes yields a
    # first-order fit rather than failing — "as many terms as the band supports".
    # A minimum of 2 nodes is required (a slope needs two points); a single node
    # gives no frequency information and is rejected.

    # --- Stokes I ---
    flux = _to_float_array(flux_jy)
    mask_i = ~np.isnan(flux)
    if flux_freq_range_ghz is not None:
        lo, hi = flux_freq_range_ghz
        mask_i &= (freq >= lo) & (freq <= hi)
    if mask_i.sum() < 2:
        raise ValueError(f"Stokes I fit needs ≥2 in-band nodes; got {mask_i.sum()} after filtering.")
    flux_at_ref, spix = fit_stokes_i(freq[mask_i], flux[mask_i], reffreq_ghz, deg=spix_deg)

    # --- Pol fraction ---
    pf = _to_float_array(polfrac)
    mask_p = ~np.isnan(pf)
    if pol_freq_range_ghz is not None:
        lo, hi = pol_freq_range_ghz
        mask_p &= (freq >= lo) & (freq <= hi)
    if mask_p.sum() < 2:
        raise ValueError(
            f"polindex fit needs ≥2 in-band nodes; got {mask_p.sum()} after filtering."
        )
    eff_polindex_deg = min(polindex_deg, int(mask_p.sum()) - 1)
    if eff_polindex_deg < polindex_deg:
        logger.warning(
            "polindex: only %d in-band node(s); clamping fit degree %d → %d.",
            int(mask_p.sum()),
            polindex_deg,
            eff_polindex_deg,
        )
    polindex_coeffs = fit_polindex(freq[mask_p], pf[mask_p], reffreq_ghz, deg=eff_polindex_deg)

    # --- Pol angle ---
    pa_deg_arr = _to_float_array(polangle_deg)
    pa_rad = np.where(np.isnan(pa_deg_arr), float("nan"), np.radians(pa_deg_arr))
    mask_a = ~np.isnan(pa_rad)
    if pol_freq_range_ghz is not None:
        lo, hi = pol_freq_range_ghz
        mask_a &= (freq >= lo) & (freq <= hi)
    if mask_a.sum() < 2:
        raise ValueError(
            f"polangle fit needs ≥2 in-band nodes; got {mask_a.sum()} after filtering."
        )
    eff_polangle_deg = min(polangle_poly_deg, int(mask_a.sum()) - 1)
    if eff_polangle_deg < polangle_poly_deg:
        logger.warning(
            "polangle: only %d in-band node(s); clamping fit degree %d → %d.",
            int(mask_a.sum()),
            polangle_poly_deg,
            eff_polangle_deg,
        )
    polangle_coeffs = fit_polangle(freq[mask_a], pa_rad[mask_a], reffreq_ghz, deg=eff_polangle_deg)

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


def fit_stokes_i_adaptive(
    freq_ghz: list[float] | np.ndarray,
    flux_jy: list[float] | np.ndarray,
    reffreq_ghz: float,
) -> tuple[float, list[float]]:
    """Fit the Stokes I log-polynomial with a degree adapted to the node count.

    Same model as ``fit_stokes_i`` — log10(S) = log10(S_ref) + alpha*x + beta*x^2,
    x = log10(f/f_ref) — but the polynomial degree is chosen from the number of
    available frequency samples so the probe works with a single SPW (1 point)
    up to a wide multi-SPW band:

        ≥3 points → degree 2 → spix = [alpha, beta]
         2 points → degree 1 → spix = [alpha]
         1 point  → degree 0 → spix = [0.0]  (flat; no slope determinable)

    Returns (flux_at_reffreq_jy, spix).
    """
    freq = np.asarray(freq_ghz, dtype=float)
    flux = np.asarray(flux_jy, dtype=float)
    n = freq.size
    if n < 1:
        raise ValueError("fit_stokes_i_adaptive needs ≥1 frequency sample; got 0.")
    if np.any(flux <= 0):
        raise ValueError("Stokes I flux samples must be positive (log fit).")
    x = np.log10(freq / reffreq_ghz)
    y = np.log10(flux)
    deg = min(2, n - 1)
    # Vandermonde in ascending powers: columns [1, x, x^2, ...]
    A = np.vander(x, deg + 1, increasing=True)
    coeffs, _, _, _ = np.linalg.lstsq(A, y, rcond=None)
    flux_at_ref = float(10.0 ** coeffs[0])
    spix = [float(c) for c in coeffs[1:]] if deg >= 1 else [0.0]
    return flux_at_ref, spix


def resolve_epoch(entry, epoch: str | None, obs_epoch_year: float | None) -> str:
    """Resolve a catalogue epoch key, auto-selecting when ``epoch`` is None.

    Epoch keys carry a 4-digit year (e.g. '2019'); pick the one nearest
    ``obs_epoch_year``, or the latest year when no observation date is given.
    A non-None ``epoch`` is returned unchanged (validated downstream).
    """
    if epoch is not None:
        return epoch

    import re

    def _epoch_year(key: str) -> int:
        m = re.search(r"(\d{4})", key)
        return int(m.group(1)) if m else 0

    if obs_epoch_year is not None:
        return min(entry.epochs, key=lambda k: abs(_epoch_year(k) - obs_epoch_year))
    return max(entry.epochs, key=_epoch_year)


def fit_pol_terms_from_catalogue(
    calibrator_name: str,
    reffreq_ghz: float,
    epoch: str | None = None,
    pol_freq_range_ghz: tuple[float, float] | None = None,
    polindex_deg: int = 3,
    polangle_deg: int = 4,
    obs_epoch_year: float | None = None,
) -> tuple[list[float], list[float]]:
    """Fit only the polarization terms (polindex, polangle) from the catalogue.

    The Stokes I flux/spix are NOT fit here — they come from a Perley-Butler
    setjy probe at run time (the pol-property tables, e.g. the 2019 NRAO epoch,
    tabulate fractional polarization and angle only, no Stokes I). This is the
    pure-numpy half of ms_setjy_polcal; it has no CASA dependency.

    Returns (polindex, polangle), both ASCENDING-order coefficient lists.

    Raises:
        KeyError:   calibrator not in catalogue, or epoch not present.
        ValueError: insufficient nodes for the requested polynomial degrees.
    """
    from ms_inspect.util.pol_calibrators import lookup_pol

    entry = lookup_pol(calibrator_name)
    if entry is None:
        raise KeyError(f"Calibrator {calibrator_name!r} not found in pol catalogue.")
    epoch = resolve_epoch(entry, epoch, obs_epoch_year)
    rows = entry.epochs.get(epoch)
    if not rows:
        raise KeyError(
            f"Epoch {epoch!r} not present for {calibrator_name!r}. "
            f"Available epochs: {list(entry.epochs)}"
        )

    rows_sorted = sorted(rows, key=lambda r: r.freq_ghz)
    freq = np.array([r.freq_ghz for r in rows_sorted], dtype=float)
    pf = np.array(
        [
            r.frac_pol_pct / 100.0 if r.frac_pol_pct is not None else float("nan")
            for r in rows_sorted
        ],
        dtype=float,
    )
    pa = np.array(
        [
            np.radians(r.pol_angle_deg) if r.pol_angle_deg is not None else float("nan")
            for r in rows_sorted
        ],
        dtype=float,
    )

    def _band_mask(values: np.ndarray) -> np.ndarray:
        mask = ~np.isnan(values)
        if pol_freq_range_ghz is not None:
            lo, hi = pol_freq_range_ghz
            mask &= (freq >= lo) & (freq <= hi)
        return mask

    # Try the requested degree first; when the in-band node count cannot support
    # it, clamp the degree down to (n_nodes - 1) and warn rather than failing. A
    # hard floor of 2 nodes is kept — a single node carries no frequency
    # information so no slope is determinable. (3C286 restricted to L-band has
    # only 3 pol nodes at 1.02/1.47/1.87 GHz, below the default deg 3/4.)
    n_p = int(_band_mask(pf).sum())
    if n_p < 2:
        raise ValueError(f"polindex fit needs ≥2 in-band nodes; got {n_p}.")
    eff_polindex_deg = min(polindex_deg, n_p - 1)
    if eff_polindex_deg < polindex_deg:
        logger.warning(
            "polindex: only %d in-band node(s); clamping fit degree %d → %d.",
            n_p,
            polindex_deg,
            eff_polindex_deg,
        )
    mask_p = _band_mask(pf)
    polindex = fit_polindex(freq[mask_p], pf[mask_p], reffreq_ghz, deg=eff_polindex_deg)

    n_a = int(_band_mask(pa).sum())
    if n_a < 2:
        raise ValueError(f"polangle fit needs ≥2 in-band nodes; got {n_a}.")
    eff_polangle_deg = min(polangle_deg, n_a - 1)
    if eff_polangle_deg < polangle_deg:
        logger.warning(
            "polangle: only %d in-band node(s); clamping fit degree %d → %d.",
            n_a,
            polangle_deg,
            eff_polangle_deg,
        )
    mask_a = _band_mask(pa)
    polangle = fit_polangle(freq[mask_a], pa[mask_a], reffreq_ghz, deg=eff_polangle_deg)

    return polindex, polangle


def fit_from_catalogue(
    calibrator_name: str,
    reffreq_ghz: float,
    epoch: str | None = None,
    flux_freq_range_ghz: tuple[float, float] | None = None,
    pol_freq_range_ghz: tuple[float, float] | None = None,
    polindex_deg: int = 3,
    polangle_deg: int = 4,
    spix_deg: int = 2,
    obs_epoch_year: float | None = None,
) -> SetjyPolParams:
    """Look up a calibrator in pol_calibrators.py and fit polynomial coefficients.

    Stokes I (flux at reffreq + spix) is taken from the bundled Perley-Butler 2017
    flux model when the calibrator is present there — this is the authoritative
    source and the only one available for 3C286, whose pol catalogue rows carry no
    tabulated flux. If the calibrator is absent from PB2017, Stokes I falls back to
    a log-polynomial fit of the catalogue's own flux_jy nodes (e.g. legacy epochs).

    Args:
        calibrator_name:     Any recognised name/alias (e.g. '3C48', 'J0137+3309').
        reffreq_ghz:         Reference frequency for the polynomial expansion.
        epoch:               Epoch key in the catalogue. None (default) auto-selects
                             the epoch nearest obs_epoch_year (latest if unset).
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

    epoch = resolve_epoch(entry, epoch, obs_epoch_year)
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
        spix_deg=spix_deg,
        polindex_deg=polindex_deg,
        polangle_poly_deg=polangle_deg,
    )
