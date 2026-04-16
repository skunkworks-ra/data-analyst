"""
Unit tests for ms_calsol_stats (src/ms_inspect/tools/calsol_stats.py).

No CASA installation required — all casatools calls mocked via open_table.
Tests cover: path validation, metadata readers, per-slice stats, G/B/K
output shape, flagged-fraction accounting, and the response envelope.
"""

from __future__ import annotations

import math
from unittest.mock import MagicMock, call, patch

import numpy as np
import pytest

from ms_inspect.tools import calsol_stats

# ---------------------------------------------------------------------------
# Mock builders
# ---------------------------------------------------------------------------

_ANT_NAMES = ["ea01", "ea02", "ea03"]
_N_ANT = len(_ANT_NAMES)
_N_CORR = 2
_N_CHAN_G = 1
_N_CHAN_B = 4


def _make_tb(
    *,
    keywords: dict | None = None,
    getcol_side: dict | None = None,
    nrows: int = 0,
    query_result: "MagicMock | None" = None,
) -> MagicMock:
    """Build a mock table context manager."""
    tb = MagicMock()
    tb.__enter__ = MagicMock(return_value=tb)
    tb.__exit__ = MagicMock(return_value=False)
    tb.nrows.return_value = nrows

    if keywords is not None:
        tb.getkeywords.return_value = keywords

    if getcol_side is not None:

        def _getcol(col):
            return getcol_side[col]

        tb.getcol.side_effect = _getcol

    if query_result is not None:
        tb.query.return_value = query_result

    return tb


def _make_subtable(
    *,
    nrows: int,
    ant1: np.ndarray,
    cparam: np.ndarray | None = None,
    fparam: np.ndarray | None = None,
    flag: np.ndarray,
    snr: np.ndarray,
) -> MagicMock:
    """Build a mock subtable (result of tb.query())."""
    sub = MagicMock()
    sub.nrows.return_value = nrows
    sub.close = MagicMock()

    def _getcol(col):
        if col == "ANTENNA1":
            return ant1
        if col == "CPARAM":
            return cparam
        if col == "FPARAM":
            return fparam
        if col == "FLAG":
            return flag
        if col == "SNR":
            return snr
        raise KeyError(col)

    sub.getcol.side_effect = _getcol
    return sub


def _g_table_data(n_rows_per_ant: int = 3):
    """
    G table slice: 2 corr, 1 chan, n_rows_per_ant rows per antenna.
    All solutions unflagged, amplitude ~1, phase ~0.
    """
    n_rows = _N_ANT * n_rows_per_ant
    ant1 = np.repeat(np.arange(_N_ANT), n_rows_per_ant)
    cparam = np.ones((_N_CORR, _N_CHAN_G, n_rows), dtype=complex)
    flag = np.zeros((_N_CORR, _N_CHAN_G, n_rows), dtype=bool)
    snr = np.full((_N_CORR, _N_CHAN_G, n_rows), 30.0)
    return ant1, cparam, flag, snr


def _b_table_data():
    """B table slice: 2 corr, 4 chan, 1 solution per antenna."""
    n_rows = _N_ANT
    ant1 = np.arange(_N_ANT)
    cparam = np.ones((_N_CORR, _N_CHAN_B, n_rows), dtype=complex) * 0.9
    flag = np.zeros((_N_CORR, _N_CHAN_B, n_rows), dtype=bool)
    snr = np.full((_N_CORR, _N_CHAN_B, n_rows), 50.0)
    return ant1, cparam, flag, snr


def _k_table_data():
    """K table slice: 2 corr, 1 chan, 1 solution per antenna."""
    n_rows = _N_ANT
    ant1 = np.arange(_N_ANT)
    # FPARAM: [n_corr, 1, n_rows], delays in ns
    fparam = np.array([[[1.0, 2.0, 3.0]], [[1.1, 2.1, 3.1]]], dtype=float)
    flag = np.zeros((2, 1, n_rows), dtype=bool)
    snr = np.full((2, 1, n_rows), 20.0)
    return ant1, fparam, flag, snr


# ---------------------------------------------------------------------------
# Path validation
# ---------------------------------------------------------------------------


