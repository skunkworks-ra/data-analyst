"""
Integration tests for ms_inspect tools.

Tests use a simulated MS created on-the-fly via casatools.simulator.
No stored test data required.

For tests against a real MS, set RADIO_MCP_TEST_MS:

    RADIO_MCP_TEST_MS=/path/to/your.ms pytest tests/integration/ -v
"""

from __future__ import annotations

import os
import shutil
import tempfile

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Simulated MS fixture
# ---------------------------------------------------------------------------

_SIM_DIR = None
_SIM_MS = None


def _create_simulated_ms(msname: str) -> str:
    """
    Create a small simulated MS using casatools.simulator.

    Based on the CASA simulation tutorial by Urvashi Rau:
    https://github.com/urvashirau/Simulation-in-CASA

    Creates a 5-antenna VLA-like array with 1 SPW (4 channels),
    RR/LL polarisation, observing a single point source for a short
    integration.
    """
    from casatools import simulator, measures, componentlist, ctsys
    from casatasks import flagdata
    from casatasks.private import simutil

    sm = simulator()
    me = measures()
    cl = componentlist()
    mysu = simutil.simutil()

    if os.path.exists(msname):
        shutil.rmtree(msname)

    sm.open(ms=msname)

    # Use a small VLA config
    antennalist = os.path.join(ctsys.resolve("alma/simmos"), "vla.d.cfg")
    (x, y, z, d, an, an2, telname, obspos) = mysu.readantenna(antennalist)

    # Use only first 5 antennas for speed
    n_ant = 5
    sm.setconfig(
        telescopename=telname,
        x=x[:n_ant],
        y=y[:n_ant],
        z=z[:n_ant],
        dishdiameter=d[:n_ant],
        mount=["alt-az"],
        antname=an[:n_ant],
        coordsystem="local",
        referencelocation=me.observatory(telname),
    )

    sm.setfeed(mode="perfect R L", pol=[""])
    sm.setspwindow(
        spwname="TestBand",
        freq="1.0GHz",
        deltafreq="0.1GHz",
        freqresolution="0.1GHz",
        nchannels=4,
        stokes="RR LL",
    )
    sm.setfield(
        sourcename="test_source",
        sourcedirection=me.direction(rf="J2000", v0="19h59m28.5s", v1="+40d44m01.5s"),
    )
    sm.setlimits(shadowlimit=0.01, elevationlimit="1deg")
    sm.setauto(autocorrwt=0.0)
    sm.settimes(
        integrationtime="60s",
        usehourangle=True,
        referencetime=me.epoch("UTC", "2019/10/4/00:00:00"),
    )
    sm.observe(sourcename="test_source", spwname="TestBand", starttime="-0.5h", stoptime="+0.5h")

    # Predict a point source so DATA is non-zero
    clname = msname + ".cl"
    if os.path.exists(clname):
        shutil.rmtree(clname)
    cl.done()
    cl.addcomponent(
        dir="J2000 19h59m28.5s +40d44m01.5s",
        flux=1.0,
        fluxunit="Jy",
        freq="1.0GHz",
        shape="point",
    )
    cl.rename(filename=clname)
    cl.done()
    sm.predict(complist=clname, incremental=False)
    sm.close()
    shutil.rmtree(clname, ignore_errors=True)

    # Ensure all flags start unflagged
    flagdata(vis=msname, mode="unflag")

    return msname


@pytest.fixture(scope="session")
def sim_ms(tmp_path_factory):
    """Session-scoped fixture: create a simulated MS once, reuse across tests."""
    tmpdir = tmp_path_factory.mktemp("sim_ms")
    msname = str(tmpdir / "test_sim.ms")
    return _create_simulated_ms(msname)


# ---------------------------------------------------------------------------
# Flag fraction tests against simulated MS
# ---------------------------------------------------------------------------


