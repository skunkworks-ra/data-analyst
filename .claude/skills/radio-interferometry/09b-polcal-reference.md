# Polcal reference — setjy model conventions

Reference material for `09-polcal-execution.md` Step 1. Read this when setting or
debugging a polarisation calibrator model with `setjy(standard='manual')`.

## The three model terms follow DIFFERENT polynomial forms — do not conflate them

This is the single most common source of error in `setjy(standard='manual')`.
Stokes I and the two polarisation terms are expanded in **different variables**
and **different functional forms**. From the CASA `setjy` definition:

| Term | Quantity | Functional form | Expansion variable |
|------|----------|-----------------|--------------------|
| `spix` | Stokes I | `S(f) = fluxdensity[0] * (f/reffreq)^(spix[0] + spix[1]*log(f/reffreq) + …)` | `log(f/reffreq)` — **log**, in the **exponent** (power law) |
| `polindex` | linear pol fraction `√(Q²+U²)/I` | `p(f) = c0 + c1·x + c2·x² + …` | `x = (f − reffreq)/reffreq` — **linear** fractional offset |
| `polangle` | pol angle in radians `0.5·arctan(U/Q)` | `χ(f) = d0 + d1·x + d2·x² + …` | `x = (f − reffreq)/reffreq` — **linear** fractional offset |

Consequences to keep straight:

- **Stokes I is a power law**, not an ordinary polynomial. `spix[0]` is the
  spectral index *at reffreq*; `spix[1]` is curvature. It is expanded in
  `log(f/reffreq)`. Fit Stokes I in log–log space.
- **`polindex`/`polangle` are ordinary Taylor polynomials** in the *linear*
  offset `(f − reffreq)/reffreq` — **not** log, **not** in the exponent. Fit them
  directly in that linear variable. `c0`/`d0` are the values at reffreq.
- The three are independent fits with independent degrees. A good Stokes I
  curvature term says nothing about the pol-fraction curvature, and vice versa.
- Coefficient arrays are **ascending order** `[c0, c1, c2, …]`. `numpy.polyfit`
  returns descending — `fit_from_catalogue` uses `numpy.polynomial.polynomial`
  which is ascending natively. Do not reverse by hand.
- With `fluxdensity=[I,0,0,0]` (Q=U=0), `polindex[0]` and `polangle[0]` set Q and
  U at reffreq. If Q/U are given non-zero in `fluxdensity`, the `c0`/`d0` entries
  are ignored — so always pass Q=U=0 when supplying `polindex`/`polangle`.

## All three are PER-BAND, not global wideband polynomials

`spix`, `polindex` and `polangle` are *local* expansions about `reffreq`, applied
across the channels being scaled. Do **not** pass a fit made across all bands
(e.g. a 1–50 GHz fit of the 17-node table) as the coefficients — the in-band
slope at, say, L-band is not the global polynomial's coefficients.

Procedure:

1. Set `reffreq` inside the observing band (band centre is fine).
2. Restrict every fit to the **in-band** nodes (`flux_freq_range_ghz` and
   `pol_freq_range_ghz` bound to the spectral window edges).
3. Take **as many terms as the in-band nodes support** — typically index +
   curvature for Stokes I, low-order for pol. L-band 3C286 (2019 epoch) has three
   in-band nodes (1.022, 1.465, 1.865 GHz) → degree 2 is the most you can fit.

`ms_setjy_polcal` does this automatically when given the band edges; never
hand-write the coefficients.

## Data provenance

Both the flux and the polarisation nodes come from the **same** NRAO VLA
Observing Guide tables (flux-density-scale / polarization-leakage /
polarization-angle), per-calibrator files `3c286_2019` etc. (R. Perley,
31 Jan/01 Feb 2019). Stokes I (Jy), pol fraction (P.F.) and pol angle (P.A.,
radians) are tabulated together at one epoch, so a single epoch is internally
consistent. These are stored in the same `pol_calibrators.py` catalogue rows
(`flux_jy`, `frac_pol_pct`, `pol_angle_deg`) and fit on the fly per band.
