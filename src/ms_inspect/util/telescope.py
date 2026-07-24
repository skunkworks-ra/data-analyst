"""
util/telescope.py — single telescope-resolution point.

One function reads OBSERVATION.TELESCOPE_NAME, normalises it through one alias
rule, and returns a TelescopeProfile that carries the telescope's reference data
(band table + SEFD). Every tool that needs telescope identity calls this instead
of re-reading and re-normalising the name itself.

Data (band edges, labels, SEFD, aliases) lives in per-telescope YAML under
data/telescopes/ and is validated by pydantic at load. Logic (interval band
lookup, alias resolution, ALMA receiver-band name parsing) lives here.

See MCP_DESIGN.md#DESIGN-002 for the design; this module closes DEFECT-001
(one alias rule), DEFECT-002 (one reader), DEFECT-003 (band code vs label).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from importlib.resources import files

import yaml
from pydantic import BaseModel, model_validator

log = logging.getLogger(__name__)

_DATA_PACKAGE = "ms_inspect.data.telescopes"

# ALMA encodes the receiver band directly in SPECTRAL_WINDOW.NAME, e.g.
# "X391777171#ALMA_RB_07#BB_1#SW-01#FULL_RES" -> band 7. Match RB_07 / RB7 /
# rb_7 defensively and strip the zero padding.
_ALMA_RB_RE = re.compile(r"RB_?0*(\d+)", re.IGNORECASE)


# ---------------------------------------------------------------------------
# YAML schema (validated at load)
# ---------------------------------------------------------------------------
class Band(BaseModel):
    """One receiver band: a frequency interval with a stable code and a label."""

    code: str
    min_ghz: float
    max_ghz: float
    label: str
    note: str | None = None

    @model_validator(mode="after")
    def _check_interval(self) -> Band:
        if self.max_ghz <= self.min_ghz:
            raise ValueError(
                f"band {self.code!r}: max_ghz ({self.max_ghz}) must exceed "
                f"min_ghz ({self.min_ghz})"
            )
        return self


class TelescopeSpec(BaseModel):
    """The on-disk telescope record. `sefd_jy` is keyed by band code."""

    canonical: str
    aliases: list[str]
    bands: list[Band]
    sefd_jy: dict[str, float] = {}


# ---------------------------------------------------------------------------
# Registry — loaded once at import
# ---------------------------------------------------------------------------
def _load_specs() -> dict[str, TelescopeSpec]:
    specs: dict[str, TelescopeSpec] = {}
    for entry in files(_DATA_PACKAGE).iterdir():
        if not entry.name.endswith(".yaml"):
            continue
        raw = yaml.safe_load(entry.read_text())
        spec = TelescopeSpec(**raw)  # pydantic validates; malformed YAML fails loud here
        specs[spec.canonical] = spec
    return specs


_SPECS: dict[str, TelescopeSpec] = _load_specs()

# Exact alias lookup: upper-cased alias/canonical -> spec.
_ALIAS: dict[str, TelescopeSpec] = {
    alias.upper(): spec
    for spec in _SPECS.values()
    for alias in (*spec.aliases, spec.canonical)
}


def _spec_for_name(raw: str) -> TelescopeSpec | None:
    """One alias rule: exact match first, then substring fallback.

    Substring handles free-form names like "Karl G. Jansky VLA" that contain a
    known alias token but are not catalogued verbatim.
    """
    key = raw.upper().strip()
    if key in _ALIAS:
        return _ALIAS[key]
    for alias, spec in _ALIAS.items():
        if alias in key:
            return spec
    return None


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TelescopeProfile:
    """In-memory, validated telescope identity + reference data for one MS."""

    canonical: str
    raw_name: str
    bands: tuple[Band, ...]
    sefd: dict[str, float]

    def bands_for_freq(self, freq_hz: float) -> list[Band]:
        """All bands whose interval contains freq_hz (empty in a gap; >1 on overlap)."""
        ghz = freq_hz / 1e9
        return [b for b in self.bands if b.min_ghz <= ghz < b.max_ghz]

    def _match(self, freq_hz: float, spw_name: str | None = None) -> list[Band]:
        """Resolve the band(s) for a SpW.

        For ALMA, the receiver band recorded in SPECTRAL_WINDOW.NAME
        (ALMA_RB_NN) is authoritative and disambiguates the Band 2/3 overlap;
        frequency-interval lookup is the fallback for every telescope.
        """
        if self.canonical == "ALMA" and spw_name:
            code = resolve_alma_band(spw_name)
            if code is not None:
                named = [b for b in self.bands if b.code == code]
                if named:
                    return named
        return self.bands_for_freq(freq_hz)

    def band_label(self, freq_hz: float, spw_name: str | None = None) -> str | None:
        """Human label for display. Joins overlapping bands with '/'; None in a gap."""
        matches = self._match(freq_hz, spw_name)
        if not matches:
            return None
        return "/".join(b.label for b in matches)

    def band_code(self, freq_hz: float, spw_name: str | None = None) -> str | None:
        """Stable band code for table lookups (e.g. SEFD).

        Returns None when the frequency is in a gap OR ambiguous (overlap with no
        SpW-name disambiguation), so callers key tables only when unambiguous.
        """
        matches = self._match(freq_hz, spw_name)
        return matches[0].code if len(matches) == 1 else None

    def sefd_for_freq(self, freq_hz: float, spw_name: str | None = None) -> float | None:
        """SEFD (Jy) for the band containing freq_hz, or None if unavailable."""
        code = self.band_code(freq_hz, spw_name)
        return self.sefd.get(code) if code is not None else None


def _profile_from_spec(spec: TelescopeSpec, raw_name: str) -> TelescopeProfile:
    return TelescopeProfile(
        canonical=spec.canonical,
        raw_name=raw_name,
        bands=tuple(spec.bands),
        sefd=dict(spec.sefd_jy),
    )


def profile_from_name(raw_name: str) -> TelescopeProfile | None:
    """Resolve a raw TELESCOPE_NAME string to a profile. None + warning if unknown.

    Pure (no disk/CASA) — the testable core of resolve_telescope.
    """
    spec = _spec_for_name(raw_name)
    if spec is None:
        log.warning("Unrecognised TELESCOPE_NAME %r — no telescope profile", raw_name)
        return None
    return _profile_from_spec(spec, raw_name)


def resolve_telescope(ms_path: str) -> TelescopeProfile | None:
    """Read OBSERVATION.TELESCOPE_NAME from an MS and resolve to a profile.

    Returns None (and logs a warning) if the name is missing or unrecognised;
    the caller decides whether that is fatal.
    """
    # Lazy import: keeps this module free of a hard CASA dependency for the
    # pure-Python paths (profile_from_name, band lookups) and their tests.
    from ms_inspect.util.casa_context import open_table, validate_ms_path

    p = validate_ms_path(ms_path)
    with open_table(str(p / "OBSERVATION")) as tb:
        names = tb.getcol("TELESCOPE_NAME")
        raw = str(names[0]).strip() if len(names) > 0 else ""
    return profile_from_name(raw)


def resolve_alma_band(spw_name: str | None) -> str | None:
    """Parse the ALMA receiver band code from a SPECTRAL_WINDOW.NAME string.

    Authoritative for ALMA (overrides frequency-interval lookup, which is
    ambiguous in the Band 2/3 overlap). Returns e.g. "7" for
    "...#ALMA_RB_07#...", or None if no RB token is present.
    """
    if not spw_name:
        return None
    m = _ALMA_RB_RE.search(spw_name)
    return str(int(m.group(1))) if m else None