class TestAntennaFlagFractionSimulated:
    """Test ms_antenna_flag_fraction against a simulated MS."""

    def test_unflagged_ms_returns_zero(self, sim_ms):
        """A freshly simulated, unflagged MS should have 0% flags."""
        from ms_inspect.tools import flags

        result = flags.run(sim_ms)

        assert result["status"] == "ok"
        assert result["tool"] == "ms_antenna_flag_fraction"
        assert result["data"]["overall_flag_fraction"]["value"] == 0.0

        for ant in result["data"]["per_antenna"]:
            if ant["n_total_elements"] > 0:
                assert ant["flag_fraction"]["value"] == 0.0
                assert ant["n_flagged_elements"] == 0

    def test_flagged_antenna(self, sim_ms):
        """Flag one antenna completely and verify its fraction is 1.0."""
        from casatasks import flagdata

        from ms_inspect.tools import flags

        # Flag antenna 0 completely
        flagdata(vis=sim_ms, mode="manual", antenna="0")

        result = flags.run(sim_ms)
        data = result["data"]

        assert result["status"] == "ok"

        # Antenna 0 should be fully flagged
        ant0 = data["per_antenna"][0]
        assert ant0["flag_fraction"]["value"] == 1.0
        assert ant0["n_flagged_elements"] == ant0["n_total_elements"]

        # Overall flag fraction should be > 0
        assert data["overall_flag_fraction"]["value"] > 0.0

        # Unflag for subsequent tests
        flagdata(vis=sim_ms, mode="unflag")

    def test_partial_channel_flags(self, sim_ms):
        """Flag specific channels and verify fraction is correct."""
        from casatools import table

        from ms_inspect.tools import flags

        # Manually flag channels 0 and 1 (out of 4) for all rows
        tb = table()
        tb.open(sim_ms, nomodify=False)
        flag_col = tb.getcol("FLAG")  # (n_corr, n_chan, n_row)
        flag_col[:, :, :] = False  # start clean
        flag_col[:, 0:2, :] = True  # flag first 2 of 4 channels
        tb.putcol("FLAG", flag_col)
        tb.close()

        result = flags.run(sim_ms)
        data = result["data"]

        assert result["status"] == "ok"
        # 2 out of 4 channels flagged = 50%
        assert abs(data["overall_flag_fraction"]["value"] - 0.5) < 0.01

        # Unflag for subsequent tests
        tb = table()
        tb.open(sim_ms, nomodify=False)
        flag_col = tb.getcol("FLAG")
        flag_col[:, :, :] = False
        tb.putcol("FLAG", flag_col)
        tb.close()

    def test_per_antenna_consistency(self, sim_ms):
        """Overall flag fraction should be consistent with per-antenna totals."""
        from ms_inspect.tools import flags

        result = flags.run(sim_ms)
        data = result["data"]

        total_flagged = sum(a["n_flagged_elements"] for a in data["per_antenna"])
        total_elements = sum(a["n_total_elements"] for a in data["per_antenna"])

        if total_elements > 0:
            computed = total_flagged / total_elements
            assert abs(computed - data["overall_flag_fraction"]["value"]) < 1e-4

    def test_autocorrelations_excluded(self, sim_ms):
        from ms_inspect.tools import flags

        result = flags.run(sim_ms)
        assert result["data"]["autocorrelations_excluded"] is True

    def test_correct_antenna_count(self, sim_ms):
        from ms_inspect.tools import flags

        result = flags.run(sim_ms)
        # We created 5 antennas
        assert len(result["data"]["per_antenna"]) == 5

    def test_n_total_rows_positive(self, sim_ms):
        from ms_inspect.tools import flags

        result = flags.run(sim_ms)
        assert result["data"]["n_total_rows"] > 0


# ---------------------------------------------------------------------------
# Tests against a real MS (optional, skipped if RADIO_MCP_TEST_MS not set)
# ---------------------------------------------------------------------------

_TEST_MS = os.environ.get("RADIO_MCP_TEST_MS")
_SKIP = pytest.mark.skipif(_TEST_MS is None, reason="RADIO_MCP_TEST_MS not set")


@_SKIP
class TestAntennaFlagFractionReal:
    """Integration tests for ms_antenna_flag_fraction against a real MS."""

    def test_flag_fraction_returns_ok(self):
        from ms_inspect.tools import flags

        result = flags.run(_TEST_MS)
        assert result["status"] == "ok"

    def test_flag_fraction_values_in_range(self):
        from ms_inspect.tools import flags

        result = flags.run(_TEST_MS)
        data = result["data"]

        overall = data["overall_flag_fraction"]["value"]
        assert 0.0 <= overall <= 1.0

        for ant in data["per_antenna"]:
            frac = ant["flag_fraction"]["value"]
            assert 0.0 <= frac <= 1.0
            assert ant["n_flagged_elements"] <= ant["n_total_elements"]


