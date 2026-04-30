"""
util/phase_cal_catalog.py — VLA phase calibrator reference catalog.

Parses PhaseCalList.txt (shipped as package data) into an in-memory
lookup table.  Provides:

    get_source(iau_name)             → PhaseCalEntry | None
    lookup_nearest(ra_deg, dec_deg,  → PhaseCalMatch | None
                   band_code,
                   array_config,
                   max_sep_deg)

Source: NRAO VLA Calibrator Manual (PhaseCalList.txt, bundled in this package).

Determinism guarantee: static data, no live web fetch, no CASA dependency.
"""

from __future__ import annotations

import importlib.resources
import math
import re
from dataclasses import dataclass, field
from functools import lru_cache

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

BAND_WAVELENGTH_TO_CODE: dict[str, str] = {
    "90cm": "P",
    "20cm": "L",
    "6cm": "C",
    "3.7cm": "X",
    "2cm": "U",
    "1.3cm": "K",
    "0.7cm": "Q",
}

# Quality codes in descending order of suitability
QUALITY_RANK: dict[str, int] = {"P": 4, "S": 3, "W": 2, "C": 1, "X": 0, "?": -1}


@dataclass
class BandEntry:
    band_code: str      # L, C, X, U, K, Q, P
    wavelength: str     # '20cm', '6cm', …
    quality_A: str      # P / S / W / C / X / ?
    quality_B: str
    quality_C: str
    quality_D: str
    flux_jy: float | None
    uvmin_kl: float | None
    uvmax_kl: float | None

    def quality_for_config(self, array_config: str) -> str:
        """Return quality code for the given VLA array config (A/B/C/D)."""
        return {
            "A": self.quality_A,
            "B": self.quality_B,
            "C": self.quality_C,
            "D": self.quality_D,
        }.get(array_config.upper(), "?")

    def is_usable(self, array_config: str, min_quality: str = "W") -> bool:
        q = self.quality_for_config(array_config)
        return QUALITY_RANK.get(q, -1) >= QUALITY_RANK.get(min_quality, -1)


@dataclass
class PhaseCalEntry:
    iau_name: str           # '0137+331'
    ra_deg: float           # J2000 RA in decimal degrees
    dec_deg: float          # J2000 Dec in decimal degrees
    pos_accuracy: str       # A / B / C / T
    pos_ref: str | None     # 'Aug01', 'May00', …
    alt_name: str | None    # '3C48', 'JVAS', 'CJ2', …
    bands: dict[str, BandEntry] = field(default_factory=dict)  # keyed by band_code

    def band(self, band_code: str) -> BandEntry | None:
        return self.bands.get(band_code.upper())


@dataclass
class PhaseCalMatch:
    entry: PhaseCalEntry
    separation_deg: float
    band: BandEntry | None      # None if no entry for requested band
    quality: str | None         # quality code at requested band/config; None if no band


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

_RA_RE = re.compile(r"(\d+)h(\d+)m([\d.]+)s")
_DEC_RE = re.compile(r'(-?)(\d+)d(\d+)\'([\d."]+)')
_BAND_RE = re.compile(
    r"^\s*(\d+\.?\d*cm)\s+([A-Z])\s+"   # wavelength  band_code
    r"([A-Z?])\s+([A-Z?])\s+([A-Z?])\s+([A-Z?])\s+"  # q_A q_B q_C q_D
    r"([\d.]+)"                           # flux
    r"(?:\s+([\d.]+))?"                   # uvmin (optional)
    r"(?:\s+([\d.]+))?"                   # uvmax (optional)
)


def _parse_ra(s: str) -> float:
    m = _RA_RE.search(s)
    if not m:
        raise ValueError(f"Cannot parse RA: {s!r}")
    h, mn, sec = int(m.group(1)), int(m.group(2)), float(m.group(3))
    return (h + mn / 60.0 + sec / 3600.0) * 15.0


def _parse_dec(s: str) -> float:
    m = _DEC_RE.search(s)
    if not m:
        raise ValueError(f"Cannot parse Dec: {s!r}")
    sign = -1.0 if m.group(1) == "-" else 1.0
    deg, mn = int(m.group(2)), int(m.group(3))
    sec_str = m.group(4).rstrip('"').rstrip("'")
    sec = float(sec_str)
    return sign * (deg + mn / 60.0 + sec / 3600.0)


def _parse_j2000_line(line: str) -> tuple[str, float, float, str, str | None, str | None]:
    """Parse a J2000 header line.  Returns (iau_name, ra_deg, dec_deg, pos_accuracy, pos_ref, alt_name)."""
    # e.g. '0137+331   J2000  B 01h37m41.299431s 33d09'35.132990'' Aug01 3C48'
    tokens = line.split()
    if len(tokens) < 4:
        raise ValueError(f"Malformed J2000 line: {line!r}")
    iau_name = tokens[0]
    pos_accuracy = tokens[2]
    ra_str = tokens[3]
    dec_str = tokens[4]
    ra_deg = _parse_ra(ra_str)
    dec_deg = _parse_dec(dec_str)

    # Remaining tokens are optional pos_ref and alt_name
    rest = tokens[5:]
    pos_ref: str | None = None
    alt_name: str | None = None
    # pos_ref looks like 'Aug01', 'May00', 'Nov96', 'Jan00' etc. — month+year
    _POSREF_RE = re.compile(r"^(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\d{2}$")
    for tok in rest:
        if _POSREF_RE.match(tok):
            pos_ref = tok
        else:
            alt_name = tok
    return iau_name, ra_deg, dec_deg, pos_accuracy, pos_ref, alt_name


