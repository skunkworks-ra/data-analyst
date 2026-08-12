"""
Unit tests for the input, the roles and the planned-output handover.

A run may start from a Measurement Set or from an ASDM, and it produces
several MSs as it goes. Two properties matter: the kind is detected from disk
rather than from the flag that was typed, and every MS the driver adopts was
declared by the tool that made it — never found by globbing, because a run
produces several split MSs and a glob cannot say which is which.
"""

from __future__ import annotations

from pathlib import Path

from analyst_driver import driver
from analyst_driver import state as state_mod


def make_ms(tmp_path, name="x.ms"):
    ms = tmp_path / name
    ms.mkdir()
    (ms / "table.info").write_text("Type = Measurement Set\nSubType = \n")
    return ms


def make_asdm(tmp_path, name="uid___A002_X1.asdm"):
    sdm = tmp_path / name
    sdm.mkdir()
    (sdm / "ASDM.xml").write_text("<ASDM/>")
    return sdm


# -- kind detection ------------------------------------------------------


def test_a_measurement_set_is_detected(tmp_path):
    assert driver.detect_input_kind(make_ms(tmp_path)) == state_mod.KIND_MS


def test_an_asdm_is_detected(tmp_path):
    assert driver.detect_input_kind(make_asdm(tmp_path)) == state_mod.KIND_ASDM


def test_something_else_is_refused(tmp_path):
    other = tmp_path / "notes"
    other.mkdir()
    (other / "readme.txt").write_text("hello")
    assert driver.detect_input_kind(other) == ""


def test_a_caltable_is_not_mistaken_for_an_ms(tmp_path):
    """A caltable also has a table.info. Only the Measurement Set line counts."""
    ct = tmp_path / "phase.G"
    ct.mkdir()
    (ct / "table.info").write_text("Type = Calibration\nSubType = G Jones\n")
    assert driver.detect_input_kind(ct) == ""


def test_detection_ignores_the_name(tmp_path):
    """--ms pointing at an ASDM still does the right thing: the kind comes
    from what is on disk, never from the flag the user typed."""
    misnamed = make_asdm(tmp_path, "looks_like_an.ms")
    assert driver.detect_input_kind(misnamed) == state_mod.KIND_ASDM


# -- role resolution -----------------------------------------------------


def test_each_tool_declares_a_role(whitelist):
    valid = {"raw", "calibrators", "target", "none"}
    for name, entry in whitelist["tools"].items():
        assert entry.get("ms_role") in valid, f"{name} has no usable ms_role"


def test_a_solve_reads_the_calibrators_ms(whitelist, tmp_path):
    st = state_mod.RunState(run_id="r", goal="g", recipe="vla_continuum", started_utc="t")
    st.record_ms(state_mod.ROLE_RAW, str(tmp_path / "raw.ms"))
    st.record_ms(state_mod.ROLE_CALIBRATORS, str(tmp_path / "calibrators.ms"))
    assert driver.resolve_ms(st, whitelist, "ms_gaincal").name == "calibrators.ms"


def test_applycal_reads_the_raw_ms(whitelist, tmp_path):
    """It applies the solutions to the target fields, which live in the raw MS."""
    st = state_mod.RunState(run_id="r", goal="g", recipe="vla_continuum", started_utc="t")
    st.record_ms(state_mod.ROLE_RAW, str(tmp_path / "raw.ms"))
    st.record_ms(state_mod.ROLE_CALIBRATORS, str(tmp_path / "calibrators.ms"))
    assert driver.resolve_ms(st, whitelist, "ms_applycal").name == "raw.ms"


def test_a_caltable_tool_gets_no_ms(whitelist):
    st = state_mod.RunState(run_id="r", goal="g", recipe="vla_continuum", started_utc="t")
    st.record_ms(state_mod.ROLE_RAW, "/w/raw.ms")
    assert driver.resolve_ms(st, whitelist, "ms_flag_caltable") is None


def test_role_resolution_is_empty_before_any_import(whitelist):
    st = state_mod.RunState(
        run_id="r", goal="g", recipe="vla_continuum", started_utc="t", input_kind="asdm"
    )
    assert driver.resolve_ms(st, whitelist, "ms_gaincal") is None


# -- planned outputs -----------------------------------------------------


def test_a_planned_ms_is_adopted_into_the_registry(tmp_path):
    st = state_mod.RunState(run_id="r", goal="g", recipe="vla_continuum", started_utc="t")
    cal = make_ms(tmp_path, "calibrators.ms")
    adopted, problem = driver.adopt_outputs(
        st, [{"role": "calibrators", "path": str(cal), "kind": "ms"}]
    )
    assert problem == ""
    assert st.ms_for(state_mod.ROLE_CALIBRATORS) == str(cal)
    assert adopted == ["calibrators=calibrators.ms"]