class TestPathValidation:
    def test_missing_path_returns_error(self, tmp_path):
        result = calsol_stats.run(str(tmp_path / "nonexistent.g"))
        assert result["status"] == "error"
        assert result["error_type"] == "CALTABLE_NOT_FOUND"

    def test_file_not_dir_returns_error(self, tmp_path):
        f = tmp_path / "notatable.g"
        f.write_text("x")
        result = calsol_stats.run(str(f))
        assert result["status"] == "error"
        assert result["error_type"] == "CALTABLE_NOT_FOUND"


# ---------------------------------------------------------------------------
# Metadata helpers
# ---------------------------------------------------------------------------


class TestReadTableType:
    def test_g_jones(self, tmp_path):
        tbl = tmp_path / "gain.g"
        tbl.mkdir()
        tb = _make_tb(keywords={"VisCal": "G Jones"})
        with patch("ms_inspect.tools.calsol_stats.open_table", return_value=tb):
            result = calsol_stats._read_table_type(str(tbl))
        assert result == "G"

    def test_b_jones(self, tmp_path):
        tbl = tmp_path / "BP.b"
        tbl.mkdir()
        tb = _make_tb(keywords={"VisCal": "B Jones"})
        with patch("ms_inspect.tools.calsol_stats.open_table", return_value=tb):
            result = calsol_stats._read_table_type(str(tbl))
        assert result == "B"

    def test_k_jones(self, tmp_path):
        tbl = tmp_path / "delay.k"
        tbl.mkdir()
        tb = _make_tb(keywords={"VisCal": "K Jones"})
        with patch("ms_inspect.tools.calsol_stats.open_table", return_value=tb):
            result = calsol_stats._read_table_type(str(tbl))
        assert result == "K"

    def test_missing_viscal_returns_empty(self, tmp_path):
        tbl = tmp_path / "weird.ct"
        tbl.mkdir()
        tb = _make_tb(keywords={})
        with patch("ms_inspect.tools.calsol_stats.open_table", return_value=tb):
            result = calsol_stats._read_table_type(str(tbl))
        assert result == ""


# ---------------------------------------------------------------------------
# Stat helpers
# ---------------------------------------------------------------------------


class TestStatHelpers:
    def test_phase_rms_deg_zero(self):
        phases = np.zeros(10)
        assert calsol_stats._phase_rms_deg(phases) == pytest.approx(0.0)

    def test_phase_rms_deg_known(self):
        phases = np.array([0.1, -0.1, 0.1, -0.1])  # radians
        expected = float(np.sqrt(np.mean(phases**2))) * (180.0 / math.pi)
        assert calsol_stats._phase_rms_deg(phases) == pytest.approx(expected)

    def test_phase_rms_all_nan(self):
        phases = np.full(5, math.nan)
        assert math.isnan(calsol_stats._phase_rms_deg(phases))

    def test_safe_mean_ignores_nan(self):
        arr = np.array([1.0, 2.0, math.nan, 3.0])
        assert calsol_stats._safe_mean(arr) == pytest.approx(2.0)

    def test_safe_mean_all_nan(self):
        assert math.isnan(calsol_stats._safe_mean(np.full(3, math.nan)))

    def test_safe_std_single_value(self):
        # std of one valid element is nan (no spread)
        arr = np.array([5.0, math.nan])
        assert math.isnan(calsol_stats._safe_std(arr))


# ---------------------------------------------------------------------------
# _process_slice — G table
# ---------------------------------------------------------------------------


