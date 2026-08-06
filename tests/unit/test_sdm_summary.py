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


def _write_alma_sdm(root):
    """
    Minimal ALMA SDM with the window ORDER that caused the band defect: the
    water-vapour windows come first, so "first window wins" reports Band 5 for a
    Band 6 dataset.
    """
    sdm = root / "sdm"
    sdm.mkdir()
    (sdm / "ASDM.xml").write_text("<ASDM/>\n")
    (sdm / "ExecBlock.xml").write_text(
        "<ExecBlockTable><row>"
        f"<startTime>{_T0}</startTime><endTime>{_T1}</endTime>"
        "<configName>A</configName>"
        "<telescopeName>ALMA</telescopeName>"
        "<observerName>knakanishi</observerName>"
        "<numAntenna>31</numAntenna>"
        "</row></ExecBlockTable>"
    )
    rows = []
    # Two water-vapour windows at 183 GHz — Band 5 territory — listed FIRST.
    for i in range(2):
        rows.append(
            "<row><netSideband>DSB</netSideband><numChan>4</numChan>"
            "<refFreq>1.833E11</refFreq><totBandwidth>7.5E9</totBandwidth>"
            "<chanFreqStart>1.833E11</chanFreqStart><chanWidth>1.5E9</chanWidth>"
            f"<name>WVR#{'NOMINAL' if i == 0 else f'Antenna_{i - 1}'}</name></row>"
        )
    # A frequency-averaged science window, then a full-resolution one.
    rows.append(
        "<row><netSideband>LSB</netSideband><numChan>1</numChan>"
        "<refFreq>2.2487E11</refFreq><totBandwidth>1.78E9</totBandwidth>"
        "<chanFreqStart>2.2487E11</chanFreqStart><chanWidth>1.78E9</chanWidth>"
        "<name>ALMA_RB_06#BB_1#SW-01#CH_AVG</name></row>"
    )
    rows.append(
        "<row><netSideband>LSB</netSideband><numChan>64</numChan>"
        "<refFreq>2.25E11</refFreq><totBandwidth>2.0E9</totBandwidth>"
        "<chanFreqStart>2.25E11</chanFreqStart><chanWidth>3.125E7</chanWidth>"
        "<name>ALMA_RB_06#BB_1#SW-01#FULL_RES</name></row>"
    )
    (sdm / "SpectralWindow.xml").write_text(
        "<SpectralWindowTable>" + "".join(rows) + "</SpectralWindowTable>"
    )
    (sdm / "Polarization.xml").write_text(
        "<PolarizationTable><row><corrType>1 4 XX XY YX YY</corrType></row></PolarizationTable>"
    )
    (sdm / "Scan.xml").write_text(
        "<ScanTable><row><sourceName>3c286</sourceName>"
        "<scanIntent>1 1 OBSERVE_TARGET</scanIntent></row></ScanTable>"
    )
    (sdm / "Field.xml").write_text(
        "<FieldTable><row><fieldName>3c286</fieldName>"
        "<referenceDir>1 2 3.5392577776 0.5324852109</referenceDir></row></FieldTable>"
    )
    return sdm


class TestBandFromScienceWindowOnly:
    """
    Regression guard. The band was inferred from the FIRST window with a
    non-zero reference frequency. On ALMA the first 32 windows are the 183 GHz
    water-vapour radiometer, so a Band 6 dataset reported "Band 5".
    """

    def test_water_vapour_windows_do_not_drive_the_band(self, tmp_path):
        _write_alma_sdm(tmp_path)
        d = run(str(tmp_path))["data"]
        assert d["band_inferred"]["value"] == "Band 6 (211–275 GHz)"

    def test_note_records_which_window_and_that_selection_is_structural(self, tmp_path):
        _write_alma_sdm(tmp_path)
        note = run(str(tmp_path))["data"]["band_inferred"]["note"]
        assert "science windows" in note
        # Before the MS exists there are no intents, and the note must admit it.
        assert "structural" in note

    def test_frequency_averaged_window_does_not_drive_the_band(self, tmp_path):
        """The CH_AVG window sits at 224.87 GHz — same band here, so the band
        alone cannot prove it was skipped. Assert on the chosen window index."""
        _write_alma_sdm(tmp_path)
        note = run(str(tmp_path))["data"]["band_inferred"]["note"]
        assert "SPW 3" in note  # the FULL_RES window, not the CH_AVG one at 2

    def test_non_alma_band_inference_is_unchanged(self, tmp_path):
        """The restriction is ALMA-only; VLA must keep the old note and value."""
        _write_sdm(tmp_path, line=True, telescope="EVLA")
        d = run(str(tmp_path))["data"]
        assert d["band_inferred"]["note"] == "from SPW reference frequency"
        assert d["band_inferred"]["value"] is not None
