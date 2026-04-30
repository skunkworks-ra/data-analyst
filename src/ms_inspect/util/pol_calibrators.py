"""
util/pol_calibrators.py — Bundled VLA polarisation calibrator reference data.

Covers primary pol angle and leakage calibrators for the VLA.
MeerKAT pol calibrators are a separate design (out of scope for Phase 1).

Source: NRAO VLA Observing Guide Tables 8.2.1–8.2.7 and evlapolcal/index.html
        (scraped March 2026, committed verbatim).

Determinism guarantee: static data, no live web fetch, no CASA dependency.

Used by:
- tools/pol_cal_feasibility.py — feasibility assessment
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class PolFreqEntry:
    freq_ghz: float
    frac_pol_pct: float | None  # None = upper limit only
    frac_pol_upper_limit: bool  # True when frac_pol_pct is an upper bound
    pol_angle_deg: float | None  # None = unmeasurable / unstable at this freq
    flux_jy: float | None = None  # Stokes I flux density; None if not tabulated


@dataclass
class PolCalEntry:
    j2000_name: str  # "J1331+3030"
    b1950_name: str  # "3C286"
    category: str  # "A", "B", "C", "D"
    role: list[str]  # ["angle"], ["leakage"], ["angle", "leakage"]
    stable_pa: bool  # True = PA reliable across bands (only 3C286)
    single_scan_sufficient: bool  # True for Cat C — known low-pol, no PA coverage needed
    variability_note: str | None  # e.g. "in flare Jan 2025 at K/Ka/Q"
    epochs: dict[str, list[PolFreqEntry]]  # {"2010": [...], "2019": [...]}
    aka: list[str]  # alternative names for field matching


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------
#
# Epoch 2019 pol properties from NRAO VLA Observing Guide Table 8.2 series.
# Frequency nodes correspond to the VLA band centres (P, L, S, C, X, Ku, K,
# Ka, Q bands). Values not listed in the guide are absent (entry omitted for
# that frequency).
#
# Notes on specific sources:
#   3C286 (J1331+3030): Category A. Stable PA ~33° from 1–48 GHz.
#                        Gold standard for both angle and leakage.
#   3C138 (J0521+1638): Category A. PA varies by band. In flare Jan 2025
#                        at K/Ka/Q — use 3C286 or 3C48 at those bands.
#   3C48  (J0137+3309):  Category A. PA rotates wildly below 4 GHz;
#                        frac_pol <1% and pol_angle=None below 4 GHz.
#   3C147 (J0542+4951):  Category C. <0.05% pol below 10 GHz — leakage only.
#                        Rises ~5% above 10 GHz (less useful as leakage cal).
#   3C84  (J0319+4130):  Category C. Low pol, bright, monitored.
#   NRAO150 / BL Lac / 3C454.3: Category B secondaries, variable.
#
# ---------------------------------------------------------------------------

POL_CATALOGUE: list[PolCalEntry] = [
    PolCalEntry(
        j2000_name="J1331+3030",
        b1950_name="3C286",
        category="A",
        role=["angle", "leakage"],
        stable_pa=True,
        single_scan_sufficient=False,
        variability_note=None,
        aka=["1331+305", "1331+3030", "3c286", "j1331+305"],
        epochs={
            # 17-node table from NRAO VLA Observing Guide Table 8.2.7 (January/February 2019).
            # Frequencies are actual measurement frequencies, not band centres.
            # Stokes I flux density not tabulated here — use calibrators.py / Perley-Butler 2017.
            "2019": [
                PolFreqEntry(
                    freq_ghz=1.02, frac_pol_pct=8.6, frac_pol_upper_limit=False, pol_angle_deg=33.0
                ),
                PolFreqEntry(
                    freq_ghz=1.47, frac_pol_pct=9.8, frac_pol_upper_limit=False, pol_angle_deg=33.0
                ),
                PolFreqEntry(
                    freq_ghz=1.87, frac_pol_pct=10.1, frac_pol_upper_limit=False, pol_angle_deg=33.0
                ),
                PolFreqEntry(
                    freq_ghz=2.57, frac_pol_pct=10.6, frac_pol_upper_limit=False, pol_angle_deg=33.0
                ),
                PolFreqEntry(
                    freq_ghz=3.57, frac_pol_pct=11.2, frac_pol_upper_limit=False, pol_angle_deg=33.0
                ),
                PolFreqEntry(
                    freq_ghz=4.89, frac_pol_pct=11.5, frac_pol_upper_limit=False, pol_angle_deg=33.0
                ),
                PolFreqEntry(
                    freq_ghz=6.68, frac_pol_pct=11.9, frac_pol_upper_limit=False, pol_angle_deg=33.0
                ),
                PolFreqEntry(
                    freq_ghz=8.43, frac_pol_pct=12.1, frac_pol_upper_limit=False, pol_angle_deg=33.0
                ),
                PolFreqEntry(
                    freq_ghz=11.3, frac_pol_pct=12.3, frac_pol_upper_limit=False, pol_angle_deg=34.0
                ),
                PolFreqEntry(
                    freq_ghz=14.1, frac_pol_pct=12.3, frac_pol_upper_limit=False, pol_angle_deg=34.0
                ),
                PolFreqEntry(
                    freq_ghz=16.6, frac_pol_pct=12.5, frac_pol_upper_limit=False, pol_angle_deg=35.0
                ),
                PolFreqEntry(
                    freq_ghz=19.1, frac_pol_pct=12.6, frac_pol_upper_limit=False, pol_angle_deg=35.0
                ),
                PolFreqEntry(
                    freq_ghz=25.6, frac_pol_pct=12.7, frac_pol_upper_limit=False, pol_angle_deg=36.0
                ),
                PolFreqEntry(
                    freq_ghz=32.1, frac_pol_pct=13.1, frac_pol_upper_limit=False, pol_angle_deg=36.0
                ),
                PolFreqEntry(
                    freq_ghz=37.1, frac_pol_pct=13.5, frac_pol_upper_limit=False, pol_angle_deg=36.0
                ),
                PolFreqEntry(
                    freq_ghz=42.1, frac_pol_pct=13.4, frac_pol_upper_limit=False, pol_angle_deg=37.0
                ),
                PolFreqEntry(
                    freq_ghz=48.1, frac_pol_pct=14.6, frac_pol_upper_limit=False, pol_angle_deg=36.0
                ),
            ],
        },
    ),
    PolCalEntry(
        j2000_name="J0521+1638",
        b1950_name="3C138",
        category="A",
        role=["angle"],
        stable_pa=False,
        single_scan_sufficient=False,
        variability_note="In flare Jan 2025 at K/Ka/Q bands — avoid until cleared",
        aka=["0518+165", "0521+1638", "3c138", "j0518+165"],
        epochs={
            "2019": [
                PolFreqEntry(
                    freq_ghz=1.45, frac_pol_pct=5.6, frac_pol_upper_limit=False, pol_angle_deg=-14.0
                ),
                PolFreqEntry(
                    freq_ghz=3.0, frac_pol_pct=7.5, frac_pol_upper_limit=False, pol_angle_deg=-11.0
                ),
                PolFreqEntry(
                    freq_ghz=6.0, frac_pol_pct=10.4, frac_pol_upper_limit=False, pol_angle_deg=-11.0
                ),
                PolFreqEntry(
                    freq_ghz=10.0, frac_pol_pct=9.0, frac_pol_upper_limit=False, pol_angle_deg=-12.0
                ),
                PolFreqEntry(
                    freq_ghz=15.0,
                    frac_pol_pct=10.6,
                    frac_pol_upper_limit=False,
                    pol_angle_deg=-10.0,
                ),
                PolFreqEntry(
                    freq_ghz=22.0, frac_pol_pct=9.9, frac_pol_upper_limit=False, pol_angle_deg=None
                ),
                PolFreqEntry(
                    freq_ghz=33.0, frac_pol_pct=10.0, frac_pol_upper_limit=False, pol_angle_deg=None
                ),
                PolFreqEntry(
                    freq_ghz=45.0, frac_pol_pct=9.7, frac_pol_upper_limit=False, pol_angle_deg=None
                ),
            ],
        },
    ),
    PolCalEntry(
        j2000_name="J0137+3309",
        b1950_name="3C48",
        category="A",
        role=["angle"],
        stable_pa=False,
        single_scan_sufficient=False,
        variability_note=None,
        aka=["0137+331", "0137+3309", "3c48", "j0137+331"],
        epochs={
            "2019": [
                # Below 4 GHz: polarisation is very low and PA is unstable/undefined.
                # Encode explicitly so callers can gate on these values.
                PolFreqEntry(
                    freq_ghz=0.35, frac_pol_pct=0.3, frac_pol_upper_limit=True, pol_angle_deg=None
                ),
                PolFreqEntry(
                    freq_ghz=1.45, frac_pol_pct=0.5, frac_pol_upper_limit=True, pol_angle_deg=None
                ),
                # Usable above 4 GHz:
                PolFreqEntry(
                    freq_ghz=6.0, frac_pol_pct=5.0, frac_pol_upper_limit=False, pol_angle_deg=-66.0
                ),
                PolFreqEntry(
                    freq_ghz=10.0, frac_pol_pct=6.0, frac_pol_upper_limit=False, pol_angle_deg=-67.0
                ),
                PolFreqEntry(
                    freq_ghz=15.0, frac_pol_pct=7.4, frac_pol_upper_limit=False, pol_angle_deg=-67.0
                ),
                PolFreqEntry(
                    freq_ghz=22.0, frac_pol_pct=7.5, frac_pol_upper_limit=False, pol_angle_deg=-68.0
                ),
                PolFreqEntry(
                    freq_ghz=33.0, frac_pol_pct=7.4, frac_pol_upper_limit=False, pol_angle_deg=-67.0
                ),
                PolFreqEntry(
                    freq_ghz=45.0, frac_pol_pct=8.5, frac_pol_upper_limit=False, pol_angle_deg=-71.0
                ),
            ],
            # 17-node table from Perley & Butler (2013), reproduced in the NRAO VLA
            # Polarization Calibration Guide (TDRW0001 example, March 2024).
            # Stokes I from the Perley-Butler 2017 flux scale; polfrac and polangle
            # measured contemporaneously.
            #
            # pol_angle_deg is stored in degrees, converted from the source radians.
            # Angles are NOT derotated for Faraday rotation (RM ≈ -68 rad/m² for 3C48).
            #
            # pol_angle_deg = None for the three lowest-frequency nodes:
            #   1.022 GHz — pol fraction < 0.3%, PA heavily rotated and near upper limit
            #   1.465 GHz — pol fraction < 0.5%, λ² large enough for near-full RM wrap
            #   1.865 GHz — PA wraps by ~π relative to higher frequencies;
            #               requires explicit π-correction before use in polynomial fitting
            #               (see NRAO polcal guide step: data[2,3] -= np.pi)
            # Angles at 2.565 GHz and above are stable and directly usable.
            "perley_butler_2013": [
                PolFreqEntry(
                    freq_ghz=1.022,
                    frac_pol_pct=0.293,
                    frac_pol_upper_limit=True,
                    pol_angle_deg=None,
                    flux_jy=20.68,
                ),
                PolFreqEntry(
                    freq_ghz=1.465,
                    frac_pol_pct=0.457,
                    frac_pol_upper_limit=True,
                    pol_angle_deg=None,
                    flux_jy=15.62,
                ),
                PolFreqEntry(
                    freq_ghz=1.865,
                    frac_pol_pct=0.897,
                    frac_pol_upper_limit=False,
                    pol_angle_deg=None,
                    flux_jy=12.88,
                ),
                PolFreqEntry(
                    freq_ghz=2.565,
                    frac_pol_pct=1.548,
                    frac_pol_upper_limit=False,
                    pol_angle_deg=-112.89,
                    flux_jy=9.82,
                ),
                PolFreqEntry(
                    freq_ghz=3.565,
                    frac_pol_pct=2.911,
                    frac_pol_upper_limit=False,
                    pol_angle_deg=-83.94,
                    flux_jy=7.31,
                ),
                PolFreqEntry(
                    freq_ghz=4.885,
                    frac_pol_pct=4.286,
                    frac_pol_upper_limit=False,
                    pol_angle_deg=-71.53,
                    flux_jy=5.48,
                ),
                PolFreqEntry(
                    freq_ghz=6.680,
                    frac_pol_pct=5.356,
                    frac_pol_upper_limit=False,
                    pol_angle_deg=-66.18,
                    flux_jy=4.12,
                ),
                PolFreqEntry(
                    freq_ghz=8.435,
                    frac_pol_pct=5.430,
                    frac_pol_upper_limit=False,
                    pol_angle_deg=-63.38,
                    flux_jy=3.34,
                ),
                PolFreqEntry(
                    freq_ghz=11.320,
                    frac_pol_pct=5.727,
                    frac_pol_upper_limit=False,
                    pol_angle_deg=-62.21,
                    flux_jy=2.56,
                ),
                PolFreqEntry(
                    freq_ghz=14.065,
                    frac_pol_pct=6.097,
                    frac_pol_upper_limit=False,
                    pol_angle_deg=-62.78,
                    flux_jy=2.14,
                ),
                PolFreqEntry(
                    freq_ghz=16.564,
                    frac_pol_pct=6.296,
                    frac_pol_upper_limit=False,
                    pol_angle_deg=-64.10,
                    flux_jy=1.86,
                ),
                PolFreqEntry(
                    freq_ghz=19.064,
                    frac_pol_pct=6.492,
                    frac_pol_upper_limit=False,
                    pol_angle_deg=-67.75,
                    flux_jy=1.67,
                ),
                PolFreqEntry(
                    freq_ghz=25.564,
                    frac_pol_pct=7.153,
                    frac_pol_upper_limit=False,
                    pol_angle_deg=-71.81,
                    flux_jy=1.33,
                ),
                PolFreqEntry(
                    freq_ghz=32.064,
                    frac_pol_pct=6.442,
                    frac_pol_upper_limit=False,
                    pol_angle_deg=-75.85,
                    flux_jy=1.11,
                ),
                PolFreqEntry(
                    freq_ghz=37.064,
                    frac_pol_pct=6.686,
                    frac_pol_upper_limit=False,
                    pol_angle_deg=-76.58,
                    flux_jy=1.00,
                ),
                PolFreqEntry(
                    freq_ghz=42.064,
                    frac_pol_pct=5.552,
                    frac_pol_upper_limit=False,
                    pol_angle_deg=-83.86,
                    flux_jy=0.92,
                ),
                PolFreqEntry(
                    freq_ghz=48.064,
                    frac_pol_pct=6.773,
                    frac_pol_upper_limit=False,
                    pol_angle_deg=-83.88,
                    flux_jy=0.82,
                ),
            ],
        },
    ),
    PolCalEntry(
        j2000_name="J0542+4951",
        b1950_name="3C147",
        category="C",
        role=["leakage"],
        stable_pa=False,
        single_scan_sufficient=True,
        variability_note=None,
        aka=["0538+498", "0542+4951", "3c147", "j0538+498"],
        epochs={
            "2019": [
                # <10 GHz: essentially unpolarised (<0.05%) — ideal leakage calibrator.
                # Rises to ~5% above 10 GHz where it is less useful.
                PolFreqEntry(
                    freq_ghz=1.45, frac_pol_pct=0.05, frac_pol_upper_limit=True, pol_angle_deg=None
                ),
                PolFreqEntry(
                    freq_ghz=3.0, frac_pol_pct=0.05, frac_pol_upper_limit=True, pol_angle_deg=None
                ),
                PolFreqEntry(
                    freq_ghz=6.0, frac_pol_pct=0.05, frac_pol_upper_limit=True, pol_angle_deg=None
                ),
                PolFreqEntry(
                    freq_ghz=10.0, frac_pol_pct=0.5, frac_pol_upper_limit=False, pol_angle_deg=None
                ),
                PolFreqEntry(
                    freq_ghz=15.0, frac_pol_pct=2.5, frac_pol_upper_limit=False, pol_angle_deg=None
                ),
                PolFreqEntry(
                    freq_ghz=22.0, frac_pol_pct=4.8, frac_pol_upper_limit=False, pol_angle_deg=None
                ),
                PolFreqEntry(
                    freq_ghz=33.0, frac_pol_pct=5.0, frac_pol_upper_limit=False, pol_angle_deg=None
                ),
                PolFreqEntry(
                    freq_ghz=45.0, frac_pol_pct=5.0, frac_pol_upper_limit=False, pol_angle_deg=None
                ),
            ],
        },
    ),
    PolCalEntry(
        j2000_name="J0319+4130",
        b1950_name="3C84",
        category="C",
        role=["leakage"],
        stable_pa=False,
        single_scan_sufficient=True,
        variability_note="Variable on month timescales — monitor before use at high freq",
        aka=["0316+413", "0319+4130", "3c84", "j0316+413", "ngc1275"],
        epochs={
            "2019": [
                PolFreqEntry(
                    freq_ghz=1.45, frac_pol_pct=0.4, frac_pol_upper_limit=False, pol_angle_deg=None
                ),
                PolFreqEntry(
                    freq_ghz=3.0, frac_pol_pct=0.6, frac_pol_upper_limit=False, pol_angle_deg=None
                ),
                PolFreqEntry(
                    freq_ghz=6.0, frac_pol_pct=1.5, frac_pol_upper_limit=False, pol_angle_deg=None
                ),
                PolFreqEntry(
                    freq_ghz=10.0, frac_pol_pct=2.0, frac_pol_upper_limit=False, pol_angle_deg=None
                ),
            ],
        },
    ),
    PolCalEntry(
        j2000_name="J1407+2827",
        b1950_name="OQ208",
        category="C",
        role=["leakage"],
        stable_pa=False,
        single_scan_sufficient=True,
        variability_note=None,
        aka=["1404+286", "1407+2827", "oq208", "j1404+286"],
        epochs={
            "2019": [
                PolFreqEntry(
                    freq_ghz=1.45, frac_pol_pct=0.1, frac_pol_upper_limit=True, pol_angle_deg=None
                ),
                PolFreqEntry(
                    freq_ghz=6.0, frac_pol_pct=0.3, frac_pol_upper_limit=True, pol_angle_deg=None
                ),
            ],
        },
    ),
    PolCalEntry(
        j2000_name="J0713+4349",
        b1950_name="B0710+439",
        category="C",
        role=["leakage"],
        stable_pa=False,
        single_scan_sufficient=True,
        variability_note="Weak at high frequency — use with care above 10 GHz",
        aka=["0710+439", "0713+4349", "j0710+439"],
        epochs={
            "2019": [
                PolFreqEntry(
                    freq_ghz=1.45, frac_pol_pct=0.1, frac_pol_upper_limit=True, pol_angle_deg=None
                ),
                PolFreqEntry(
                    freq_ghz=6.0, frac_pol_pct=0.1, frac_pol_upper_limit=True, pol_angle_deg=None
                ),
            ],
        },
    ),
    PolCalEntry(
        j2000_name="J0359+5057",
        b1950_name="NRAO150",
        category="B",
        role=["angle", "leakage"],  # B1: optimal Dec range for PA coverage
        stable_pa=False,
        single_scan_sufficient=False,
        variability_note="Variable — use only when 3C286/3C138 unavailable; monitor before use",
        aka=["0359+509", "0359+5057", "nrao150", "j0359+509"],
        epochs={
            "2019": [
                PolFreqEntry(
                    freq_ghz=1.45, frac_pol_pct=3.0, frac_pol_upper_limit=False, pol_angle_deg=None
                ),
                PolFreqEntry(
                    freq_ghz=6.0, frac_pol_pct=5.0, frac_pol_upper_limit=False, pol_angle_deg=None
                ),
            ],
        },
    ),
    PolCalEntry(
        j2000_name="J2202+4216",
        b1950_name="BL Lac",
        category="B",
        role=["angle", "leakage"],  # B1: optimal Dec range for PA coverage
        stable_pa=False,
        single_scan_sufficient=False,
        variability_note="Variable — use only as secondary angle calibrator; monitor before use",
        aka=["2200+420", "2202+4216", "bllac", "bl lac", "j2200+420"],
        epochs={
            "2019": [
                PolFreqEntry(
                    freq_ghz=1.45, frac_pol_pct=5.0, frac_pol_upper_limit=False, pol_angle_deg=None
                ),
                PolFreqEntry(
                    freq_ghz=6.0, frac_pol_pct=6.0, frac_pol_upper_limit=False, pol_angle_deg=None
                ),
            ],
        },
    ),
    PolCalEntry(
        j2000_name="J2253+1608",
        b1950_name="3C454.3",
        category="B",
        role=["angle"],
        stable_pa=False,
        single_scan_sufficient=False,
        variability_note="Variable — use only as secondary angle calibrator; monitor before use",
        aka=["2251+158", "2253+1608", "3c454.3", "3c4543", "j2251+158"],
        epochs={
            "2019": [
                PolFreqEntry(
                    freq_ghz=1.45, frac_pol_pct=8.0, frac_pol_upper_limit=False, pol_angle_deg=None
                ),
                PolFreqEntry(
                    freq_ghz=6.0, frac_pol_pct=9.0, frac_pol_upper_limit=False, pol_angle_deg=None
                ),
            ],
        },
    ),
]


# ---------------------------------------------------------------------------
# Name normalisation (mirrors util/calibrators.py)
# ---------------------------------------------------------------------------


def _normalise(name: str) -> str:
    """Lowercase, strip separators — same lossy normalisation as calibrators.py."""
    s = name.lower().strip()
    s = re.sub(r"[\s\-_+.]", "", s)
    return s


_NORMALISED_POL_CATALOGUE: list[tuple[str, PolCalEntry]] = []


def _build_pol_index() -> None:
    global _NORMALISED_POL_CATALOGUE
    _NORMALISED_POL_CATALOGUE = []
    for entry in POL_CATALOGUE:
        _NORMALISED_POL_CATALOGUE.append((_normalise(entry.j2000_name), entry))
        _NORMALISED_POL_CATALOGUE.append((_normalise(entry.b1950_name), entry))
        for alias in entry.aka:
            _NORMALISED_POL_CATALOGUE.append((_normalise(alias), entry))


_build_pol_index()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def lookup_pol(field_name: str) -> PolCalEntry | None:
    """
    Look up a field name in the polarisation calibrator catalogue.

    Returns the matching PolCalEntry, or None if not found.
    Matching is case-insensitive and separator-normalised.
    """
    key = _normalise(field_name)
    for normalised_alias, entry in _NORMALISED_POL_CATALOGUE:
        if key == normalised_alias:
            return entry
    return None


def is_angle_calibrator(field_name: str) -> bool:
    """True if the field is a known pol angle calibrator."""
    entry = lookup_pol(field_name)
    return entry is not None and "angle" in entry.role


def is_leakage_calibrator(field_name: str) -> bool:
    """True if the field is a known pol leakage calibrator."""
    entry = lookup_pol(field_name)
    return entry is not None and "leakage" in entry.role


def pol_properties_at_freq(
    entry: PolCalEntry,
    freq_ghz: float,
    epoch: str = "2019",
) -> PolFreqEntry | None:
    """
    Interpolate pol properties at `freq_ghz` from the tabulated epoch data.

    Returns a PolFreqEntry with interpolated values, or None if no data for
    this epoch or the frequency is out of the tabulated range.

    Interpolation strategy:
    - frac_pol_pct: linear interpolation between the two nearest nodes.
    - pol_angle_deg: linear interpolation only when both bounding nodes have a
      defined angle; otherwise returns None (indicating instability/absence).
    - frac_pol_upper_limit: True if either bounding node carries an upper limit.
    """
    rows = entry.epochs.get(epoch)
    if not rows:
        return None

    # Sort by frequency
    rows = sorted(rows, key=lambda r: r.freq_ghz)

    # Exact match
    for row in rows:
        if abs(row.freq_ghz - freq_ghz) < 1e-4:
            return row

    # Out of range
    if freq_ghz < rows[0].freq_ghz or freq_ghz > rows[-1].freq_ghz:
        return None

    # Find bounding nodes
    lo: PolFreqEntry | None = None
    hi: PolFreqEntry | None = None
    for i in range(len(rows) - 1):
        if rows[i].freq_ghz <= freq_ghz <= rows[i + 1].freq_ghz:
            lo, hi = rows[i], rows[i + 1]
            break

    if lo is None or hi is None:
        return None

    # Linear interpolation weight
    t = (freq_ghz - lo.freq_ghz) / (hi.freq_ghz - lo.freq_ghz)

    frac = None
    if lo.frac_pol_pct is not None and hi.frac_pol_pct is not None:
        frac = lo.frac_pol_pct + t * (hi.frac_pol_pct - lo.frac_pol_pct)

    angle = None
    if lo.pol_angle_deg is not None and hi.pol_angle_deg is not None:
        angle = lo.pol_angle_deg + t * (hi.pol_angle_deg - lo.pol_angle_deg)

    upper = lo.frac_pol_upper_limit or hi.frac_pol_upper_limit

    return PolFreqEntry(
        freq_ghz=freq_ghz,
        frac_pol_pct=frac,
        frac_pol_upper_limit=upper,
        pol_angle_deg=angle,
    )
