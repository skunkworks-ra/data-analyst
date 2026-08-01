"""
Unit tests for the per-channel payload bound in tools/spw_amp_severity.py.

What these cover: _bound_per_chan_payload's decisions — when it truncates,
what it drops, what it leaves alone, what it records, and that it refuses a
malformed input instead of degrading. The dicts are hand-built, so they test
the bounding logic only.

What they do NOT cover: that the real run() builds per_spw in this shape, or
the measurement itself. Those need a real MS; ms_spw_amp_severity has no
integration coverage.
"""

from __future__ import annotations

import json

import pytest

from ms_inspect.tools.spw_amp_severity import _bound_per_chan_payload


def _data(n_spw: int, n_chan: int) -> dict:
    """A payload in the shape run() builds, with the fields that must survive."""
    return {
        "datacolumn": "CORRECTED_DATA",
        "n_spw": n_spw,
        "per_spw": [
            {
                "spw_id": s,
                "n_channels": n_chan,
                "band_floor": {"value": 0.5 + s, "flag": "COMPLETE"},
                "severity": 1.0 + s,
                "estimated_discardable_frac": {"value": 0.01, "flag": "COMPLETE"},
                "per_chan": [{"chan": c, "median": 0.5} for c in range(n_chan)],
            }
            for s in range(n_spw)
        ],
    }


class TestUnderTheBound:
    def test_nothing_dropped_and_no_sidecar_written(self, tmp_path):
        sidecar = tmp_path / "out.json"
        warnings: list[str] = []
        data = _bound_per_chan_payload(
            _data(2, 10), max_per_chan_records=100, sidecar_path=str(sidecar), warnings=warnings
        )

        assert data["per_chan_truncated"]["value"] is False
        assert data["n_per_chan_records"] == 20
        assert all(len(e["per_chan"]) == 10 for e in data["per_spw"])
        assert "detail_path" not in data
        assert warnings == []
        # A read-only tool must not write next to the caller's MS for nothing.
        assert not sidecar.exists()

    def test_zero_disables_the_bound(self, tmp_path):
        sidecar = tmp_path / "out.json"
        data = _bound_per_chan_payload(
            _data(4, 5000), max_per_chan_records=0, sidecar_path=str(sidecar), warnings=[]
        )

        assert data["per_chan_truncated"]["value"] is False
        assert all("per_chan" in e for e in data["per_spw"])
        assert not sidecar.exists()


class TestOverTheBound:
    def test_per_chan_dropped_from_every_spw(self, tmp_path):
        sidecar = tmp_path / "out.json"
        data = _bound_per_chan_payload(
            _data(3, 1000), max_per_chan_records=100, sidecar_path=str(sidecar), warnings=[]
        )

        assert all("per_chan" not in e for e in data["per_spw"])
        # All-or-nothing: no SpW keeps a partial spectrum.
        assert all(e["n_per_chan_omitted"] == 1000 for e in data["per_spw"])

    def test_per_spw_aggregates_are_never_capped(self, tmp_path):
        """These are what 13-postcal-rfi-flagging.md consumes."""
        sidecar = tmp_path / "out.json"
        data = _bound_per_chan_payload(
            _data(3, 1000), max_per_chan_records=100, sidecar_path=str(sidecar), warnings=[]
        )

        assert len(data["per_spw"]) == 3
        for s, entry in enumerate(data["per_spw"]):
            assert entry["spw_id"] == s
            assert entry["band_floor"]["value"] == 0.5 + s
            assert entry["severity"] == 1.0 + s
            assert entry["estimated_discardable_frac"]["value"] == 0.01

    def test_truncation_is_visible(self, tmp_path):
        sidecar = tmp_path / "out.json"
        data = _bound_per_chan_payload(
            _data(3, 1000), max_per_chan_records=100, sidecar_path=str(sidecar), warnings=[]
        )

        trunc = data["per_chan_truncated"]
        assert trunc["value"] is True
        assert trunc["flag"] == "PARTIAL"
        assert "3000" in trunc["note"]  # the count dropped
        assert data["n_per_chan_records_dropped"] == 3000
        assert data["detail_path"]["value"] == str(sidecar)
        assert "max_per_chan_records" in data["detail_note"]  # how to get the rest

    def test_sidecar_holds_the_full_detail(self, tmp_path):
        sidecar = tmp_path / "out.json"
        _bound_per_chan_payload(
            _data(3, 1000), max_per_chan_records=100, sidecar_path=str(sidecar), warnings=[]
        )

        written = json.loads(sidecar.read_text())
        assert len(written["per_spw"]) == 3
        assert all(len(e["per_chan"]) == 1000 for e in written["per_spw"])

    def test_unwritable_sidecar_still_truncates_and_says_so(self, tmp_path):
        bad = tmp_path / "no-such-dir" / "out.json"
        warnings: list[str] = []
        data = _bound_per_chan_payload(
            _data(3, 1000), max_per_chan_records=100, sidecar_path=str(bad), warnings=warnings
        )

        assert data["per_chan_truncated"]["value"] is True
        assert data["detail_path"]["flag"] == "UNAVAILABLE"
        assert any("Could not write per-channel sidecar" in w for w in warnings)
        assert "max_per_chan_records=0" in data["detail_note"]


class TestMalformedInputRaises:
    """A missing key must not degrade to a count of zero."""

    def test_missing_per_spw(self, tmp_path):
        with pytest.raises(KeyError, match="per_spw"):
            _bound_per_chan_payload({"datacolumn": "DATA"}, 100, str(tmp_path / "o.json"), [])

    def test_entry_without_per_chan(self, tmp_path):
        data = _data(2, 10)
        del data["per_spw"][1]["per_chan"]

        with pytest.raises(KeyError, match="spw_id=1"):
            _bound_per_chan_payload(data, 100, str(tmp_path / "o.json"), [])

    def test_per_chan_wrong_type(self, tmp_path):
        data = _data(1, 10)
        data["per_spw"][0]["per_chan"] = {"chan": 0}

        with pytest.raises(KeyError, match="per_chan"):
            _bound_per_chan_payload(data, 100, str(tmp_path / "o.json"), [])
