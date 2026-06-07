"""
Unit tests for ms_create/sdm_summary.py — ASDM XML parsing only.

No CASA required. Builds a minimal synthetic SDM directory with just the
tables the tool reads, mirroring the EVLA L-band HI structure of 13A-154.
"""

from __future__ import annotations

import pytest

from ms_create.exceptions import ASDMNotFoundError
from ms_create.sdm_summary import _asdm_array, _classify_spectral_mode, run

# ASDM ArrayTime nanoseconds for two times ~2 hours apart (MJD 56424.2).
_T0 = 4875051017799999488
_T1 = 4875058189500000256


def _write_sdm(root, *, line=True, telescope="EVLA"):
    sdm = root / "sdm"
    sdm.mkdir()
    (sdm / "ASDM.xml").write_text("<ASDM/>\n")
    (sdm / "ExecBlock.xml").write_text(
        "<ExecBlockTable><row>"
        f"<startTime>{_T0}</startTime><endTime>{_T1}</endTime>"
        "<configName>DnC</configName>"
        f"<telescopeName>{telescope}</telescopeName>"
        "<observerName>Dr. David Green</observerName>"
        "<numAntenna>27</numAntenna>"
        "</row></ExecBlockTable>"
    )
    chan_width = 15625.0 if line else 1000000.0
    n_chan = 128 if line else 64
    tot_bw = chan_width * n_chan
    (sdm / "SpectralWindow.xml").write_text(
        "<SpectralWindowTable><row>"
        f"<netSideband>USB</netSideband><numChan>{n_chan}</numChan>"
        "<refFreq>1.4194790506352072E9</refFreq>"
        f"<totBandwidth>{tot_bw}</totBandwidth>"
        "<chanFreqStart>1.4194790506352072E9</chanFreqStart>"
        f"<chanWidth>{chan_width}</chanWidth>"
        "<name>EVLA_L#A0C0#0</name>"
        "</row></SpectralWindowTable>"
    )
    (sdm / "Polarization.xml").write_text(
        "<PolarizationTable><row><corrType>1 4 RR RL LR LL</corrType></row></PolarizationTable>"
    )
    (sdm / "Scan.xml").write_text(
        "<ScanTable>"
        "<row><sourceName>G327.6+14.6</sourceName><scanIntent>1 1 OBSERVE_TARGET</scanIntent></row>"
        "<row><sourceName>J1331+3030</sourceName>"
        "<scanIntent>1 2 CALIBRATE_BANDPASS CALIBRATE_FLUX</scanIntent></row>"
        "<row><sourceName>J1454-4012</sourceName>"
        "<scanIntent>1 2 CALIBRATE_PHASE CALIBRATE_AMPLI</scanIntent></row>"
        "</ScanTable>"
    )
    (sdm / "Field.xml").write_text(
        "<FieldTable>"
        "<row><fieldName>G327.6+14.6</fieldName>"
        "<referenceDir>1 2 3.9032043334 -0.7017799935</referenceDir></row>"
        "<row><fieldName>J1331+3030</fieldName>"
        "<referenceDir>1 2 3.5392577776 0.5324852109</referenceDir></row>"
        "</FieldTable>"
    )
    return sdm


class TestAsdmArray:
    def test_scalar_intent(self):
        assert _asdm_array("1 1 OBSERVE_TARGET") == ["OBSERVE_TARGET"]

    def test_multi_intent(self):
        assert _asdm_array("1 2 CALIBRATE_PHASE CALIBRATE_AMPLI") == [
            "CALIBRATE_PHASE",
            "CALIBRATE_AMPLI",
        ]

    def test_direction(self):
        assert _asdm_array("1 2 3.90 -0.70") == ["3.90", "-0.70"]

    def test_empty(self):
        assert _asdm_array(None) == []
        assert _asdm_array("") == []


class TestClassify:
    def test_narrow_channel_is_line(self):
        mode, _ = _classify_spectral_mode(15625.0, 2e6, 128)
        assert mode == "spectral_line"

    def test_wide_channel_is_continuum(self):
        mode, _ = _classify_spectral_mode(1e6, 64e6, 64)
        assert mode == "continuum"


class TestRun:
    def test_line_dataset(self, tmp_path):
        _write_sdm(tmp_path, line=True)
        result = run(str(tmp_path))  # wrapper resolution
        d = result["data"]
        assert result["status"] == "ok"
        assert d["telescope"]["value"] == "EVLA"
        assert d["array_config"]["value"] == "DnC"
        assert d["n_antennas"]["value"] == 27
        assert d["n_spw"]["value"] == 1
        assert d["spectral_mode_inferred"]["value"] == "spectral_line"
        assert d["covers_hi_21cm"]["value"] is True
        assert "L-band" in d["band_inferred"]["value"]
        assert d["correlation_products"]["value"] == [["RR", "RL", "LR", "LL"]]
        # SN1006 at dec -40.2 deg from VLA: max elevation ~16 deg.
        assert 10.0 < d["target_max_elevation_deg"]["value"] < 20.0

    def test_continuum_dataset(self, tmp_path):
        _write_sdm(tmp_path, line=False)
        d = run(str(tmp_path))["data"]
        assert d["spectral_mode_inferred"]["value"] == "continuum"

    def test_intent_counts(self, tmp_path):
        _write_sdm(tmp_path, line=True)
        counts = run(str(tmp_path))["data"]["scan_intent_counts"]["value"]
        assert counts["OBSERVE_TARGET"] == 1
        assert counts["CALIBRATE_BANDPASS"] == 1
        assert counts["CALIBRATE_PHASE"] == 1

    def test_missing_path(self, tmp_path):
        with pytest.raises(ASDMNotFoundError):
            run(str(tmp_path / "nope"))

    def test_not_an_sdm(self, tmp_path):
        (tmp_path / "empty").mkdir()
        with pytest.raises(ASDMNotFoundError):
            run(str(tmp_path / "empty"))
