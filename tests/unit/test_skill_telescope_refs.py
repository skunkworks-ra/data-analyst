"""
Drift guard: skill files must not carry their own copy of telescope data.

The telescope YAML profiles (src/ms_inspect/data/telescopes/*.yaml) are the
single source of truth for band edges and SEFDs. Skill markdown used to restate
both, and it drifted: the uGMRT band ladder in 02-orientation.md was off by one
receiver, and the SEFD table listed uGMRT values under VLA band letters. The
gridder rule separately matched on the raw name `EVLA` after the profile layer
started returning the canonical `VLA`, silently degrading VLA mosaics.

These tests assert the *absence* of duplication rather than the agreement of two
copies, with one exception: the cross-telescope SEFD comparison table in
11-imaging.md is deliberately retained for at-a-glance reference, so it is
checked against `sefd_jy` value by value.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ms_inspect.util.telescope import _SPECS

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_DIR = REPO_ROOT / ".claude" / "skills" / "radio-interferometry"
TELESCOPE_DATA_DIR = REPO_ROOT / "src" / "ms_inspect" / "data" / "telescopes"

# Skill files carrying telescope-derived reference material: each must point the
# reader at the profiles rather than answering from its own text.
PROFILE_POINTER_FILES = [
    SKILLS_DIR / "02-orientation.md",
    SKILLS_DIR / "wildcat" / "00-core.md",
    SKILLS_DIR / "08-pband-specifics.md",
]

# The band-identification sections proper. These held the tables that drifted,
# so they must carry no frequency ranges at all. 08-pband-specifics.md is
# deliberately absent: its ranges are correlator setup (16 x 16 MHz SPWs) and
# known-RFI subbands, which are not receiver edges and belong in the skill.
NO_BAND_EDGE_FILES = [
    SKILLS_DIR / "02-orientation.md",
    SKILLS_DIR / "wildcat" / "00-core.md",
]

SEFD_TABLE_FILES = [
    SKILLS_DIR / "11-imaging.md",
    SKILLS_DIR / "wildcat" / "11-imaging.md",
]

GRIDDER_FILES = SEFD_TABLE_FILES


def _skill_files() -> list[Path]:
    return sorted(SKILLS_DIR.rglob("*.md"))


# ---------------------------------------------------------------------------
# The referenced profiles actually exist
# ---------------------------------------------------------------------------
class TestProfileReferencesResolve:
    def test_data_dir_exists(self) -> None:
        assert TELESCOPE_DATA_DIR.is_dir(), (
            f"{TELESCOPE_DATA_DIR} missing — skills reference this path directly."
        )

    @pytest.mark.parametrize("path", PROFILE_POINTER_FILES, ids=lambda p: p.name)
    def test_band_reference_points_at_profiles(self, path: Path) -> None:
        """Files that dropped their band tables must say where the edges live."""
        text = path.read_text(encoding="utf-8")
        assert "src/ms_inspect/data/telescopes/" in text, (
            f"{path.name} no longer points readers at the telescope profiles; "
            "without the pointer the agent will fall back on memorized edges."
        )

    def test_referenced_yaml_paths_exist(self) -> None:
        """Every concrete <telescope>.yaml named in a skill must be a real file."""
        pattern = re.compile(r"src/ms_inspect/data/telescopes/([A-Za-z0-9_]+)\.yaml")
        missing: list[str] = []
        for path in _skill_files():
            for name in pattern.findall(path.read_text(encoding="utf-8")):
                if not (TELESCOPE_DATA_DIR / f"{name}.yaml").is_file():
                    missing.append(f"{path.name} -> {name}.yaml")
        assert not missing, f"Skill files reference non-existent profiles: {missing}"


# ---------------------------------------------------------------------------
# No second copy of the band edges
# ---------------------------------------------------------------------------
class TestNoDuplicatedBandEdges:
    # "120–250 MHz", "1.75-3.5 GHz", "26.5–40 GHz" — a range with a unit.
    RANGE = re.compile(
        r"\b\d+(?:\.\d+)?\s*[–-]\s*\d+(?:\.\d+)?\s*(?:MHz|GHz)\b",
        re.IGNORECASE,
    )

    @pytest.mark.parametrize("path", NO_BAND_EDGE_FILES, ids=lambda p: p.name)
    def test_band_tables_carry_no_frequency_ranges(self, path: Path) -> None:
        """
        The band-identification sections must not restate edges. Scoped to those
        files: elsewhere a frequency range is legitimate (correlator setup, a
        known-RFI subband), and a repo-wide ban would be noise.
        """
        found = self.RANGE.findall(path.read_text(encoding="utf-8"))
        # The retained SEFD comparison table annotates uGMRT band codes with
        # their ranges for disambiguation; those files are not in this list.
        assert not found, (
            f"{path.name} restates band edges {found} — these belong only in "
            "src/ms_inspect/data/telescopes/*.yaml. Replace with a pointer."
        )

    def test_ugmrt_band_one_is_not_resurrected(self) -> None:
        """uGMRT has no commissioned Band 1; the old tables invented one."""
        offenders = [
            path.name
            for path in _skill_files()
            if re.search(r"\|\s*Band 1\s*\|", path.read_text(encoding="utf-8"))
        ]
        assert not offenders, (
            f"{offenders} tabulate a uGMRT 'Band 1'. The profile defines bands "
            "2-5 only (GMRT_specs.pdf, 15 Dec 2025)."
        )


# ---------------------------------------------------------------------------
# The retained SEFD comparison table must match the profiles
# ---------------------------------------------------------------------------
class TestSefdTableMatchesProfiles:
    ROW = re.compile(
        r"^\|\s*(VLA|MeerKAT|uGMRT|ALMA)\s*\|\s*([^|]+?)\s*\|\s*(\d+(?:\.\d+)?)\s*\|",
        re.MULTILINE,
    )

    @staticmethod
    def _band_code(cell: str) -> str:
        """'3 (250-500 MHz)' -> '3'; 'L' -> 'L'."""
        return cell.split("(")[0].strip()

    @pytest.mark.parametrize("path", SEFD_TABLE_FILES, ids=lambda p: p.name)
    def test_every_tabulated_sefd_matches_yaml(self, path: Path) -> None:
        rows = self.ROW.findall(path.read_text(encoding="utf-8"))
        assert rows, f"{path.name}: SEFD comparison table not found or reshaped."

        mismatches: list[str] = []
        for telescope, band_cell, value in rows:
            code = self._band_code(band_cell)
            spec = _SPECS.get(telescope)
            if spec is None:
                mismatches.append(f"{telescope}: no profile")
                continue
            expected = spec.sefd_jy.get(code)
            if expected is None:
                mismatches.append(
                    f"{telescope} band {code}: tabulated {value}, absent from sefd_jy"
                )
            elif float(value) != expected:
                mismatches.append(f"{telescope} band {code}: table {value} != yaml {expected}")
        assert not mismatches, f"{path.name} SEFD drift: {mismatches}"

    @pytest.mark.parametrize("path", SEFD_TABLE_FILES, ids=lambda p: p.name)
    def test_table_is_complete(self, path: Path) -> None:
        """Every sefd_jy entry in every profile appears in the comparison table."""
        rows = self.ROW.findall(path.read_text(encoding="utf-8"))
        tabulated = {(t, self._band_code(b)) for t, b, _ in rows}
        missing = [
            f"{name} band {code}"
            for name, spec in _SPECS.items()
            for code in spec.sefd_jy
            if (name, code) not in tabulated
        ]
        assert not missing, f"{path.name} omits SEFD entries present in the profiles: {missing}"

    @pytest.mark.parametrize("path", SEFD_TABLE_FILES, ids=lambda p: p.name)
    def test_points_at_authoritative_source(self, path: Path) -> None:
        text = path.read_text(encoding="utf-8")
        assert "sefd_jy" in text, (
            f"{path.name} must name `sefd_jy` as the authoritative source so the "
            "table is not mistaken for it."
        )


# ---------------------------------------------------------------------------
# Telescope-name matching uses canonical names
# ---------------------------------------------------------------------------
class TestCanonicalNameMatching:
    CANONICAL = {spec.canonical for spec in _SPECS.values()}

    @pytest.mark.parametrize("path", GRIDDER_FILES, ids=lambda p: p.name)
    def test_gridder_rule_matches_canonical_names(self, path: Path) -> None:
        """
        The Step 6 gridder rule compares against ms_inspect's `telescope` field,
        which is the canonical name. Matching on an alias silently sends VLA
        mosaics down the unsupported-telescope path.
        """
        text = path.read_text(encoding="utf-8")
        rules = re.findall(r"telescope (?:in|NOT in) `\{([^}]*)\}`", text)
        assert rules, f"{path.name}: gridder telescope rule not found or reshaped."

        bad: list[str] = []
        for rule in rules:
            for name in (n.strip() for n in rule.split(",")):
                if name and name not in self.CANONICAL:
                    bad.append(f"{name!r} (canonical names: {sorted(self.CANONICAL)})")
        assert not bad, (
            f"{path.name} matches telescope on non-canonical name(s): {bad}. "
            "resolve_telescope() returns the canonical name."
        )