@_SKIP
class TestPolCalFeasibilityReal:
    """Integration tests for ms_pol_cal_feasibility against a real MS."""

    def test_returns_ok(self):
        from ms_inspect.tools import pol_cal_feasibility

        result = pol_cal_feasibility.run(_TEST_MS)
        assert result["status"] == "ok"

    def test_verdict_is_valid(self):
        from ms_inspect.tools import pol_cal_feasibility

        result = pol_cal_feasibility.run(_TEST_MS)
        verdict = result["data"]["verdict"]
        assert verdict in {"FULL", "LEAKAGE_ONLY", "DEGRADED", "NOT_FEASIBLE"}

    def test_band_centre_present(self):
        from ms_inspect.tools import pol_cal_feasibility

        result = pol_cal_feasibility.run(_TEST_MS)
        band = result["data"]["band_centre_ghz"]
        assert band["value"] is not None
        assert band["value"] > 0.0

    def test_pa_spread_threshold_respected(self):
        """Relaxed threshold should not make NOT_FEASIBLE worse than strict threshold."""
        from ms_inspect.tools import pol_cal_feasibility

        strict  = pol_cal_feasibility.run(_TEST_MS, pa_spread_threshold_deg=90.0)
        relaxed = pol_cal_feasibility.run(_TEST_MS, pa_spread_threshold_deg=10.0)

        _order = {"FULL": 0, "DEGRADED": 1, "LEAKAGE_ONLY": 2, "NOT_FEASIBLE": 3}
        # A relaxed threshold should never produce a worse verdict than a strict one
        assert _order[relaxed["data"]["verdict"]] <= _order[strict["data"]["verdict"]]


@_SKIP
class TestRefAntReal:
    """Integration tests for ms_refant against a real MS."""

    def test_returns_ok(self):
        from ms_inspect.tools import refant

        result = refant.run(_TEST_MS)
        assert result["status"] == "ok"

    def test_refant_list_non_empty(self):
        from ms_inspect.tools import refant

        result = refant.run(_TEST_MS)
        assert len(result["data"]["refant_list"]["value"]) > 0

    def test_refant_is_first_in_list(self):
        from ms_inspect.tools import refant

        result = refant.run(_TEST_MS)
        data = result["data"]
        assert data["refant"]["value"] == data["refant_list"]["value"][0]

    def test_ranked_list_length_matches_n_antennas(self):
        from ms_inspect.tools import refant

        result = refant.run(_TEST_MS)
        data = result["data"]
        assert len(data["ranked"]) == data["n_antennas"]

    def test_scores_in_valid_range(self):
        from ms_inspect.tools import refant

        result = refant.run(_TEST_MS)
        n = result["data"]["n_antennas"]
        for r in result["data"]["ranked"]:
            assert 0.0 <= r["geo_score"] <= float(n)
            assert 0.0 <= r["flag_score"] <= float(n)
            assert r["combined_score"] == pytest.approx(
                r["geo_score"] + r["flag_score"], abs=1e-3
            )

    def test_ranked_descending_order(self):
        from ms_inspect.tools import refant

        result = refant.run(_TEST_MS)
        scores = [r["combined_score"] for r in result["data"]["ranked"]]
        assert scores == sorted(scores, reverse=True)