class TestProcessSliceG:
    def _run(self, tmp_path, ant1, cparam, flag, snr):
        tbl = tmp_path / "gain.g"
        tbl.mkdir()
        sub = _make_subtable(
            nrows=len(ant1), ant1=ant1, cparam=cparam, flag=flag, snr=snr
        )
        tb = _make_tb(nrows=len(ant1), query_result=sub)
        with patch("ms_inspect.tools.calsol_stats.open_table", return_value=tb):
            return calsol_stats._process_slice(
                str(tbl), spw=0, field=0,
                ant_names=_ANT_NAMES, table_type="G", n_chan_max=1
            )

    def test_all_antennas_present(self, tmp_path):
        ant1, cparam, flag, snr = _g_table_data()
        result = self._run(tmp_path, ant1, cparam, flag, snr)
        assert set(result.keys()) == {0, 1, 2}

    def test_amplitude_mean_near_one(self, tmp_path):
        ant1, cparam, flag, snr = _g_table_data()
        result = self._run(tmp_path, ant1, cparam, flag, snr)
        for a in range(_N_ANT):
            assert result[a]["amp_mean"] == pytest.approx(1.0, abs=1e-6)

    def test_phase_rms_near_zero(self, tmp_path):
        ant1, cparam, flag, snr = _g_table_data()
        result = self._run(tmp_path, ant1, cparam, flag, snr)
        for a in range(_N_ANT):
            assert result[a]["phase_rms_deg"] == pytest.approx(0.0, abs=1e-6)

    def test_unflagged_fraction_zero(self, tmp_path):
        ant1, cparam, flag, snr = _g_table_data()
        result = self._run(tmp_path, ant1, cparam, flag, snr)
        for a in range(_N_ANT):
            assert result[a]["flagged_frac"] == pytest.approx(0.0)

    def test_flagged_solutions_counted(self, tmp_path):
        ant1, cparam, flag, snr = _g_table_data(n_rows_per_ant=2)
        # flag all rows for antenna 0
        flag[:, :, ant1 == 0] = True
        result = self._run(tmp_path, ant1, cparam, flag, snr)
        assert result[0]["flagged_frac"] == pytest.approx(1.0)
        assert result[1]["flagged_frac"] == pytest.approx(0.0)

    def test_snr_mean(self, tmp_path):
        ant1, cparam, flag, snr = _g_table_data()
        result = self._run(tmp_path, ant1, cparam, flag, snr)
        for a in range(_N_ANT):
            assert result[a]["snr_mean"] == pytest.approx(30.0)

    def test_empty_slice_returns_empty(self, tmp_path):
        tbl = tmp_path / "gain.g"
        tbl.mkdir()
        sub = MagicMock()
        sub.nrows.return_value = 0
        sub.close = MagicMock()
        tb = _make_tb(nrows=0, query_result=sub)
        with patch("ms_inspect.tools.calsol_stats.open_table", return_value=tb):
            result = calsol_stats._process_slice(
                str(tbl), spw=0, field=0,
                ant_names=_ANT_NAMES, table_type="G", n_chan_max=1
            )
        assert result == {}

    def test_sub_close_always_called(self, tmp_path):
        """Verify sub.close() is called even when nrows == 0."""
        tbl = tmp_path / "gain.g"
        tbl.mkdir()
        sub = MagicMock()
        sub.nrows.return_value = 0
        sub.close = MagicMock()
        tb = _make_tb(query_result=sub)
        with patch("ms_inspect.tools.calsol_stats.open_table", return_value=tb):
            calsol_stats._process_slice(
                str(tbl), spw=0, field=0,
                ant_names=_ANT_NAMES, table_type="G", n_chan_max=1
            )
        sub.close.assert_called_once()


# ---------------------------------------------------------------------------
# _process_slice — B table
# ---------------------------------------------------------------------------


class TestProcessSliceB:
    def _run(self, tmp_path):
        tbl = tmp_path / "BP.b"
        tbl.mkdir()
        ant1, cparam, flag, snr = _b_table_data()
        sub = _make_subtable(
            nrows=_N_ANT, ant1=ant1, cparam=cparam, flag=flag, snr=snr
        )
        tb = _make_tb(nrows=_N_ANT, query_result=sub)
        with patch("ms_inspect.tools.calsol_stats.open_table", return_value=tb):
            return calsol_stats._process_slice(
                str(tbl), spw=0, field=0,
                ant_names=_ANT_NAMES, table_type="B", n_chan_max=_N_CHAN_B
            )

    def test_amp_array_length(self, tmp_path):
        result = self._run(tmp_path)
        for a in range(_N_ANT):
            assert len(result[a]["amp_array"]) == _N_CHAN_B

    def test_amp_array_values(self, tmp_path):
        result = self._run(tmp_path)
        for a in range(_N_ANT):
            for v in result[a]["amp_array"]:
                assert v == pytest.approx(0.9, abs=1e-6)

    def test_amp_array_padded_with_nan(self, tmp_path):
        tbl = tmp_path / "BP.b"
        tbl.mkdir()
        ant1, cparam, flag, snr = _b_table_data()
        # Use n_chan=2 but n_chan_max=4 → last 2 entries should be NaN
        cparam_short = cparam[:, :2, :]
        flag_short = flag[:, :2, :]
        snr_short = snr[:, :2, :]
        sub = _make_subtable(
            nrows=_N_ANT, ant1=ant1, cparam=cparam_short,
            flag=flag_short, snr=snr_short
        )
        tb = _make_tb(nrows=_N_ANT, query_result=sub)
        with patch("ms_inspect.tools.calsol_stats.open_table", return_value=tb):
            result = calsol_stats._process_slice(
                str(tbl), spw=0, field=0,
                ant_names=_ANT_NAMES, table_type="B", n_chan_max=4
            )
        for a in range(_N_ANT):
            arr = result[a]["amp_array"]
            assert not math.isnan(arr[0])
            assert not math.isnan(arr[1])
            assert math.isnan(arr[2])
            assert math.isnan(arr[3])


