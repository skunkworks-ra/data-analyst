"""
Unit tests for driver/brief.py.

BRIEF.md is the model's entire view of the world at one wake, so two properties
matter: it must stay small as the run grows, and it must not lie about what is
on disk. Both have already broken once — a raw measurements dump truncated
mid-JSON, and a file list naming a script that did not exist.
"""

from __future__ import annotations

import json

from analyst_driver import brief as brief_mod

RECIPE = {"description": "VLA continuum.", "order": ["ms_apply_preflag", "ms_gaincal"]}


def step(n: int, tool: str = "ms_apply_preflag", result: str = "OK", **kw) -> dict:
    return {
        "step": n,
        "tool": tool,
        "params": {"field": "0"},
        "result": result,
        "headline": "flagged 8.1%",
        "duration": "0h04m00s",
        **kw,
    }


def render(run_dir, whitelist, fake_ms, **kw):
    defaults = dict(
        run_dir=run_dir,
        run_id="r1",
        step=2,
        goal="Image 3C286.",
        instrument="VLA · 27 antennas",
        ms_rows=[
            {"path": str(fake_ms), "name": "fake.ms", "fields": "0 3C286", "flag_fraction": 0.081}
        ],
        active_ms=fake_ms,
        whitelist=whitelist,
        recipe=RECIPE,
        steps=[],
        tools_done=[],
        last=None,
        last_step_dir=None,
        verdict_text="verifier: PASS — 1 of 1 checks ran",
        prev_rationale="",
        refusals=[],
        decision_path=run_dir / "decisions" / "002.json",
    )
    return brief_mod.render(**{**defaults, **kw}).read_text()


# -- structure -----------------------------------------------------------


def test_all_seven_sections_are_present(run_dir, whitelist, fake_ms):
    text = render(run_dir, whitelist, fake_ms)
    for n in range(1, 8):
        assert f"## {n}." in text


def test_it_names_the_output_path_the_model_must_write(run_dir, whitelist, fake_ms):
    assert "decisions/002.json" in render(run_dir, whitelist, fake_ms)


def test_the_stable_sections_do_not_change_between_wakes(run_dir, whitelist, fake_ms):
    """Sections 1-4 must be byte-identical so a prefix cache can hit them."""
    a = render(run_dir, whitelist, fake_ms, step=2, steps=[])
    b = render(run_dir, whitelist, fake_ms, step=9, steps=[step(1)])
    head = lambda t: t.split("## 5.")[0].split("## 1.")[1]  # noqa: E731
    assert head(a) == head(b)


def test_it_tells_the_model_not_to_send_driver_owned_parameters(run_dir, whitelist, fake_ms):
    assert "Do not pass ms_path, workdir or execute" in render(run_dir, whitelist, fake_ms)


def test_the_usual_order_is_labelled_as_a_map(run_dir, whitelist, fake_ms):
    text = render(run_dir, whitelist, fake_ms)
    assert "a map, not a rule" in text
    assert "Leave this order when the data says to" in text


# -- section 2, the data -------------------------------------------------


def test_an_unknown_flag_fraction_says_unknown_rather_than_zero(run_dir, whitelist, fake_ms):
    rows = [{"path": str(fake_ms), "name": "fake.ms", "fields": "0 3C286", "flag_fraction": None}]
    assert "unknown" in render(run_dir, whitelist, fake_ms, ms_rows=rows)


def test_the_active_ms_is_marked(run_dir, whitelist, fake_ms):
    rows = [
        {"path": str(fake_ms), "name": "cal.ms", "fields": "0", "flag_fraction": 0.1},
        {"path": "/elsewhere/target.ms", "name": "target.ms", "fields": "1", "flag_fraction": 0.2},
    ]
    text = render(run_dir, whitelist, fake_ms, ms_rows=rows)
    assert "cal.ms" in text and "target.ms" in text
    assert text.count("YES") == 1


# -- section 3, preconditions -------------------------------------------


def test_an_unmet_precondition_is_shown_with_its_reason(run_dir, whitelist, fake_ms):
    text = render(run_dir, whitelist, fake_ms)
    assert "ms_fluxscale" in text
    assert "NOT MET" in text