@_SKIP
class TestInitialBandpassReal:
    """Integration tests for ms_initial_bandpass against a real MS."""

    @pytest.fixture(scope="class")
    def bp_workdir(self, tmp_path_factory):
        return str(tmp_path_factory.mktemp("bp_workdir"))

    @pytest.fixture(scope="class")
    def bp_result(self, bp_workdir):
        from ms_inspect.tools.refant import run as refant_run
        from ms_modify.initial_bandpass import run as bp_run

        # Get refant from real MS
        refant_result = refant_run(_TEST_MS)
        ref_ant = refant_result["data"]["refant"]["value"]

        # Get bandpass field: first field with CALIBRATE_BANDPASS intent
        from ms_inspect.tools.fields import run as fields_run
        fields_result = fields_run(_TEST_MS)
        bp_field = None
        for f in fields_result["data"]["fields"]:
            intents = f.get("intents", {}).get("value", [])
            if any("BANDPASS" in i for i in intents):
                bp_field = f["name"]
                break

        if bp_field is None:
            pytest.skip("No CALIBRATE_BANDPASS field found in test MS")

        return bp_run(_TEST_MS, bp_field=bp_field, ref_ant=ref_ant, workdir=bp_workdir)

    def test_returns_ok(self, bp_result):
        assert bp_result["status"] == "ok"

    def test_init_gain_table_exists(self, bp_result):
        import os
        table_path = bp_result["data"]["init_gain_table"]["value"]
        assert os.path.exists(table_path)

    def test_bp_table_exists(self, bp_result):
        import os
        table_path = bp_result["data"]["bp_table"]["value"]
        assert os.path.exists(table_path)

    def test_corrected_written(self, bp_result):
        assert bp_result["data"]["corrected_written"]["value"] is True

    def test_provenance_has_three_steps(self, bp_result):
        calls = bp_result["provenance"]["casa_calls"]
        assert len(calls) == 3


@_SKIP
class TestVerifyCaltablesReal:
    """Integration tests for ms_verify_caltables against caltables from a real run."""

    def test_verify_after_bandpass(self, tmp_path):
        from ms_inspect.tools.refant import run as refant_run
        from ms_modify.initial_bandpass import run as bp_run
        from ms_inspect.tools.caltables import run as verify_run
        from ms_inspect.tools.fields import run as fields_run

        workdir = str(tmp_path)
        refant_result = refant_run(_TEST_MS)
        ref_ant = refant_result["data"]["refant"]["value"]

        fields_result = fields_run(_TEST_MS)
        bp_field = None
        for f in fields_result["data"]["fields"]:
            intents = f.get("intents", {}).get("value", [])
            if any("BANDPASS" in i for i in intents):
                bp_field = f["name"]
                break
        if bp_field is None:
            pytest.skip("No CALIBRATE_BANDPASS field found in test MS")

        bp_run(_TEST_MS, bp_field=bp_field, ref_ant=ref_ant, workdir=workdir, execute=True)

        import os
        init_gain = os.path.join(workdir, "init_gain.g")
        bp_table = os.path.join(workdir, "BP0.b")
        result = verify_run(_TEST_MS, init_gain, bp_table)
        assert result["status"] == "ok"
        assert result["data"]["caltables_valid"]["value"] is True


@_SKIP
class TestRfiChannelStatsReal:
    """Integration test for ms_rfi_channel_stats against a real MS."""

    def test_basic_run(self):
        from ms_inspect.tools.rfi import run
        result = run(_TEST_MS)
        assert result["status"] == "ok"
        assert "per_spw" in result["data"]

    def test_returns_list(self):
        from ms_inspect.tools.rfi import run
        result = run(_TEST_MS)
        assert isinstance(result["data"]["per_spw"], list)


@_SKIP
class TestFlagSummaryReal:
    """Integration test for ms_flag_summary against a real MS."""

    def test_basic_run(self):
        from ms_inspect.tools.flag_summary import run
        result = run(_TEST_MS)
        assert result["status"] == "ok"
        assert "total_flag_fraction" in result["data"]
        assert "per_antenna" in result["data"]

    def test_field_selection(self):
        from ms_inspect.tools.flag_summary import run
        from ms_inspect.tools.fields import run as fields_run
        fields_result = fields_run(_TEST_MS)
        first_field = fields_result["data"]["fields"][0]["name"]
        result = run(_TEST_MS, field=first_field)
        assert result["status"] == "ok"


@_SKIP
class TestApplyRflagReal:
    """Integration tests for ms_apply_rflag against a real MS."""

    def test_script_generation_only(self, tmp_path):
        from ms_modify.rflag import run
        result = run(_TEST_MS, workdir=str(tmp_path), execute=False)
        assert result["status"] == "ok"
        import os
        assert os.path.exists(os.path.join(str(tmp_path), "apply_rflag.py"))