# ---------------------------------------------------------------------------
# _process_slice — K table
# ---------------------------------------------------------------------------


class TestProcessSliceK:
    def _run(self, tmp_path):
        tbl = tmp_path / "delay.k"
        tbl.mkdir()
        ant1, fparam, flag, snr = _k_table_data()
        sub = _make_subtable(
            nrows=_N_ANT, ant1=ant1, fparam=fparam, flag=flag, snr=snr
        )
        tb = _make_tb(nrows=_N_ANT, query_result=sub)
        with patch("ms_inspect.tools.calsol_stats.open_table", return_value=tb):
            return calsol_stats._process_slice(
                str(tbl), spw=0, field=0,
                ant_names=_ANT_NAMES, table_type="K", n_chan_max=1
            )

    def test_delay_ns_shape(self, tmp_path):
        result = self._run(tmp_path)
        for a in range(_N_ANT):
            # [n_corr, n_rows] — 1 row per antenna
            assert len(result[a]["delay_ns"]) == _N_CORR

    def test_delay_ns_values(self, tmp_path):
        result = self._run(tmp_path)
        # antenna 0: corr0=1.0, corr1=1.1
        assert result[0]["delay_ns"][0][0] == pytest.approx(1.0)
        assert result[0]["delay_ns"][1][0] == pytest.approx(1.1)

    def test_flagged_delay_is_nan(self, tmp_path):
        tbl = tmp_path / "delay.k"
        tbl.mkdir()
        ant1, fparam, flag, snr = _k_table_data()
        flag[:, :, 0] = True  # flag antenna 0
        sub = _make_subtable(
            nrows=_N_ANT, ant1=ant1, fparam=fparam, flag=flag, snr=snr
        )
        tb = _make_tb(nrows=_N_ANT, query_result=sub)
        with patch("ms_inspect.tools.calsol_stats.open_table", return_value=tb):
            result = calsol_stats._process_slice(
                str(tbl), spw=0, field=0,
                ant_names=_ANT_NAMES, table_type="K", n_chan_max=1
            )
        for corr in range(_N_CORR):
            assert math.isnan(result[0]["delay_ns"][corr][0])


# ---------------------------------------------------------------------------
# run() — full G table end-to-end
# ---------------------------------------------------------------------------


def _patch_all_for_g(tbl_path, spw_ids=(0,), field_ids=(0, 1)):
    """
    Construct all mocks needed to run calsol_stats.run() on a G table.
    Returns (patch_target, side_effect_fn).
    """
    ant1, cparam, flag, snr = _g_table_data()

    ant_tb = _make_tb(getcol_side={"NAME": np.array(_ANT_NAMES)})
    field_tb = _make_tb(getcol_side={"NAME": np.array(["3C286", "J0137"])})
    kw_tb = _make_tb(keywords={"VisCal": "G Jones"})
    axis_tb = _make_tb(
        getcol_side={
            "SPECTRAL_WINDOW_ID": np.array([0, 0, 0, 0, 0, 0]),
            "FIELD_ID": np.array([0, 0, 0, 1, 1, 1]),
        }
    )
    sub = _make_subtable(
        nrows=_N_ANT * 3, ant1=ant1, cparam=cparam, flag=flag, snr=snr
    )
    slice_tb = _make_tb(nrows=_N_ANT * 3, query_result=sub)

    calls = []

    def _open_table(path, **kwargs):
        p = str(path)
        calls.append(p)
        if p.endswith("ANTENNA"):
            return ant_tb
        if p.endswith("FIELD"):
            return field_tb
        if p == str(tbl_path):
            # first call: keywords; second: axis ids; subsequent: slices
            n = sum(1 for c in calls if c == str(tbl_path))
            if n == 1:
                return kw_tb
            if n == 2:
                return axis_tb
            return slice_tb
        return slice_tb

    return _open_table