def test_a_precondition_becomes_met_once_its_input_exists(run_dir, whitelist, fake_ms):
    before = render(run_dir, whitelist, fake_ms)
    (run_dir / "steps" / "003-ms_gaincal").mkdir(parents=True)
    (run_dir / "steps" / "003-ms_gaincal" / "phase.G").write_text("")
    after = render(run_dir, whitelist, fake_ms)
    assert before.count("NOT MET") == after.count("NOT MET") + 1


# -- section 5, history --------------------------------------------------


def test_history_shows_one_line_per_step(run_dir, whitelist, fake_ms):
    text = render(run_dir, whitelist, fake_ms, steps=[step(1), step(2, "ms_gaincal")])
    body = text.split("## 5.")[1].split("## 6.")[0]
    assert "ms_apply_preflag" in body
    assert "ms_gaincal" in body


def test_history_folds_older_successful_steps(run_dir, whitelist, fake_ms):
    steps = [step(n) for n in range(1, 26)]
    body = render(run_dir, whitelist, fake_ms, steps=steps, full_tail=10).split("## 5.")[1]
    assert "1-15 all OK" in body


def test_history_stays_flat_as_the_run_grows(run_dir, whitelist, fake_ms):
    """The one section that would otherwise grow without bound."""
    short = render(run_dir, whitelist, fake_ms, steps=[step(n) for n in range(1, 12)], full_tail=10)
    long = render(run_dir, whitelist, fake_ms, steps=[step(n) for n in range(1, 80)], full_tail=10)
    assert len(long) < len(short) * 1.5


def test_a_failed_step_is_never_folded_away(run_dir, whitelist, fake_ms):
    steps = [step(n) for n in range(1, 20)]
    steps[4] = step(5, result="FAILED")
    body = render(run_dir, whitelist, fake_ms, steps=steps, full_tail=5).split("## 5.")[1]
    assert "FAILED" in body


def test_an_empty_history_says_so(run_dir, whitelist, fake_ms):
    assert "this is the first step" in render(run_dir, whitelist, fake_ms, steps=[])


# -- section 6, the last step -------------------------------------------


def test_measurements_are_rendered_as_numbers_not_raw_json(run_dir, whitelist, fake_ms):
    d = run_dir / "steps" / "001-ms_apply_preflag"
    d.mkdir(parents=True)
    (d / "measurements.json").write_text(
        json.dumps({"total_flag_fraction": {"value": 0.081, "flag": "COMPLETE"}})
    )
    text = render(run_dir, whitelist, fake_ms, last=step(1), last_step_dir=d)
    assert "total_flag_fraction = 0.081" in text
    assert '"flag": "COMPLETE"' not in text


def test_a_long_array_becomes_a_count_and_a_pointer(run_dir, whitelist, fake_ms):
    """Regression: per_antenna on a full VLA run swamped the whole brief."""
    d = run_dir / "steps" / "001-ms_apply_preflag"
    d.mkdir(parents=True)
    (d / "measurements.json").write_text(
        json.dumps(
            {
                "per_antenna": [
                    {"antenna_name": f"ea{i:02d}", "flag_fraction": 0.1} for i in range(27)
                ]
            }
        )
    )
    text = render(run_dir, whitelist, fake_ms, last=step(1), last_step_dir=d)
    assert "per_antenna: 27 entries — see measurements.json" in text
    assert "ea26" not in text


def test_a_short_array_is_shown_inline(run_dir, whitelist, fake_ms):
    d = run_dir / "steps" / "001-ms_apply_preflag"
    d.mkdir(parents=True)
    (d / "measurements.json").write_text(
        json.dumps({"per_field": [{"field_name": "3C286", "flag_fraction": 0.31}]})
    )
    text = render(run_dir, whitelist, fake_ms, last=step(1), last_step_dir=d)
    assert "field_name=3C286" in text


