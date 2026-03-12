"""
util/vla_calibrators.py — VLA calibrator database positional cross-match.

Scrapes the VLA calibrator list from NRAO, caches it to disk as JSON,
and provides cone-search by (RA, Dec) for field identification.

Used by:
- tools/fields.py — positional cross-match when name-based lookup fails

No CASA dependency.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import astropy.units as u
from astropy.coordinates import SkyCoord

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

VLA_CALLIST_URL = "https://science.nrao.edu/facilities/vla/observing/callist"
CACHE_DIR = Path.home() / ".cache" / "ms_inspect"
CACHE_FILE = CACHE_DIR / "vla_callist.json"
CACHE_MAX_AGE_DAYS = 30

# Declination below which the VLA cal list is not meaningful
_DEC_LIMIT_DEG = -40.0


@dataclass
class BandInfo:
    band_letter: str  # P, L, C, X, U, K, Q
    qual_A: str  # quality code in A-config (P/S/W/C/X/?)
    qual_B: str
    qual_C: str
    qual_D: str
    flux_jy: float | None = None
    uvmin_klambda: float | None = None
    uvmax_klambda: float | None = None


@dataclass
class VLACalibratorEntry:
    name: str  # e.g. "0137+331"
    alt_name: str | None  # e.g. "3C48"
    ra_j2000_deg: float
    dec_j2000_deg: float
    position_code: str  # A/B/C — position accuracy
    bands: dict[str, BandInfo] = field(default_factory=dict)


@dataclass
class ConeSearchResult:
    name: str
    alt_name: str | None
    separation_arcsec: float
    position_code: str
    bands: dict[str, BandInfo]
    note: str | None = None


@dataclass
class FieldCalMatch:
    field_name: str
    match: ConeSearchResult | None
    note: str | None = None


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

# Regex for the first line of an entry (J2000 position line)
# Format: NAME   J2000  PC RAhRAmRAs DECdDECmDECs  REF [ALTNAME]
_J2000_LINE_RE = re.compile(
    r"^(\S+)\s+J2000\s+([A-Z])\s+"
    r"(\d{1,2}h\d{2}m[\d.]+s)\s+"
    r"([+-]?\d{1,3}d\d{2}['\u2019][\d.]+[\"'\u2019]{1,2})\s+"
    r"\S+"  # position reference (e.g. Aug01)
    r"(?:\s+(\S+))?"  # optional alt name
)

# Regex for band lines — matches the quality codes portion.
# Column positions for FLUX/UVMIN/UVMAX are determined from the header line.
_BAND_LINE_RE = re.compile(
    r"^\s*\S+\s+"  # wavelength (e.g. 20cm)
    r"([A-Z])\s+"  # band letter
    r"([PSWCX?])\s+([PSWCX?])\s+([PSWCX?])\s+([PSWCX?])"  # quality codes A B C D
)

# Header line pattern for detecting column positions
_HEADER_RE = re.compile(r"FLUX\(Jy\)|UVMIN|UVMAX")


def _parse_ra_dec(ra_str: str, dec_str: str) -> tuple[float, float]:
    """Parse CASA-style RA/Dec strings to degrees using astropy."""
    # Normalise Dec: replace d with :, ' with :, strip trailing quotes
    dec_clean = dec_str.replace("d", ":").replace("\u2019", "'")
    # Handle '' or " at end
    dec_clean = re.sub(r"[\"']+$", "", dec_clean)
    dec_clean = dec_clean.replace("'", ":")

    ra_clean = ra_str.replace("h", ":").replace("m", ":").rstrip("s")

    coord = SkyCoord(ra=ra_clean, dec=dec_clean, unit=(u.hourangle, u.deg), frame="icrs")
    return coord.ra.deg, coord.dec.deg


def _find_column_positions(header_line: str) -> tuple[int, int, int]:
    """
    Find the starting column positions for FLUX, UVMIN, UVMAX from the header.
    Returns (flux_col, uvmin_col, uvmax_col).
    """
    flux_col = header_line.find("FLUX")
    uvmin_col = header_line.find("UVMIN")
    uvmax_col = header_line.find("UVMAX")
    return flux_col, uvmin_col, uvmax_col


def _extract_number_at_column(line: str, col_start: int, col_end: int) -> float | None:
    """Extract a numeric value from a fixed-width column region."""
    if col_start < 0:
        return None
    segment = line[col_start:col_end] if col_end > 0 else line[col_start:]
    segment = segment.strip()
    if not segment:
        return None
    try:
        return float(segment)
    except ValueError:
        return None


def _parse_entry(lines: list[str]) -> VLACalibratorEntry | None:
    """
    Parse one calibrator entry (a block of lines starting with the J2000 line
    and followed by band lines).
    """
    j2000_line = None
    header_line = None
    band_lines: list[str] = []

    for line in lines:
        if "J2000" in line and j2000_line is None:
            j2000_line = line
        elif _HEADER_RE.search(line):
            header_line = line
        elif _BAND_LINE_RE.match(line):
            band_lines.append(line)

    if j2000_line is None:
        return None

    m = _J2000_LINE_RE.match(j2000_line)
    if not m:
        return None

    name = m.group(1)
    pos_code = m.group(2)
    ra_str = m.group(3)
    dec_str = m.group(4)
    alt_name = m.group(5)

    try:
        ra_deg, dec_deg = _parse_ra_dec(ra_str, dec_str)
    except Exception:
        logger.debug("Failed to parse coordinates for %s: %s %s", name, ra_str, dec_str)
        return None

    # Determine column positions from header
    if header_line:
        flux_col, uvmin_col, uvmax_col = _find_column_positions(header_line)
    else:
        flux_col, uvmin_col, uvmax_col = -1, -1, -1

    bands: dict[str, BandInfo] = {}
    for bline in band_lines:
        bm = _BAND_LINE_RE.match(bline)
        if not bm:
            continue
        band_letter = bm.group(1)

        # Extract flux/uvmin/uvmax from fixed column positions
        flux = _extract_number_at_column(bline, flux_col, uvmin_col)
        uvmin = _extract_number_at_column(bline, uvmin_col, uvmax_col)
        uvmax = _extract_number_at_column(bline, uvmax_col, len(bline) + 1)

        bands[band_letter] = BandInfo(
            band_letter=band_letter,
            qual_A=bm.group(2),
            qual_B=bm.group(3),
            qual_C=bm.group(4),
            qual_D=bm.group(5),
            flux_jy=flux,
            uvmin_klambda=uvmin,
            uvmax_klambda=uvmax,
        )

    return VLACalibratorEntry(
        name=name,
        alt_name=alt_name,
        ra_j2000_deg=ra_deg,
        dec_j2000_deg=dec_deg,
        position_code=pos_code,
        bands=bands,
    )


def _scrape_vla_callist() -> list[VLACalibratorEntry]:
    """Fetch and parse the VLA calibrator list from NRAO."""
    import requests
    from bs4 import BeautifulSoup

    resp = requests.get(VLA_CALLIST_URL, timeout=30)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")

    # The calibrator data is in preformatted text blocks
    # Try <pre> tags first, then fall back to the full text
    pre_blocks = soup.find_all("pre")
    text = "\n".join(block.get_text() for block in pre_blocks) if pre_blocks else soup.get_text()

    return _parse_text(text)


def _parse_text(text: str) -> list[VLACalibratorEntry]:
    """Parse the full text of the calibrator list into entries."""
    lines = text.splitlines()
    entries: list[VLACalibratorEntry] = []

    # Split into blocks: each entry starts with a line containing "J2000"
    current_block: list[str] = []
    for line in lines:
        if "J2000" in line and current_block:
            entry = _parse_entry(current_block)
            if entry is not None:
                entries.append(entry)
            current_block = [line]
        else:
            current_block.append(line)

    # Don't forget the last block
    if current_block:
        entry = _parse_entry(current_block)
        if entry is not None:
            entries.append(entry)

    return entries


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def _entry_to_dict(entry: VLACalibratorEntry) -> dict[str, Any]:
    """Serialise a VLACalibratorEntry to a JSON-compatible dict."""
    d = asdict(entry)
    # bands is already a dict of dicts from asdict
    return d


def _entry_from_dict(d: dict[str, Any]) -> VLACalibratorEntry:
    """Deserialise a VLACalibratorEntry from a dict."""
    bands = {}
    for k, v in d.get("bands", {}).items():
        bands[k] = BandInfo(**v)
    return VLACalibratorEntry(
        name=d["name"],
        alt_name=d.get("alt_name"),
        ra_j2000_deg=d["ra_j2000_deg"],
        dec_j2000_deg=d["dec_j2000_deg"],
        position_code=d["position_code"],
        bands=bands,
    )


def _save_cache(entries: list[VLACalibratorEntry]) -> None:
    """Save catalogue to disk cache."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "timestamp": time.time(),
        "n_entries": len(entries),
        "entries": [_entry_to_dict(e) for e in entries],
    }
    CACHE_FILE.write_text(json.dumps(payload, indent=2))