class TestRunG:
    def test_status_ok(self, tmp_path):
        tbl = tmp_path / "gain.g"
        tbl.mkdir()
        ot = _patch_all_for_g(tbl)
        with patch("ms_inspect.tools.calsol_stats.open_table", side_effect=ot):
            result = calsol_stats.run(str(tbl))
        assert result["status"] == "ok"

    def test_table_type_field(self, tmp_path):
        tbl = tmp_path / "gain.g"
        tbl.mkdir()
        ot = _patch_all_for_g(tbl)
        with patch("ms_inspect.tools.calsol_stats.open_table", side_effect=ot):
            result = calsol_stats.run(str(tbl))
        assert result["data"]["table_type"]["value"] == "G"

    def test_axis_metadata(self, tmp_path):
        tbl = tmp_path / "gain.g"
        tbl.mkdir()
        ot = _patch_all_for_g(tbl)
        with patch("ms_inspect.tools.calsol_stats.open_table", side_effect=ot):
            result = calsol_stats.run(str(tbl))
        d = result["data"]
        assert d["n_antennas"]["value"] == _N_ANT
        assert d["n_spw"]["value"] == 1
        assert d["n_field"]["value"] == 2
        assert d["ant_names"]["value"] == _ANT_NAMES
        assert d["field_names"]["value"] == ["3C286", "J0137"]

    def test_flagged_frac_shape(self, tmp_path):
        tbl = tmp_path / "gain.g"
        tbl.mkdir()
        ot = _patch_all_for_g(tbl)
        with patch("ms_inspect.tools.calsol_stats.open_table", side_effect=ot):
            result = calsol_stats.run(str(tbl))
        ff = result["data"]["flagged_frac"]["value"]
        # shape [n_ant=3, n_spw=1, n_field=2]
        assert len(ff) == _N_ANT
        assert len(ff[0]) == 1
        assert len(ff[0][0]) == 2

    def test_overall_flagged_frac_zero(self, tmp_path):
        tbl = tmp_path / "gain.g"
        tbl.mkdir()
        ot = _patch_all_for_g(tbl)
        with patch("ms_inspect.tools.calsol_stats.open_table", side_effect=ot):
            result = calsol_stats.run(str(tbl))
        assert result["data"]["overall_flagged_frac"]["value"] == pytest.approx(0.0)

    def test_no_antennas_lost(self, tmp_path):
        tbl = tmp_path / "gain.g"
        tbl.mkdir()
        ot = _patch_all_for_g(tbl)
        with patch("ms_inspect.tools.calsol_stats.open_table", side_effect=ot):
            result = calsol_stats.run(str(tbl))
        assert result["data"]["n_antennas_lost"]["value"] == 0
        assert result["data"]["antennas_lost"]["value"] == []

    def test_g_table_has_no_delay_fields(self, tmp_path):
        tbl = tmp_path / "gain.g"
        tbl.mkdir()
        ot = _patch_all_for_g(tbl)
        with patch("ms_inspect.tools.calsol_stats.open_table", side_effect=ot):
            result = calsol_stats.run(str(tbl))
        assert "delay_ns" not in result["data"]
        assert "delay_rms_ns" not in result["data"]

    def test_g_table_has_amp_phase_fields(self, tmp_path):
        tbl = tmp_path / "gain.g"
        tbl.mkdir()
        ot = _patch_all_for_g(tbl)
        with patch("ms_inspect.tools.calsol_stats.open_table", side_effect=ot):
            result = calsol_stats.run(str(tbl))
        d = result["data"]
        assert "amp_mean" in d
        assert "phase_rms_deg" in d
        assert "amp_array" not in d  # amp_array is B-only
