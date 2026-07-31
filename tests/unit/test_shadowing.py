"""
Unit tests for tools/shadowing.py's report parser.

What these cover: _parse_shadow_report's handling of the return shapes
flagdata can produce, including the empty-dict case observed for real.

What they do NOT cover: the flagdata call itself, the FLAG_CMD read, or the
assembled envelope. The empty-dict input below is not invented — it is what
`flagdata(vis=<real MS>, mode='shadow', tolerance=0.0, action='calculate',
savepars=False, flagbackup=False)` returned from casatasks 6.7.5.18 against
3C391 D-config on 2026-07-31.
"""

from __future__ import annotations

from ms_inspect.tools.shadowing import _parse_shadow_report


class TestEmptyReport:
    def test_empty_dict_is_not_measured(self):
        """The observed real-world case. Must not degrade to (0, 0)."""
        n_total, n_flagged, note = _parse_shadow_report({})

        assert n_total is None
        assert n_flagged is None
        assert note is not None
        assert "NOT measured" in note

    def test_none_is_not_measured(self):
        n_total, n_flagged, note = _parse_shadow_report(None)
        assert (n_total, n_flagged) == (None, None)
        assert note is not None

    def test_dict_without_total_is_not_measured(self):
        n_total, n_flagged, note = _parse_shadow_report({"antenna": {}})
        assert (n_total, n_flagged) == (None, None)
        assert "no usable 'total'" in note


class TestWellFormedReport:
    def test_nested_total_record(self):
        n_total, n_flagged, note = _parse_shadow_report(
            {"total": {"total": 216417024.0, "flagged": 74199808.0}}
        )
        assert n_total == 216417024
        assert n_flagged == 74199808
        assert note is None

    def test_flat_total_and_flagged(self):
        """The shape flagdata(mode='summary') really uses: floats at top level.

        Confirmed against the same MS — summary returns 'total' and 'flagged'
        as top-level floats, not as a nested record.
        """
        n_total, n_flagged, note = _parse_shadow_report(
            {"total": 216417024.0, "flagged": 74199808.0, "antenna": {}}
        )
        assert n_total == 216417024
        assert n_flagged == 74199808
        assert note is None

    def test_partial_nested_record_is_rejected_not_defaulted(self):
        """A 'total' record missing 'flagged' must not read as zero flagged."""
        n_total, n_flagged, note = _parse_shadow_report({"total": {"total": 100}})

        assert (n_total, n_flagged) == (None, None)
        assert "without 'total'/'flagged' keys" in note

    def test_genuine_zero_is_preserved(self):
        """Measured-as-zero and not-measured must stay distinguishable."""
        n_total, n_flagged, note = _parse_shadow_report(
            {"total": {"total": 216417024.0, "flagged": 0.0}}
        )
        assert n_total == 216417024
        assert n_flagged == 0
        assert note is None