def _load_cache() -> list[VLACalibratorEntry] | None:
    """Load catalogue from disk cache if fresh enough. Returns None if stale or missing."""
    if not CACHE_FILE.exists():
        return None
    try:
        payload = json.loads(CACHE_FILE.read_text())
        age_days = (time.time() - payload["timestamp"]) / 86400
        if age_days > CACHE_MAX_AGE_DAYS:
            logger.info(
                "VLA callist cache is %.0f days old (limit %d), refreshing",
                age_days,
                CACHE_MAX_AGE_DAYS,
            )
            return None
        return [_entry_from_dict(d) for d in payload["entries"]]
    except Exception:
        logger.debug("Failed to load VLA callist cache, will re-scrape", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Catalogue loading (lazy singleton)
# ---------------------------------------------------------------------------

_catalogue: list[VLACalibratorEntry] | None = None


def _load_catalogue() -> list[VLACalibratorEntry]:
    """Load the VLA calibrator catalogue, from cache or by scraping."""
    global _catalogue
    if _catalogue is not None:
        return _catalogue

    cached = _load_cache()
    if cached is not None:
        logger.info("Loaded %d VLA calibrators from cache", len(cached))
        _catalogue = cached
        return _catalogue

    logger.info("Scraping VLA calibrator list from %s", VLA_CALLIST_URL)
    entries = _scrape_vla_callist()
    logger.info("Parsed %d calibrators from VLA list", len(entries))

    if entries:
        _save_cache(entries)

    _catalogue = entries
    return _catalogue


def clear_cache() -> None:
    """Clear the in-memory and on-disk cache. Useful for testing."""
    global _catalogue
    _catalogue = None
    if CACHE_FILE.exists():
        CACHE_FILE.unlink()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def cone_search(
    ra_deg: float,
    dec_deg: float,
    radius_arcsec: float = 5.0,
) -> ConeSearchResult | None:
    """
    Search the VLA calibrator database for the nearest source within
    `radius_arcsec` of the given (RA, Dec) in J2000 degrees.

    Returns a ConeSearchResult or None if no match within the radius.
    Returns a result with a note if dec < -40° (VLA cal list not valid).
    """
    if dec_deg < _DEC_LIMIT_DEG:
        return ConeSearchResult(
            name="",
            alt_name=None,
            separation_arcsec=float("inf"),
            position_code="",
            bands={},
            note=f"Declination {dec_deg:.1f}° is below {_DEC_LIMIT_DEG}°; "
            "VLA calibrator list is not valid at this declination.",
        )

    catalogue = _load_catalogue()
    if not catalogue:
        return None

    target = SkyCoord(ra=ra_deg, dec=dec_deg, unit="deg", frame="icrs")

    cat_ra = [e.ra_j2000_deg for e in catalogue]
    cat_dec = [e.dec_j2000_deg for e in catalogue]
    cat_coords = SkyCoord(ra=cat_ra, dec=cat_dec, unit="deg", frame="icrs")

    seps = target.separation(cat_coords).arcsec
    idx_min = seps.argmin()
    min_sep = seps[idx_min]

    if min_sep > radius_arcsec:
        return None

    best = catalogue[idx_min]
    return ConeSearchResult(
        name=best.name,
        alt_name=best.alt_name,
        separation_arcsec=round(float(min_sep), 3),
        position_code=best.position_code,
        bands=best.bands,
    )


def identify_fields(
    fields: list[dict],
    radius_arcsec: float = 5.0,
) -> list[FieldCalMatch]:
    """
    Annotate a list of field records with VLA calibrator matches.

    Each field dict must have 'name', 'ra_deg', and 'dec_deg' keys.
    Returns a list of FieldCalMatch in the same order.
    """
    results: list[FieldCalMatch] = []
    for f in fields:
        ra = f.get("ra_deg")
        dec = f.get("dec_deg")
        name = f.get("name", "unknown")

        if ra is None or dec is None:
            results.append(
                FieldCalMatch(
                    field_name=name,
                    match=None,
                    note="No coordinates available for positional match",
                )
            )
            continue

        match = cone_search(ra, dec, radius_arcsec=radius_arcsec)
        results.append(
            FieldCalMatch(
                field_name=name,
                match=match,
            )
        )

    return results