def test_a_planned_output_that_never_appeared_is_a_problem(tmp_path):
    """The step exited zero but produced nothing. That is a failure, not a
    silent success — the next step would otherwise read a stale MS."""
    st = state_mod.RunState(run_id="r", goal="g", recipe="vla_continuum", started_utc="t")
    adopted, problem = driver.adopt_outputs(
        st, [{"role": "calibrators", "path": str(tmp_path / "never.ms"), "kind": "ms"}]
    )
    assert adopted == []
    assert "did not produce" in problem
    assert st.ms_registry == {}


def test_a_non_ms_output_is_checked_but_not_registered(tmp_path):
    """The online flag file must exist, but it is not an MS and has no role."""
    st = state_mod.RunState(run_id="r", goal="g", recipe="vla_continuum", started_utc="t")
    flags = tmp_path / "x.flagonline.txt"
    flags.write_text("")
    adopted, problem = driver.adopt_outputs(
        st, [{"role": "online_flags", "path": str(flags), "kind": "file"}]
    )
    assert problem == ""
    assert adopted == []
    assert st.ms_registry == {}


def test_no_planned_outputs_is_not_a_problem(tmp_path):
    """Most tools produce caltables, not MSs, and declare nothing."""
    st = state_mod.RunState(run_id="r", goal="g", recipe="vla_continuum", started_utc="t")
    assert driver.adopt_outputs(st, []) == ([], "")


def test_preflag_reports_calibrators_ms_as_a_planned_output(tmp_path):
    """The whole handover depends on this being reported with execute=False,
    which is the only mode the driver ever uses. The path was already computed
    inside the tool; it simply was not reported until the script had run."""
    from ms_modify import preflag

    ms = make_ms(tmp_path, "raw.ms")
    workdir = tmp_path / "processed"
    workdir.mkdir()
    data = preflag.run(ms_path=str(ms), workdir=str(workdir), cal_fields="0,1", execute=False)[
        "data"
    ]
    assert data["planned_outputs"] == [
        {"role": "calibrators", "path": str(workdir / "calibrators.ms"), "kind": "ms"}
    ]


def test_import_asdm_reports_the_ms_and_the_flag_file(tmp_path):
    from ms_create import import_asdm

    sdm = make_asdm(tmp_path, "uid___A002_Xtest")
    workdir = tmp_path / "processed"
    workdir.mkdir()
    data = import_asdm.run(asdm_path=str(sdm), workdir=str(workdir), execute=False)["data"]
    planned = data["planned_outputs"]
    assert {p["role"] for p in planned} == {"raw", "online_flags"}
    ms = next(p for p in planned if p["role"] == "raw")
    assert ms["kind"] == "ms"
    assert Path(ms["path"]).parent == workdir, "the MS must land in the shared workdir"


def test_a_planned_path_is_absolute(tmp_path):
    """The driver checks these paths from a different working directory than
    the one the script ran in."""
    from ms_modify import preflag

    ms = make_ms(tmp_path, "raw.ms")
    workdir = tmp_path / "processed"
    workdir.mkdir()
    data = preflag.run(ms_path=str(ms), workdir=str(workdir), cal_fields="0", execute=False)["data"]
    for item in data["planned_outputs"]:
        assert Path(item["path"]).is_absolute()


# -- the recipe map ------------------------------------------------------


def test_the_import_step_is_dropped_on_an_ms_run(driver_dir):
    import yaml

    recipes = yaml.safe_load((driver_dir / "recipe.yaml").read_text())["recipes"]
    st = state_mod.RunState(
        run_id="r", goal="g", recipe="vla_continuum", started_utc="t", input_kind="ms"
    )
    order = driver._rendered_recipe(recipes["vla_continuum"], st)["order"]
    assert "ms_import_asdm" not in order


def test_the_import_step_is_kept_on_an_asdm_run(driver_dir):
    import yaml

    recipes = yaml.safe_load((driver_dir / "recipe.yaml").read_text())["recipes"]
    st = state_mod.RunState(
        run_id="r", goal="g", recipe="vla_continuum", started_utc="t", input_kind="asdm"
    )
    order = driver._rendered_recipe(recipes["vla_continuum"], st)["order"]
    assert order[0] == "ms_import_asdm"


def test_every_recipe_offers_the_import_step(driver_dir):
    """One list serves both starting points, so all of them must carry it."""
    import yaml

    recipes = yaml.safe_load((driver_dir / "recipe.yaml").read_text())["recipes"]
    for key, recipe in recipes.items():
        assert recipe["order"][0] == "ms_import_asdm", f"{key} cannot start from an ASDM"


def test_every_recipe_step_is_whitelisted(driver_dir, whitelist):
    """A map naming a tool that cannot be called sends the model nowhere."""
    import yaml

    recipes = yaml.safe_load((driver_dir / "recipe.yaml").read_text())["recipes"]
    for key, recipe in recipes.items():
        for tool in recipe["order"]:
            assert tool in whitelist["tools"], f"{key} names unknown tool {tool}"
