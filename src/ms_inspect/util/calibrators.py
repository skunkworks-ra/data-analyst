"""
util/calibrators.py — Bundled calibrator catalogue and lookup logic.

Covers primary flux and bandpass calibrators for VLA, MeerKAT, uGMRT and ALMA,
including the solar-system bodies ALMA uses as flux standards.
Phase calibrators are field-specific and NOT included here.

Two invariants this file must keep:

- `flux_standard` is spelled exactly as CASA's setjy accepts it — a space, not
  a hyphen, before the year. `None` means CASA has no standard for the source.
- `freq_range_ghz` is per SOURCE, not per standard, and `None` means the range
  is unknown to us. It never means unbounded.

Used by:
- tools/fields.py  — intent inference when MS has no scan intents
- tools/antennas.py — resolved-source UV range warning

No CASA dependency.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
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
    telescopes: list[str]  # 'VLA', 'MeerKAT', 'uGMRT', 'ALMA'
    resolved: bool
    # CASA's own standard string, spelled exactly as setjy accepts it
    # (e.g. 'Perley-Butler 2017'). None means CASA has no standard for this
    # source and it must be given an explicit manual flux instead.
    flux_standard: str | None
    # Validity range of THIS SOURCE under its standard, in GHz. Ranges are
    # per source, not per standard: Perley-Butler 2017 spans 0.05-50 GHz as a
    # standard, but Fornax A within it is only good over 0.2-0.5 GHz.
    # None means the range is not known to us — callers must NOT read that as
    # unbounded, and must not gate on it.
    freq_range_ghz: tuple[float, float] | None = None
    # Moving target with no fixed sky position. Its position field is not a
    # discriminator, so name lookup and positional cross-match must both skip
    # the coordinate test rather than mismatch on it.
    solar_system: bool = False
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
        flux_standard="Perley-Butler 2017",
        freq_range_ghz=(0.05, 50.0),
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
        flux_standard="Perley-Butler 2017",
        freq_range_ghz=(0.05, 50.0),
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
        flux_standard="Perley-Butler 2017",
        freq_range_ghz=(0.05, 50.0),
        notes=None,
    ),
    CalibratorEntry(
        canonical_name="3C138",
        aka=["0518+165", "0521+1638", "j0521+1638"],
        role=["flux", "bandpass"],
        telescopes=["VLA"],
        resolved=False,
        flux_standard="Perley-Butler 2017",
        freq_range_ghz=(0.2, 50.0),
        notes=("Linearly polarised. Useful for R-L phase calibration at L-band."),
    ),
    CalibratorEntry(
        canonical_name="PKS1934-638",
        aka=["1934-638", "1934-63", "j1939-6342", "pks1934", "pks1934-638"],
        role=["flux", "bandpass"],
        telescopes=["MeerKAT"],
        resolved=False,
        flux_standard="Stevens-Reynolds 2016",
        freq_range_ghz=None,
        notes=(
            "Primary MeerKAT and ATCA flux and bandpass calibrator. "
            "Unpolarised to <0.2% — unsuitable for polarisation calibration. "
            "Validity range not recorded here: confirm against the CASA setjy "
            "documentation before gating on frequency."
        ),
    ),
    CalibratorEntry(
        canonical_name="PKS0408-65",
        aka=["0408-658", "0408-65", "j0408-6545", "pks0408", "pks0408-65"],
        role=["flux"],
        telescopes=["MeerKAT"],
        resolved=False,
        flux_standard=None,
        freq_range_ghz=None,
        notes=(
            "Secondary MeerKAT flux calibrator. Used when PKS1934-638 "
            "is below the horizon or otherwise unavailable. "
            "CASA has NO flux standard for this source — it must be given an "
            "explicit manual flux density. Never route it to a standard string."
        ),
    ),
    CalibratorEntry(
        canonical_name="CasA",
        aka=["cas-a", "casa", "j2323+5848", "3c461", "cassiopeia-a", "cassiopeia a"],
        role=["flux"],
        telescopes=["VLA", "uGMRT"],
        resolved=True,
        flux_standard="Perley-Butler 2017",
        freq_range_ghz=(0.2, 4.0),
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
        flux_standard="Perley-Butler 2017",
        freq_range_ghz=(0.05, 12.0),
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
        flux_standard="Perley-Butler 2017",
        freq_range_ghz=(0.05, 4.0),
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
        flux_standard="Perley-Butler 2017",
        freq_range_ghz=(0.05, 3.0),
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
    # -----------------------------------------------------------------------
    # Remaining Perley-Butler 2017 sources.
    #
    # casa_model_available is left False throughout: a component model may well
    # ship with CASA for the extended ones, but we have not verified which, and
    # claiming one that does not exist fails at setjy time.
    # -----------------------------------------------------------------------
    CalibratorEntry(
        canonical_name="3C123",
        aka=["0433+295", "0437+2940", "j0437+2940"],
        role=["flux"],
        telescopes=["VLA"],
        resolved=True,
        flux_standard="Perley-Butler 2017",
        freq_range_ghz=(0.06, 50.0),
        notes="Extended ~20 arcsec. Use a component model on long baselines.",
    ),
    CalibratorEntry(
        canonical_name="3C196",
        aka=["0809+483", "0813+4813", "j0813+4813"],
        role=["flux", "bandpass"],
        telescopes=["VLA"],
        resolved=False,
        flux_standard="Perley-Butler 2017",
        freq_range_ghz=(0.05, 50.0),
        notes=None,
    ),
    CalibratorEntry(
        canonical_name="3C295",
        aka=["1409+524", "1411+5212", "j1411+5212"],
        role=["flux"],
        telescopes=["VLA"],
        resolved=False,
        flux_standard="Perley-Butler 2017",
        freq_range_ghz=(0.05, 50.0),
        notes="Compact double, ~4 arcsec separation. Resolved on the longest VLA baselines.",
    ),
    CalibratorEntry(
        canonical_name="3C380",
        aka=["1828+487", "1829+4844", "j1829+4844"],
        role=["flux"],
        telescopes=["VLA"],
        resolved=False,
        flux_standard="Perley-Butler 2017",
        freq_range_ghz=(0.05, 4.0),
        notes=None,
    ),
    CalibratorEntry(
        canonical_name="3C353",
        aka=["1717-009", "1720-0058", "j1720-0058"],
        role=["flux"],
        telescopes=["VLA"],
        resolved=True,
        flux_standard="Perley-Butler 2017",
        freq_range_ghz=(0.2, 4.0),
        notes="Extended double, ~4 arcmin. Use a component model.",
    ),
    CalibratorEntry(
        canonical_name="3C444",
        aka=["2211-172", "2214-1701", "j2214-1701"],
        role=["flux"],
        telescopes=["VLA"],
        resolved=True,
        flux_standard="Perley-Butler 2017",
        freq_range_ghz=(0.2, 12.0),
        notes="Extended. Use a component model on long baselines.",
    ),
    CalibratorEntry(
        canonical_name="HydraA",
        aka=["hydra-a", "hyda", "hyd-a", "3c218", "0915-118", "0918-1205", "j0918-1205"],
        role=["flux"],
        telescopes=["VLA"],
        resolved=True,
        flux_standard="Perley-Butler 2017",
        freq_range_ghz=(0.05, 12.0),
        notes="Hydra A (3C218). Extended radio galaxy. Use a component model.",
    ),
    CalibratorEntry(
        canonical_name="HerA",
        aka=["hercules-a", "hercules a", "her-a", "hera", "3c348", "1648+050", "j1651+0459"],
        role=["flux"],
        telescopes=["VLA"],
        resolved=True,
        flux_standard="Perley-Butler 2017",
        freq_range_ghz=(0.2, 12.0),
        notes="Hercules A (3C348). Double-lobed, ~3 arcmin. Use a component model.",
    ),
    CalibratorEntry(
        canonical_name="PicA",
        aka=["pictor-a", "pictor a", "pic-a", "pica", "0518-458", "0519-4546", "j0519-4546"],
        role=["flux"],
        telescopes=["VLA"],
        resolved=True,
        flux_standard="Perley-Butler 2017",
        freq_range_ghz=(0.2, 4.0),
        notes="Pictor A. Double-lobed, ~8 arcmin separation. Use a component model.",
    ),
    CalibratorEntry(
        canonical_name="ForA",
        aka=["fornax-a", "fornax a", "for-a", "fora", "ngc1316", "0320-37", "j0322-3712"],
        role=["flux"],
        telescopes=["VLA"],
        resolved=True,
        flux_standard="Perley-Butler 2017",
        freq_range_ghz=(0.2, 0.5),
        notes=(
            "Fornax A (NGC 1316). Very extended — lobes span ~1 degree. "
            "Narrowest validity range of any Perley-Butler 2017 source."
        ),
    ),
    CalibratorEntry(
        canonical_name="J0133-3649",
        aka=["0131-367", "j0133-3649"],
        role=["flux"],
        telescopes=["VLA"],
        resolved=False,
        flux_standard="Perley-Butler 2017",
        freq_range_ghz=(0.2, 4.0),
        notes="Southern flux calibrator.",
    ),
    CalibratorEntry(
        canonical_name="J0444-2809",
        aka=["0442-282", "j0444-2809"],
        role=["flux"],
        telescopes=["VLA"],
        resolved=False,
        flux_standard="Perley-Butler 2017",
        freq_range_ghz=(0.2, 2.0),
        notes="Southern flux calibrator.",
    ),
    # -----------------------------------------------------------------------
    # Solar-system flux standards (Butler-JPL-Horizons 2012).
    #
    # These are MOVING TARGETS. solar_system=True tells every caller that the
    # position field is not a discriminator, so name lookup and the VLA
    # positional cross-match must both skip the coordinate test.
    #
    # freq_range_ghz is None for all of them: the CASA documentation gives no
    # per-object validity range for this standard. None means unknown, so no
    # caller may gate on it. Do not fill these in with guesses.
    #
    # resolved=True for all fifteen. Apparent diameter varies over the synodic
    # cycle and even the smallest body here is resolved on ALMA's long
    # baselines, so a static False would be wrong in the dangerous direction.
    # Typical apparent diameters are in each note.
    # -----------------------------------------------------------------------
    CalibratorEntry(
        canonical_name="Venus",
        aka=["venus"],
        role=["flux"],
        telescopes=["ALMA", "VLA"],
        resolved=True,
        flux_standard="Butler-JPL-Horizons 2012",
        solar_system=True,
        notes="Apparent diameter ~10-60 arcsec. Thick atmosphere; strongly resolved.",
    ),
    CalibratorEntry(
        canonical_name="Mars",
        aka=["mars"],
        role=["flux"],
        telescopes=["ALMA", "VLA"],
        resolved=True,
        flux_standard="Butler-JPL-Horizons 2012",
        solar_system=True,
        notes="Apparent diameter ~4-25 arcsec.",
    ),
    CalibratorEntry(
        canonical_name="Jupiter",
        aka=["jupiter"],
        role=["flux"],
        telescopes=["ALMA", "VLA"],
        resolved=True,
        flux_standard="Butler-JPL-Horizons 2012",
        solar_system=True,
        notes=(
            "Apparent diameter ~30-50 arcsec. Also emits strong synchrotron "
            "radiation at low frequency, which the thermal model does not describe."
        ),
    ),
    CalibratorEntry(
        canonical_name="Uranus",
        aka=["uranus"],
        role=["flux"],
        telescopes=["ALMA", "VLA"],
        resolved=True,
        flux_standard="Butler-JPL-Horizons 2012",
        solar_system=True,
        notes="Apparent diameter ~3.4-3.7 arcsec. A common ALMA flux standard.",
    ),
    CalibratorEntry(
        canonical_name="Neptune",
        aka=["neptune"],
        role=["flux"],
        telescopes=["ALMA", "VLA"],
        resolved=True,
        flux_standard="Butler-JPL-Horizons 2012",
        solar_system=True,
        notes="Apparent diameter ~2.2-2.4 arcsec. A common ALMA flux standard.",
    ),
    CalibratorEntry(
        canonical_name="Io",
        aka=["io"],
        role=["flux"],
        telescopes=["ALMA"],
        resolved=True,
        flux_standard="Butler-JPL-Horizons 2012",
        solar_system=True,
        notes="Jovian moon. Apparent diameter ~1.2 arcsec. Confusion from Jupiter is a hazard.",
    ),
    CalibratorEntry(
        canonical_name="Europa",
        aka=["europa"],
        role=["flux"],
        telescopes=["ALMA"],
        resolved=True,
        flux_standard="Butler-JPL-Horizons 2012",
        solar_system=True,
        notes="Jovian moon. Apparent diameter ~1.0 arcsec. Confusion from Jupiter is a hazard.",
    ),
    CalibratorEntry(
        canonical_name="Ganymede",
        aka=["ganymede"],
        role=["flux"],
        telescopes=["ALMA"],
        resolved=True,
        flux_standard="Butler-JPL-Horizons 2012",
        solar_system=True,
        notes="Jovian moon. Apparent diameter ~1.7 arcsec. Confusion from Jupiter is a hazard.",
    ),
    CalibratorEntry(
        canonical_name="Callisto",
        aka=["callisto"],
        role=["flux"],
        telescopes=["ALMA"],
        resolved=True,
        flux_standard="Butler-JPL-Horizons 2012",
        solar_system=True,
        notes="Jovian moon. Apparent diameter ~1.6 arcsec. Confusion from Jupiter is a hazard.",
    ),
    CalibratorEntry(
        canonical_name="Titan",
        aka=["titan"],
        role=["flux"],
        telescopes=["ALMA"],
        resolved=True,
        flux_standard="Butler-JPL-Horizons 2012",
        solar_system=True,
        notes=(
            "Saturnian moon. Apparent diameter ~0.8 arcsec. Thick atmosphere with "
            "molecular lines — avoid windows containing them for continuum work."
        ),
    ),
    CalibratorEntry(
        canonical_name="Ceres",
        aka=["ceres", "1ceres"],
        role=["flux"],
        telescopes=["ALMA"],
        resolved=True,
        flux_standard="Butler-JPL-Horizons 2012",
        solar_system=True,
        notes="Asteroid. Apparent diameter ~0.3-0.8 arcsec.",
    ),
    CalibratorEntry(
        canonical_name="Pallas",
        aka=["pallas", "2pallas"],
        role=["flux"],
        telescopes=["ALMA"],
        resolved=True,
        flux_standard="Butler-JPL-Horizons 2012",
        solar_system=True,
        notes="Asteroid. Apparent diameter ~0.3 arcsec.",
    ),
    CalibratorEntry(
        canonical_name="Vesta",
        aka=["vesta", "4vesta"],
        role=["flux"],
        telescopes=["ALMA"],
        resolved=True,
        flux_standard="Butler-JPL-Horizons 2012",
        solar_system=True,
        notes="Asteroid. Apparent diameter ~0.3-0.6 arcsec.",
    ),
    CalibratorEntry(
        canonical_name="Juno",
        aka=["juno", "3juno"],
        role=["flux"],
        telescopes=["ALMA"],
        resolved=True,
        flux_standard="Butler-JPL-Horizons 2012",
        solar_system=True,
        notes="Asteroid. Apparent diameter ~0.2 arcsec.",
    ),
    CalibratorEntry(
        canonical_name="Lutetia",
        aka=["lutetia", "21lutetia"],
        role=["flux"],
        telescopes=["ALMA"],
        resolved=True,
        flux_standard="Butler-JPL-Horizons 2012",
        solar_system=True,
        notes="Asteroid. Apparent diameter ~0.05 arcsec — the smallest body in this set.",
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


# Intent prefix -> role. The inverse of infer_intents_from_role, but over a
# WIDER vocabulary: the catalogue only ever says 'flux' or 'bandpass', while
# intents also name the phase calibrator, the target, and the polarisation
# standards.
#
# Keyed on the part before '#', because the suffix is telescope-specific:
# ALMA writes CALIBRATE_FLUX#ON_SOURCE, the VLA CALIBRATE_FLUX#UNSPECIFIED.
_INTENT_PREFIX_TO_ROLE: dict[str, str] = {
    "CALIBRATE_FLUX": "flux",
    "CALIBRATE_AMPLI": "amplitude",
    "CALIBRATE_BANDPASS": "bandpass",
    "CALIBRATE_PHASE": "phase",
    "CALIBRATE_DELAY": "delay",
    "CALIBRATE_POL_ANGLE": "polangle",
    "CALIBRATE_POL_LEAKAGE": "polleakage",
    "CALIBRATE_POLARIZATION": "polarization",
    "OBSERVE_TARGET": "target",
    "OBSERVE_CHECK_SOURCE": "check",
    "OBSERVE_CHECK": "check",
}

# Technical intents that ride along on calibrators and targets alike. They say
# what was measured, not what the field is for, so they must yield no role —
# otherwise every ALMA field acquires one.
_NON_ROLE_INTENT_PREFIXES: frozenset[str] = frozenset(
    {
        "CALIBRATE_ATMOSPHERE",
        "CALIBRATE_POINTING",
        "CALIBRATE_WVR",
        "CALIBRATE_SIDEBAND_RATIO",
        "CALIBRATE_FOCUS",
        "CALIBRATE_FOCUS_X",
        "CALIBRATE_FOCUS_Y",
        "CALIBRATE_ANTENNA_POSITION",
        "CALIBRATE_ANTENNA_PHASE",
        "CALIBRATE_ANTENNA_POINTING_MODEL",
        "CALIBRATE_DIFFGAIN",
        "MAP_ANTENNA_SURFACE",
        "SYSTEM_CONFIGURATION",
        "UNSPECIFIED",
    }
)


def role_from_intents(intents: Iterable[str]) -> list[str]:
    """
    Map a field's scan intents to calibration roles.

    Returns a sorted list of roles, or [] when the intents name none. An empty
    result is meaningful: it says the intents carry no role information, which
    is the case for a field marked only with technical intents such as
    CALIBRATE_ATMOSPHERE. It does NOT mean the field has no intents.

    Unrecognised intents are ignored rather than guessed at.
    """
    roles: set[str] = set()
    for intent in intents:
        prefix = str(intent).split("#", 1)[0].strip().upper()
        if not prefix or prefix in _NON_ROLE_INTENT_PREFIXES:
            continue
        role = _INTENT_PREFIX_TO_ROLE.get(prefix)
        if role is not None:
            roles.add(role)
    return sorted(roles)


def roles_disagree(intent_roles: list[str], catalogue_roles: list[str]) -> bool:
    """
    True when the intent-derived roles and the catalogue roles contradict.

    The test is DISJOINTNESS, not inequality. A source the catalogue lists as
    both flux and bandpass, used in this observation as bandpass only, is a
    narrower truth rather than a contradiction. A source the catalogue calls a
    flux calibrator whose intents say TARGET shares nothing, and that is the
    case worth shouting about.

    Two empty sets cannot disagree; neither can a comparison with nothing to
    compare against.
    """
    if not intent_roles or not catalogue_roles:
        return False
    return not (set(intent_roles) & set(catalogue_roles))


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
