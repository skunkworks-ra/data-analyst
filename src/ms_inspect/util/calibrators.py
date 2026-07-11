"""
util/calibrators.py — Bundled calibrator catalogue and lookup logic.

Covers primary flux and bandpass calibrators for VLA, MeerKAT, and uGMRT.
Phase calibrators are field-specific and NOT included here.

Used by:
- tools/fields.py  — intent inference when MS has no scan intents
- tools/antennas.py — resolved-source UV range warning

No CASA dependency.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class UVRangeEntry:
    max_klambda: float
    reference: str


@dataclass
class CalibratorEntry:
    canonical_name: str
    aka: list[str]  # alternative names / coordinate strings
    role: list[str]  # 'flux', 'bandpass'
    telescopes: list[str]  # 'VLA', 'MeerKAT', 'uGMRT'
    resolved: bool
    flux_standard: str
    notes: str | None = None
    safe_uv_range_klambda: dict[str, UVRangeEntry] = field(default_factory=dict)
    casa_model_available: bool = False
    casa_model_name: str | None = None


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------

CATALOGUE: list[CalibratorEntry] = [
    CalibratorEntry(
        canonical_name="3C286",
        aka=["1331+305", "1331+3030", "j1331+3030", "j1331+305"],
        role=["flux", "bandpass"],
        telescopes=["VLA", "uGMRT"],
        resolved=False,
        flux_standard="Perley-Butler-2017",
        notes=(
            "Primary VLA flux and bandpass calibrator. "
            "Linearly polarised ~11% at L-band — useful for R-L phase calibration."
        ),
    ),
    CalibratorEntry(
        canonical_name="3C48",
        aka=["0137+331", "0137+3309", "j0137+3309", "j0137+331"],
        role=["flux", "bandpass"],
        telescopes=["VLA", "uGMRT"],
        resolved=False,
        flux_standard="Perley-Butler-2017",
        notes=(
            "Slightly variable at high frequencies (>20 GHz). "
            "Avoid for polarisation calibration due to low fractional polarisation."
        ),
    ),
    CalibratorEntry(
        canonical_name="3C147",
        aka=["0538+498", "0542+4951", "j0542+4951"],
        role=["flux"],
        telescopes=["VLA"],
        resolved=False,
        flux_standard="Perley-Butler-2017",
        notes=None,
    ),
    CalibratorEntry(
        canonical_name="3C138",
        aka=["0518+165", "0521+1638", "j0521+1638"],
        role=["flux", "bandpass"],
        telescopes=["VLA"],
        resolved=False,
        flux_standard="Perley-Butler-2017",
        notes=("Linearly polarised. Useful for R-L phase calibration at L-band."),
    ),
    CalibratorEntry(
        canonical_name="PKS1934-638",
        aka=["1934-638", "1934-63", "j1939-6342", "pks1934", "pks1934-638"],
        role=["flux", "bandpass"],
        telescopes=["MeerKAT"],
        resolved=False,
        flux_standard="Reynolds-1994",
        notes=(
            "Primary MeerKAT and ATCA flux and bandpass calibrator. "
            "Unpolarised to <0.2% — unsuitable for polarisation calibration."
        ),
    ),
    CalibratorEntry(
        canonical_name="PKS0408-65",
        aka=["0408-658", "0408-65", "j0408-6545", "pks0408", "pks0408-65"],
        role=["flux"],
        telescopes=["MeerKAT"],
        resolved=False,
        flux_standard="Stevens-2004",
        notes=(
            "Secondary MeerKAT flux calibrator. Used when PKS1934-638 "
            "is below the horizon or otherwise unavailable."
        ),
    ),
    CalibratorEntry(
        canonical_name="CasA",
        aka=["cas-a", "casa", "j2323+5848", "3c461", "cassiopeia-a", "cassiopeia a"],
        role=["flux"],
        telescopes=["VLA", "uGMRT"],
        resolved=True,
        flux_standard="Perley-Butler-2017",
        notes=(
            "Cassiopeia A. Highly resolved SNR ~4 arcmin diameter. "
            "Flux density declines ~0.6%/yr at GHz frequencies. "
            "Use setjy with component model only. Never use as a point source."
        ),
        safe_uv_range_klambda={
            "P-band (230-470 MHz)": UVRangeEntry(max_klambda=2.0, reference="Perley & Butler 2017"),
            "L-band (1-2 GHz)": UVRangeEntry(
                max_klambda=0.5, reference="estimated — use component model at all baselines"
            ),
        },
        casa_model_available=True,
        casa_model_name="CasA_Epoch2010.0",
    ),
    CalibratorEntry(
        canonical_name="CygA",
        aka=["cyg-a", "cyga", "j1959+4044", "3c405", "cygnus-a", "cygnus a"],
        role=["flux"],
        telescopes=["VLA", "uGMRT"],
        resolved=True,
        flux_standard="Perley-Butler-2017",
        notes=(
            "Cygnus A. Double-lobed radio galaxy. Core + lobes separated ~1.5 arcmin. "
            "Use component model for B/A config at L-band and above. "
            "Core is variable — exercise care at high frequencies."
        ),
        safe_uv_range_klambda={
            "P-band (230-470 MHz)": UVRangeEntry(max_klambda=5.0, reference="McKean et al. 2016"),
            "L-band (1-2 GHz)": UVRangeEntry(max_klambda=50.0, reference="McKean et al. 2016"),
            "C-band (4-8 GHz)": UVRangeEntry(
                max_klambda=5.0, reference="estimated — core dominates"
            ),
        },
        casa_model_available=True,
        casa_model_name="3C405_CygA",
    ),
    CalibratorEntry(
        canonical_name="TauA",
        aka=["tau-a", "taua", "j0534+2200", "3c144", "m1", "crab", "crab nebula"],
        role=["flux"],
        telescopes=["VLA", "uGMRT"],
        resolved=True,
        flux_standard="Perley-Butler-2017",
        notes=(
            "Crab Nebula (M1). Extended SNR ~7 arcmin diameter. "
            "Flux varies ~0.2%/yr. Use component model. "
            "Also a bright X-ray and gamma-ray source — not relevant for radio calibration."
        ),
        safe_uv_range_klambda={
            "P-band (230-470 MHz)": UVRangeEntry(max_klambda=1.0, reference="estimated"),
            "L-band (1-2 GHz)": UVRangeEntry(max_klambda=5.0, reference="estimated"),
        },
        casa_model_available=True,
        casa_model_name="3C144_TauA",
    ),
    CalibratorEntry(
        canonical_name="VirA",
        aka=["vir-a", "vira", "j1230+1223", "3c274", "m87", "virgo-a", "virgo a"],
        role=["flux"],
        telescopes=["VLA", "uGMRT"],
        resolved=True,
        flux_standard="Perley-Butler-2017",
        notes=(
            "M87 (3C274). Compact core + extended lobes + visible jet. "
            "Core is variable on months-years timescale. "
            "Jet visible on long baselines. Use component model for B/A config."
        ),
        safe_uv_range_klambda={
            "P-band (230-470 MHz)": UVRangeEntry(max_klambda=3.0, reference="estimated"),
            "L-band (1-2 GHz)": UVRangeEntry(max_klambda=20.0, reference="estimated"),
        },
        casa_model_available=True,
        casa_model_name="3C274_VirA",
    ),
]


# ---------------------------------------------------------------------------
# Normalisation helper
# ---------------------------------------------------------------------------


def _normalise(name: str) -> str:
    """
    Normalise a source name for matching:
    - lowercase
    - strip leading/trailing whitespace
    - collapse internal whitespace to single space
    - remove common separators: +, -, _, .  from coordinate suffixes
      but preserve them in the middle of coordinate strings only when they
      are not part of B1950/J2000 suffixes.

    Strategy: lowercase + strip separators entirely for alias matching.
    This is intentionally lossy — we match 'pks1934638' == 'PKS1934-638'.
    """
    s = name.lower().strip()
    # Remove spaces, hyphens, underscores, plus signs, dots
    s = re.sub(r"[\s\-_+.]", "", s)
    return s


_NORMALISED_CATALOGUE: list[tuple[str, CalibratorEntry]] = []


def _build_index() -> None:
    global _NORMALISED_CATALOGUE
    _NORMALISED_CATALOGUE = []
    for entry in CATALOGUE:
        _NORMALISED_CATALOGUE.append((_normalise(entry.canonical_name), entry))
        for alias in entry.aka:
            _NORMALISED_CATALOGUE.append((_normalise(alias), entry))


_build_index()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def lookup(field_name: str) -> CalibratorEntry | None:
    """
    Look up a field name in the calibrator catalogue.

    Returns the matching CalibratorEntry, or None if not found.
    Matching is case-insensitive and separator-normalised.
    Handles CASA's '=' convention for appending common names (e.g. '0137+331=3C48').
    """
    key = _normalise(field_name)
    for normalised_alias, entry in _NORMALISED_CATALOGUE:
        if key == normalised_alias:
            return entry
    # CASA appends common names with '=' (e.g. '0137+331=3C48') — try each part.
    if "=" in field_name:
        for part in field_name.split("="):
            part = part.strip()
            if not part:
                continue
            part_key = _normalise(part)
            if part_key == key:
                continue  # already tried
            for normalised_alias, entry in _NORMALISED_CATALOGUE:
                if part_key == normalised_alias:
                    return entry
    return None


def infer_intents_from_role(role: list[str]) -> list[str]:
    """
    Map catalogue roles to CASA-style intent strings.

    Args:
        role: List of role strings (e.g. ['flux', 'bandpass']).

    Returns:
        List of CASA intent strings (e.g. ['CALIBRATE_FLUX#ON_SOURCE',
        'CALIBRATE_BANDPASS#ON_SOURCE']).
    """
    intent_map = {
        "flux": "CALIBRATE_FLUX#ON_SOURCE",
        "bandpass": "CALIBRATE_BANDPASS#ON_SOURCE",
    }
    return [intent_map[r] for r in role if r in intent_map]


def is_known_calibrator(field_name: str) -> bool:
    """True if the field name matches any entry in the catalogue."""
    return lookup(field_name) is not None


def resolved_warning_message(
    entry: CalibratorEntry,
    max_baseline_klambda: float,
    band_name: str | None,
) -> str | None:
    """
    Return a warning message string if the calibrator is resolved at the
    given max baseline and band. Returns None if no warning is needed.

    Logic per DESIGN.md §3.5:
    - If resolved=False: no warning.
    - If resolved=True and band not in safe_uv_range: warn, state unknown limit.
    - If resolved=True and max_baseline > safe max: warn with specifics.
    - If resolved=True and max_baseline <= safe max: mild advisory only.
    """
    if not entry.resolved:
        return None

    # Try to find a matching band key — require the primary band letter/name to match
    # e.g. "L-band" should match key "L-band (1-2 GHz)" but NOT "P-band"
    matched_band_key: str | None = None
    if band_name:
        # Extract the primary band token: everything before the first space or '('
        import re as _re

        primary_token = _re.split(r"[\s(]", band_name.lower())[0].rstrip("-")
        for key in entry.safe_uv_range_klambda:
            key_primary = _re.split(r"[\s(]", key.lower())[0].rstrip("-")
            if primary_token == key_primary:
                matched_band_key = key
                break

    if matched_band_key is None:
        # Band not in safe_uv_range table — unknown safe limit
        band_display = band_name or "unknown"
        return (
            f"WARNING [{entry.canonical_name}]: This source is resolved on long baselines. "
            f"Safe UV range for band '{band_display}' is not in the catalogue. "
            f"A component model MUST be provided before calibration. "
            f"CASA model available: {entry.casa_model_available} "
            f"({'model: ' + entry.casa_model_name if entry.casa_model_name else 'no model name'}). "
            f"Do NOT use setjy with a point-source model."
        )

    uv_entry = entry.safe_uv_range_klambda[matched_band_key]

    if max_baseline_klambda > uv_entry.max_klambda:
        return (
            f"WARNING [{entry.canonical_name}]: Source is resolved at your maximum baseline "
            f"({max_baseline_klambda:.1f} kλ). "
            f"Safe UV range for {matched_band_key}: ≤{uv_entry.max_klambda} kλ "
            f"({uv_entry.reference}). "
            f"Do NOT use setjy with a point-source model. "
            f"Use: setjy(vis=..., field='{entry.canonical_name}', "
            f"model='{entry.casa_model_name or 'COMPONENT_MODEL'}') "
            f"CASA component model available: {entry.casa_model_available}."
        )
    else:
        return (
            f"ADVISORY [{entry.canonical_name}]: This source is intrinsically extended, "
            f"but your max baseline ({max_baseline_klambda:.1f} kλ) is within the safe range "
            f"(≤{uv_entry.max_klambda} kλ for {matched_band_key}). "
            f"Proceed with care; verify with a short-baseline image."
        )