def test_the_file_list_names_files_that_exist(run_dir, whitelist, fake_ms):
    """Regression: the brief used to promise script.py, which no tool writes."""
    d = run_dir / "steps" / "001-ms_apply_preflag"
    d.mkdir(parents=True)
    (d / "measurements.json").write_text("{}")
    (d / "preflag.py").write_text("")
    text = render(run_dir, whitelist, fake_ms, last=step(1), last_step_dir=d)
    listed = text.split("files in 001-ms_apply_preflag/:")[1].split("\n")[0]
    for name in listed.split(" · "):
        assert (d / name.strip()).exists(), f"brief names {name!r} but it is not on disk"


def test_the_previous_rationale_is_carried_in_full(run_dir, whitelist, fake_ms):
    d = run_dir / "steps" / "001-ms_apply_preflag"
    d.mkdir(parents=True)
    long_reason = "Field 1 sits at 22 degrees. " * 6
    text = render(
        run_dir, whitelist, fake_ms, last=step(1), last_step_dir=d, prev_rationale=long_reason
    )
    assert long_reason.strip() in text


def test_the_verifier_verdict_is_included(run_dir, whitelist, fake_ms):
    d = run_dir / "steps" / "001-ms_apply_preflag"
    d.mkdir(parents=True)
    text = render(
        run_dir,
        whitelist,
        fake_ms,
        last=step(1),
        last_step_dir=d,
        verdict_text="verifier: FAIL — 1 of 1 checks ran",
    )
    assert "verifier: FAIL — 1 of 1 checks ran" in text


def test_a_failed_step_gets_a_file_and_a_line_number(run_dir, whitelist, fake_ms):
    d = run_dir / "steps" / "001-ms_apply_preflag"
    d.mkdir(parents=True)
    (d / "casa.log").write_text("INFO ok\nINFO ok\nSEVERE flagdata blew up\n")
    text = render(run_dir, whitelist, fake_ms, last=step(1, result="FAILED"), last_step_dir=d)
    assert "casa.log:3" in text
    assert "blew up" not in text, "log content must never be inlined"


def test_first_severe_prefers_the_casa_log(run_dir):
    d = run_dir / "steps" / "001"
    d.mkdir(parents=True)
    (d / "casa.log").write_text("a\nSEVERE here\n")
    (d / "stderr").write_text("SEVERE and here\n")
    assert brief_mod.first_severe(d) == "casa.log:2"


def test_first_severe_falls_back_to_stderr(run_dir):
    d = run_dir / "steps" / "001"
    d.mkdir(parents=True)
    (d / "stderr").write_text("boom\nSEVERE in stderr\n")
    assert brief_mod.first_severe(d) == "stderr:2"


def test_first_severe_is_honest_when_it_finds_nothing(run_dir):
    d = run_dir / "steps" / "001"
    d.mkdir(parents=True)
    (d / "stdout").write_text("all quiet\n")
    assert "no SEVERE line found" in brief_mod.first_severe(d)


# -- section 7, refusals -------------------------------------------------


def test_no_refusal_renders_as_none(run_dir, whitelist, fake_ms):
    assert "(none)" in render(run_dir, whitelist, fake_ms).split("## 7.")[1]


def test_a_refusal_is_shown_with_its_reason(run_dir, whitelist, fake_ms):
    text = render(run_dir, whitelist, fake_ms, refusals=["- ms_setjy has no parameter 'flux'"])
    body = text.split("## 7.")[1]
    assert "has no parameter 'flux'" in body
    assert "Do not resubmit the same decision" in body


# -- size ----------------------------------------------------------------


def test_the_brief_stays_small(run_dir, whitelist, fake_ms):
    """A brief that balloons defeats the whole design."""
    d = run_dir / "steps" / "001-ms_apply_preflag"
    d.mkdir(parents=True)
    (d / "measurements.json").write_text(
        json.dumps({"per_antenna": [{"antenna_name": f"ea{i:02d}"} for i in range(27)]})
    )
    text = render(
        run_dir,
        whitelist,
        fake_ms,
        steps=[step(n) for n in range(1, 60)],
        last=step(59),
        last_step_dir=d,
    )
    assert len(text) < 12000, f"brief is {len(text)} bytes"