@_SKIP
class TestApplyPreflagReal:
    """Integration tests for ms_apply_preflag against a real MS."""

    def test_script_generation_only(self, tmp_path):
        from ms_modify.preflag import run
        from ms_inspect.tools.fields import run as fields_run
        fields_result = fields_run(_TEST_MS)
        cal_field = fields_result["data"]["fields"][0]["name"]
        result = run(_TEST_MS, workdir=str(tmp_path), cal_fields=cal_field, execute=False)
        assert result["status"] == "ok"
        import os
        assert os.path.exists(os.path.join(str(tmp_path), "preflag_cmds.txt"))
        assert os.path.exists(os.path.join(str(tmp_path), "preflag.py"))

    def test_n_flag_commands_positive(self, tmp_path):
        from ms_modify.preflag import run
        from ms_inspect.tools.fields import run as fields_run
        fields_result = fields_run(_TEST_MS)
        cal_field = fields_result["data"]["fields"][0]["name"]
        result = run(_TEST_MS, workdir=str(tmp_path), cal_fields=cal_field, execute=False)
        assert result["data"]["n_flag_commands"]["value"] >= 3


@_SKIP
class TestGeneratePriorcalsReal:
    """Integration tests for ms_generate_priorcals against a real MS."""

    def test_script_generation_only(self, tmp_path):
        from ms_modify.priorcals import run
        result = run(_TEST_MS, workdir=str(tmp_path), execute=False)
        assert result["status"] == "ok"
        import os
        assert os.path.exists(os.path.join(str(tmp_path), "priorcals.py"))

    def test_script_contains_four_gencal_types(self, tmp_path):
        from ms_modify.priorcals import run
        run(_TEST_MS, workdir=str(tmp_path), execute=False)
        import os
        script = open(os.path.join(str(tmp_path), "priorcals.py")).read()
        for caltype in ("gc", "opac", "rq", "antpos"):
            assert caltype in script


@_SKIP
class TestVerifyPriorcalsReal:
    """Integration tests for ms_verify_priorcals."""

    def test_missing_tables_reported(self, tmp_path):
        from ms_inspect.tools.priorcals_check import run
        result = run(_TEST_MS, str(tmp_path))
        assert result["status"] == "ok"
        assert result["data"]["n_missing"]["value"] == 4
        assert not result["data"]["all_valid"]["value"]


@_SKIP
class TestSetjyReal:
    """Integration tests for ms_setjy against a real MS."""

    def test_script_generation_only(self, tmp_path):
        from ms_modify.setjy import run
        result = run(_TEST_MS, workdir=str(tmp_path), execute=False)
        assert result["status"] == "ok"
        import os
        assert os.path.exists(os.path.join(str(tmp_path), "setjy.py"))

    def test_response_has_flux_fields(self, tmp_path):
        from ms_modify.setjy import run
        result = run(_TEST_MS, workdir=str(tmp_path), execute=False)
        assert "flux_fields" in result["data"]


@_SKIP
class TestApplyInitialRflagReal:
    """Integration tests for ms_apply_initial_rflag against a real MS."""

    def test_script_generation_only(self, tmp_path):
        from ms_modify.initial_rflag import run
        result = run(_TEST_MS, workdir=str(tmp_path), execute=False)
        assert result["status"] == "ok"
        import os
        assert os.path.exists(os.path.join(str(tmp_path), "initial_rflag_cmds.txt"))
        assert os.path.exists(os.path.join(str(tmp_path), "initial_rflag.py"))

    def test_cmds_file_has_two_lines(self, tmp_path):
        from ms_modify.initial_rflag import run
        import os
        run(_TEST_MS, workdir=str(tmp_path), execute=False)
        cmds = open(os.path.join(str(tmp_path), "initial_rflag_cmds.txt")).read()
        lines = [l for l in cmds.splitlines() if l.strip()]
        assert len(lines) == 2


@_SKIP
class TestResidualStatsReal:
    """Integration tests for ms_residual_stats against a real MS."""

    def test_basic_run(self):
        from ms_inspect.tools.residual_stats import run
        result = run(_TEST_MS, field_id=0)
        assert result["status"] == "ok"
        assert "per_spw" in result["data"]
