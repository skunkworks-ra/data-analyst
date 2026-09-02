"""
Unit tests for analyst_driver/backends.py — the tool ban in particular.

The fixture in tests/unit/fixtures/ is the REAL system/init event captured from
turn 1 of the 2026-08-31 G55 run, not a hand-written one. That matters here more
than usual: the run was configured with Bash absent from allowed_tools and made
101 Bash calls anyway, and the reason is visible only in this event. A synthetic
fixture would have been written to match what we believed, which is exactly how
the ban came to be documented as working while it did not.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from analyst_driver.backends import ClaudeBackend

FIXTURES = Path(__file__).parent / "fixtures"

#: The list the G55 run actually passed as --allowedTools.
G55_ALLOWED = [
    "mcp__ms-inspect",
    "mcp__ms-modify",
    "mcp__ms-create",
    "Read",
    "Glob",
    "Grep",
    "Skill",
]

BANNED = ["Bash", "Write", "Edit", "NotebookEdit", "Task", "WebFetch", "WebSearch"]


@pytest.fixture
def g55_init_event() -> dict:
    return json.loads((FIXTURES / "g55_turn1_init_event.json").read_text())


# ---------------------------------------------------------------- the evidence


def test_the_g55_run_was_offered_every_tool_it_thought_it_had_banned(g55_init_event):
    """The measurement the whole change rests on.

    If this ever fails because the harness stopped offering these, the ban is
    being enforced somewhere else and --disallowedTools may be redundant. Until
    then it is the proof that an allow list does not remove anything.
    """
    offered = set(g55_init_event["tools"])
    assert {"Bash", "Write", "Edit", "Task", "WebFetch", "WebSearch", "NotebookEdit"} <= offered
    # ... and none of them were on the allow list.
    assert not (offered & set(BANNED)) & set(G55_ALLOWED)


# ---------------------------------------------------------------- the flags


def test_allowed_and_disallowed_are_separate_flags():
    args = ClaudeBackend(allowed_tools=["Read"], disallowed_tools=["Bash"])._args()
    assert "--allowedTools" in args
    assert args[args.index("--allowedTools") + 1] == "Read"
    assert "--disallowedTools" in args
    assert args[args.index("--disallowedTools") + 1] == "Bash"


def test_disallowed_tools_are_comma_joined_not_variadic():
    """--allowedTools is variadic: a space-separated list eats whatever follows.
    The same trap applies here, and the prompt goes on stdin for the same
    reason."""
    args = ClaudeBackend(disallowed_tools=BANNED)._args()
    assert args[args.index("--disallowedTools") + 1] == ",".join(BANNED)


def test_no_flag_when_the_ban_is_explicitly_empty():
    assert "--disallowedTools" not in ClaudeBackend(disallowed_tools=[])._args()


def test_the_prompt_is_not_on_the_command_line():
    """Regression: a prompt appended as a positional is eaten by --allowedTools."""
    args = ClaudeBackend(allowed_tools=["Read"], disallowed_tools=["Bash"])._args()
    assert not any("You are one decision point" in a for a in args)


# ------------------------------------------------- the ban is verified, not assumed


def test_a_ban_that_did_not_take_is_detected(g55_init_event):
    """A flag that is silently ignored looks exactly like a flag that works.

    This is the assertion that would have caught the original defect on turn 1
    rather than after 16 turns and a post-mortem.
    """
    backend = ClaudeBackend(allowed_tools=G55_ALLOWED, disallowed_tools=BANNED)
    leaked = backend.banned_tools_offered(g55_init_event["tools"])
    assert "Bash" in leaked
    assert leaked == {"Bash", "Write", "Edit", "NotebookEdit", "Task", "WebFetch", "WebSearch"}


def test_a_ban_that_took_reports_nothing(g55_init_event):
    offered = [t for t in g55_init_event["tools"] if t not in BANNED]
    backend = ClaudeBackend(allowed_tools=G55_ALLOWED, disallowed_tools=BANNED)
    assert backend.banned_tools_offered(offered) == set()


def test_an_absent_init_event_is_not_reported_as_a_violation():
    """No evidence is not evidence of a leak. Reporting one here would make
    every backend that emits no init event look like a safety failure."""
    backend = ClaudeBackend(disallowed_tools=BANNED)
    assert backend.banned_tools_offered(None) == set()


def test_nothing_is_checked_when_the_ban_is_explicitly_empty(g55_init_event):
    assert ClaudeBackend(disallowed_tools=[]).banned_tools_offered(g55_init_event["tools"]) == set()


# ---------------------------------------------------------------- parse


def test_parse_extracts_the_offered_tool_list(g55_init_event):
    raw = json.dumps(g55_init_event) + "\n"
    assert "Bash" in ClaudeBackend.parse(raw).tool_names_offered


def test_parse_leaves_the_offered_list_none_without_an_init_event():
    raw = json.dumps({"type": "assistant", "message": {"content": []}}) + "\n"
    assert ClaudeBackend.parse(raw).tool_names_offered is None


def test_tools_offered_is_not_the_same_as_tools_used(g55_init_event):
    """A turn that used no Bash still had Bash available. The ban is about what
    the harness loaded, not about what the model happened to call."""
    raw = json.dumps(g55_init_event) + "\n"
    res = ClaudeBackend.parse(raw)
    assert res.tool_names_offered
    assert res.tool_calls == []


# ---------------------------------------------------- the ban defaults to ON


def test_the_ban_applies_without_any_configuration():
    """A config.toml written before the ban existed has no disallowed_tools
    key. Taking the ban from config alone would leave every such run
    unprotected while the file still claimed Bash was absent — the same failure
    shape the ban exists to fix."""
    args = ClaudeBackend()._args()
    assert "--disallowedTools" in args
    assert "Bash" in args[args.index("--disallowedTools") + 1].split(",")


def test_an_explicit_empty_list_turns_the_ban_off(g55_init_event):
    """"Not specified" and "no ban" are different. Silently upgrading the
    second to the first would make the flag impossible to switch off."""
    backend = ClaudeBackend(disallowed_tools=[])
    assert "--disallowedTools" not in backend._args()
    assert backend.banned_tools_offered(g55_init_event["tools"]) == set()


def test_the_default_ban_catches_the_g55_leak(g55_init_event):
    """End to end on the real event, with no arguments at all."""
    assert "Bash" in ClaudeBackend().banned_tools_offered(g55_init_event["tools"])
