"""
Unit tests for the CASA-free logic in tools/pol_cal_conditions.py.

What these cover: the two pure decision helpers — the frequency-dependent
effective role of a catalogued source, and the Df poltype derived from source
knowledge alone — plus the constants the tool ships as provenance.

What they do NOT cover: anything that opens an MS. The field enumeration,
the PA-spread ranking, and the absence of the gate fields on a real payload
are exercised in tests/integration/test_tools.py::TestPolCalConditionsReal.
"""

from __future__ import annotations

import math

from ms_inspect.tools.pol_cal_conditions import (
    LOW_POL_FRAC_PCT,
    PA_SPREAD_NRAO_RECOMMENDED_DEG,
    PA_SPREAD_PRACTICAL_FLOOR_DEG,
    _df_poltype_from_source_knowledge,
    _effective_role_at_band,
)
from ms_inspect.util.pol_calibrators import lookup_pol


class TestEffectiveRoleAtBand:
    def test_uncatalogued_source_is_unknown(self):
        assert _effective_role_at_band(None, 1.5) == "unknown"

    def test_unreadable_band_is_unknown(self):
        entry = lookup_pol("3C286")
        assert entry is not None
        assert _effective_role_at_band(entry, float("nan")) == "unknown"

    def test_3c286_is_polarized_at_l_band(self):
        """3C286 has ~10% polarization with a defined EVPA — angle-cal regime."""
        entry = lookup_pol("3C286")
        assert entry is not None
        assert _effective_role_at_band(entry, 1.5) == "angle_known_pol"

    def test_3c84_is_a_zero_pol_leakage_cal_at_l_band(self):
        """NRAO: 3C84 is 'low polarization (<1%)' at the low bands."""
        entry = lookup_pol("3C84")
        assert entry is not None
        assert _effective_role_at_band(entry, 1.5) == "leakage_zero_pol"

    def test_role_is_frequency_dependent_for_3c147(self):
        """3C147 is low-pol below ~10 GHz and polarized above it — not a fixed role."""
        entry = lookup_pol("3C147")
        assert entry is not None
        low = _effective_role_at_band(entry, 1.5)
        high = _effective_role_at_band(entry, 22.0)
        assert low == "leakage_zero_pol"
        assert high != "leakage_zero_pol"


class TestDfPoltypeFromSourceKnowledge:
    def test_zero_pol_source_gives_df(self):
        poltype, basis = _df_poltype_from_source_knowledge("leakage_zero_pol")
        assert poltype == "Df"
        assert "leakage_zero_pol" in basis

    def test_known_pol_source_gives_df(self):
        for role in ("angle_known_pol", "known_pol"):
            poltype, basis = _df_poltype_from_source_knowledge(role)
            assert poltype == "Df"
            assert role in basis

    def test_unknown_pol_source_gives_df_qu(self):
        poltype, basis = _df_poltype_from_source_knowledge("unknown")
        assert poltype == "Df+QU"
        assert "solved jointly" in basis

    def test_poltype_does_not_depend_on_pa_coverage(self):
        """The helper takes no PA argument at all — coverage cannot change the poltype.

        This is the regression the rename was for: the old code returned None
        below a PA threshold, which conflated 'how well constrained' with
        'which poltype applies'.
        """
        import inspect

        params = inspect.signature(_df_poltype_from_source_knowledge).parameters
        assert list(params) == ["role_at_band"]

    def test_unrecognised_role_returns_none_with_a_stated_reason(self):
        poltype, basis = _df_poltype_from_source_knowledge("something_new")
        assert poltype is None
        assert "something_new" in basis


class TestConstants:
    def test_reference_levels_are_ordered_and_finite(self):
        assert math.isfinite(PA_SPREAD_NRAO_RECOMMENDED_DEG)
        assert math.isfinite(PA_SPREAD_PRACTICAL_FLOOR_DEG)
        assert PA_SPREAD_PRACTICAL_FLOOR_DEG < PA_SPREAD_NRAO_RECOMMENDED_DEG

    def test_low_pol_cut_matches_the_nrao_wording(self):
        assert LOW_POL_FRAC_PCT == 1.0