def _parse_band_line(line: str) -> BandEntry | None:
    m = _BAND_RE.match(line)
    if not m:
        return None
    wavelength = m.group(1).strip()
    band_code = BAND_WAVELENGTH_TO_CODE.get(wavelength, m.group(2))
    return BandEntry(
        band_code=band_code,
        wavelength=wavelength,
        quality_A=m.group(3),
        quality_B=m.group(4),
        quality_C=m.group(5),
        quality_D=m.group(6),
        flux_jy=float(m.group(7)),
        uvmin_kl=float(m.group(8)) if m.group(8) else None,
        uvmax_kl=float(m.group(9)) if m.group(9) else None,
    )


# ---------------------------------------------------------------------------
# Catalog loader
# ---------------------------------------------------------------------------

def _load_catalog(text: str) -> dict[str, PhaseCalEntry]:
    catalog: dict[str, PhaseCalEntry] = {}
    current: PhaseCalEntry | None = None
    in_band_table = False

    for raw_line in text.splitlines():
        line = raw_line.rstrip()

        # Global file header and per-entry separators
        if line.startswith("===") or line.startswith("---"):
            # "===" may appear as the global header underline or as the band-table
            # underline that immediately follows "BAND  A B C D ...".  In both cases
            # we just skip it; band-table state is set by the BAND header line itself.
            continue

        # Blank / whitespace-only lines end the current band table
        if not line.strip():
            in_band_table = False
            continue

        # Band table header — sequence: "---" then this line then "==="
        if "BAND" in line and "FLUX" in line:
            in_band_table = True
            continue

        # J2000 source header → start a new entry
        if "J2000" in line:
            in_band_table = False
            try:
                iau_name, ra_deg, dec_deg, pos_acc, pos_ref, alt_name = _parse_j2000_line(line)
            except ValueError:
                continue
            current = PhaseCalEntry(
                iau_name=iau_name,
                ra_deg=ra_deg,
                dec_deg=dec_deg,
                pos_accuracy=pos_acc,
                pos_ref=pos_ref,
                alt_name=alt_name,
            )
            catalog[iau_name] = current
            continue

        # Skip B1950 lines (duplicate position in old equinox)
        if "B1950" in line:
            continue

        # Band data rows
        if in_band_table and current is not None:
            band = _parse_band_line(line)
            if band is not None:
                current.bands[band.band_code] = band

    return catalog


@lru_cache(maxsize=1)
def _get_catalog() -> dict[str, PhaseCalEntry]:
    pkg = importlib.resources.files("ms_inspect.util")
    data = (pkg / "PhaseCalList.txt").read_text(encoding="ascii", errors="replace")
    return _load_catalog(data)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_source(iau_name: str) -> PhaseCalEntry | None:
    """Return a catalog entry by IAU name, or None if not found."""
    return _get_catalog().get(iau_name)


def _angular_separation_deg(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    """Great-circle separation in degrees (haversine formula)."""
    ra1_r = math.radians(ra1)
    ra2_r = math.radians(ra2)
    dec1_r = math.radians(dec1)
    dec2_r = math.radians(dec2)
    d_ra = ra2_r - ra1_r
    d_dec = dec2_r - dec1_r
    a = math.sin(d_dec / 2) ** 2 + math.cos(dec1_r) * math.cos(dec2_r) * math.sin(d_ra / 2) ** 2
    return math.degrees(2 * math.asin(math.sqrt(a)))


def lookup_nearest(
    ra_deg: float,
    dec_deg: float,
    band_code: str | None = None,
    array_config: str | None = None,
    max_sep_deg: float = 0.5,
    min_quality: str = "W",
) -> PhaseCalMatch | None:
    """Find the nearest catalog source within max_sep_deg.

    If band_code and array_config are given, the match must be usable at that
    band/config (quality >= min_quality).  Returns None if no match found.

    Args:
        ra_deg:       Target RA in decimal degrees (J2000).
        dec_deg:      Target Dec in decimal degrees (J2000).
        band_code:    VLA band code ('L', 'C', 'X', …).  None = position-only match.
        array_config: Array configuration ('A', 'B', 'C', 'D').  None = ignore.
        max_sep_deg:  Search radius in degrees.
        min_quality:  Minimum acceptable quality code ('P', 'S', 'W', 'C').

    Returns:
        PhaseCalMatch with the nearest qualifying source, or None.
    """
    catalog = _get_catalog()
    best: PhaseCalMatch | None = None
    best_sep = max_sep_deg

    for entry in catalog.values():
        sep = _angular_separation_deg(ra_deg, dec_deg, entry.ra_deg, entry.dec_deg)
        if sep > best_sep:
            continue

        band_entry = entry.band(band_code) if band_code else None

        # If band/config filter requested, skip unusable sources
        if band_code and array_config and (band_entry is None or not band_entry.is_usable(array_config, min_quality)):
            continue

        best_sep = sep
        quality = band_entry.quality_for_config(array_config) if (band_entry and array_config) else None
        best = PhaseCalMatch(entry=entry, separation_deg=sep, band=band_entry, quality=quality)

    return best


def lookup_by_name(name: str) -> PhaseCalEntry | None:
    """Match by IAU name or alt_name (case-insensitive).  Returns first match."""
    name_upper = name.upper().replace(" ", "")
    catalog = _get_catalog()
    # exact IAU match first
    if name in catalog:
        return catalog[name]
    # alt_name match
    for entry in catalog.values():
        if entry.alt_name and entry.alt_name.upper().replace(" ", "") == name_upper:
            return entry
    return None
