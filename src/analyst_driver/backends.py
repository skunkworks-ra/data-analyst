"""Backend contract + claude, opencode, codex adapters (PLAN.md step 6).

One contract: a command that takes a prompt non-interactively and returns
text on stdout. Each adapter also does its best to extract the tool calls the
turn made, the model name and the token usage — recorded when the backend can
report them, null when it cannot. A parse failure anywhere degrades to raw
stdout as ``text``; the loop treats an unusable decision as a retryable turn,
never a run failure.

Capability notes per backend:

- ``claude -p --output-format stream-json --verbose`` — full event stream:
  tool calls, tool results, model, token usage.
- ``opencode run --format json`` — raw JSON events; tool events extracted
  best-effort.
- ``codex exec --json`` — event stream (unverified here; codex is not
  installed on the dev machine). codex does not read SKILL.md, so its adapter
  prepends the skill file paths to the prompt.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass
class BackendResult:
    text: str
    transcript: str | None = None  # raw event stream, journal-only
    model: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    #: Why the backend itself failed — a non-zero exit or no output at all.
    #: Distinct from "the model answered something unusable": without it a
    #: harness that never launched is indistinguishable from a bad answer, and
    #: the loop retries the identical failure until max_turns.
    error: str | None = None
    exit_code: int | None = None
    #: Tool names the harness reported loading, from the system/init event.
    #: None when no such event appeared. Distinct from the tools actually used.
    tool_names_offered: list[str] | None = None
    #: Banned tools the harness offered anyway — the ban silently not applying.
    tools_ban_violated: set[str] = field(default_factory=set)


class Backend(Protocol):
    kind: str

    def run(self, prompt: str, workdir: str | Path) -> BackendResult: ...


def _with_failure(res: BackendResult, out: subprocess.CompletedProcess) -> BackendResult:
    """Record a backend that failed to produce anything, and why.

    Without this a harness that never launched looks exactly like a model that
    answered nothing usable: both give an empty text, the turn records
    "decision did not parse", and the loop retries the identical failure until
    max_turns. The one-line reason sits unread in stderr.
    """
    res.exit_code = out.returncode
    if out.returncode != 0 or not (out.stdout or "").strip():
        stderr = (out.stderr or "").strip()
        res.error = stderr[:2000] or f"exited {out.returncode} with no output"
    return res


def _jsonl(raw: str) -> list[Any]:
    events = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


class StubBackend:
    """Canned responses, for tests and the dry run."""

    kind = "stub"

    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.calls: list[str] = []

    def run(self, prompt: str, workdir: str | Path) -> BackendResult:
        self.calls.append(prompt)
        if not self.responses:
            raise RuntimeError("stub backend ran out of responses")
        return BackendResult(text=self.responses.pop(0), model="stub")


#: Removed from every claude turn unless a caller explicitly overrides it.
#: This is a CODE default, not a config default, on purpose: a config written
#: before the ban existed has no disallowed_tools key, and taking the ban from
#: config alone would leave every such run unprotected while the file still
#: claimed Bash was absent. Pass disallowed_tools=[] to turn it off deliberately.
#:
#: Bash is the one that matters — a turn that runs CASA itself leaves no job id,
#: no exit code and no artifact checksum in the journal, so the run becomes
#: unauditable. Write/Edit/NotebookEdit would let a turn change an MS or a
#: caltable outside the tools, where the guards live. Task, WebFetch and
#: WebSearch are removed as unnecessary surface, not because of an observed
#: failure.
DEFAULT_DISALLOWED_TOOLS = [
    "Bash",
    "Write",
    "Edit",
    "NotebookEdit",
    "Task",
    "WebFetch",
    "WebSearch",
]


class ClaudeBackend:
    kind = "claude"

    def __init__(
        self,
        cmd: str = "claude",
        mcp_config: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        allowed_tools: list[str] | None = None,
        disallowed_tools: list[str] | None = None,
    ):
        """``allowed_tools`` becomes ``--allowedTools``, ``disallowed_tools``
        ``--disallowedTools``.

        ``claude -p`` is non-interactive, so there is nobody to answer a
        permission prompt: any tool not on the allow list is DENIED, and the
        turn comes back as a refusal the driver can only record and retry.
        Without it the driver cannot call a single ms_modify or ms_create tool,
        which is every tool it exists to call.

        The two flags are NOT opposites, and conflating them is why the ban was
        never enforced. ``--allowedTools`` PRE-APPROVES; it does not remove
        anything. The 2026-08-31 G55 run made 101 Bash calls across 16 turns
        with Bash absent from the allow list — only 36 were blocked, and those
        by the working-directory rule, not by the tool list. Every transcript's
        system/init event listed Bash, Write, Edit, Task and WebFetch. Removing
        a tool takes ``--disallowedTools``.
        """
        self.cmd = cmd
        self.mcp_config = mcp_config
        self.model = model
        self.timeout = timeout
        self.allowed_tools = list(allowed_tools) if allowed_tools else []
        # None means "not specified" and takes the code default; an explicit
        # empty list means "no ban", which is a different thing and must not be
        # silently upgraded.
        self.disallowed_tools = (
            list(DEFAULT_DISALLOWED_TOOLS) if disallowed_tools is None else list(disallowed_tools)
        )

    def _args(self) -> list[str]:
        """The command line. The prompt is NOT here — it goes on stdin.

        ``--allowedTools <tools...>`` is variadic: it consumes every argument
        that follows it, so a prompt appended as the last positional is eaten
        as another tool name and claude exits 1 with "Input must be provided
        either through stdin or as a prompt argument". stdin also removes any
        argv length limit on a long brief.
        """
        args = [self.cmd, "-p", "--output-format", "stream-json", "--verbose"]
        if self.mcp_config:
            args += ["--mcp-config", self.mcp_config]
        if self.model:
            args += ["--model", self.model]
        if self.allowed_tools:
            args += ["--allowedTools", ",".join(self.allowed_tools)]
        if self.disallowed_tools:
            args += ["--disallowedTools", ",".join(self.disallowed_tools)]
        return args

    def run(self, prompt: str, workdir: str | Path) -> BackendResult:
        out = subprocess.run(
            self._args(),
            input=prompt,
            capture_output=True,
            text=True,
            cwd=str(workdir),
            timeout=self.timeout,
        )
        res = _with_failure(self.parse(out.stdout), out)
        leaked = self.banned_tools_offered(res.tool_names_offered)
        if leaked:
            res.tools_ban_violated = leaked
            res.error = (
                f"backend offered banned tools {sorted(leaked)} despite --disallowedTools."
                " The turn could have run CASA itself, so nothing it did is in the journal."
                + (f" ({res.error})" if res.error else "")
            )
        return res

    def banned_tools_offered(self, offered: list[str] | None) -> set[str]:
        """Which banned tools the harness actually offered this turn.

        A flag that is passed but ignored looks exactly like a flag that works:
        the config claims Bash is gone, the transcript says otherwise, and
        nothing reads the transcript. The system/init event lists the tools the
        harness really loaded, so it is checked against the ban rather than
        trusted. None means no init event was seen — reported as no violation,
        because an absent event is not evidence of a leak.
        """
        if not self.disallowed_tools or offered is None:
            return set()
        return {t for t in offered if t in set(self.disallowed_tools)}

    @staticmethod
    def parse(raw: str) -> BackendResult:
        events = _jsonl(raw)
        res = BackendResult(text=raw, transcript=raw or None)
        tool_names: dict[str, str] = {}
        for ev in events:
            if not isinstance(ev, dict):
                continue
            etype = ev.get("type")
            if etype == "system" and ev.get("subtype") == "init":
                res.model = ev.get("model") or res.model
                tools = ev.get("tools")
                if isinstance(tools, list):
                    res.tool_names_offered = [t for t in tools if isinstance(t, str)]
            msg = ev.get("message")
            content = msg.get("content") if isinstance(msg, dict) else None
            if not isinstance(content, list):
                content = []
            if etype == "assistant":
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        tool_names[block.get("id", "")] = block.get("name", "")
            elif etype == "user":
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        res.tool_calls.append(
                            {
                                "tool": tool_names.get(block.get("tool_use_id", ""), ""),
                                "result": block.get("content"),
                            }
                        )
            elif etype == "result":
                if isinstance(ev.get("result"), str):
                    res.text = ev["result"]
                usage = ev.get("usage") or {}
                res.tokens_in = usage.get("input_tokens")
                res.tokens_out = usage.get("output_tokens")
        return res


class OpencodeBackend:
    kind = "opencode"

    def __init__(
        self,
        cmd: str = "opencode",
        model: str | None = None,
        agent: str | None = None,
        timeout: float | None = None,
    ):
        self.cmd = cmd
        self.model = model
        self.agent = agent
        self.timeout = timeout

    def _args(self, prompt: str) -> list[str]:
        args = [self.cmd, "run", "--format", "json"]
        if self.model:
            args += ["--model", self.model]
        if self.agent:
            args += ["--agent", self.agent]
        args.append(prompt)
        return args

    def run(self, prompt: str, workdir: str | Path) -> BackendResult:
        out = subprocess.run(
            self._args(prompt),
            capture_output=True,
            text=True,
            cwd=str(workdir),
            timeout=self.timeout,
        )
        return _with_failure(self.parse(out.stdout), out)

    @staticmethod
    def parse(raw: str) -> BackendResult:
        events = _jsonl(raw)
        res = BackendResult(text=raw, transcript=raw or None)
        texts: list[str] = []
        for ev in events:
            if not isinstance(ev, dict):
                continue
            part = ev.get("part") if isinstance(ev.get("part"), dict) else ev
            ptype = part.get("type")
            if ptype == "text" and isinstance(part.get("text"), str):
                texts.append(part["text"])
            elif ptype == "tool":
                state = part.get("state") or {}
                res.tool_calls.append(
                    {
                        "tool": part.get("tool", ""),
                        "result": state.get("output"),
                    }
                )
        if texts:
            res.text = "\n".join(texts)
        return res


class CodexBackend:
    kind = "codex"

    def __init__(
        self,
        cmd: str = "codex",
        skill_paths: list[str] | None = None,
        model: str | None = None,
        timeout: float | None = None,
    ):
        self.cmd = cmd
        self.skill_paths = list(skill_paths or [])
        self.model = model
        self.timeout = timeout

    def _args(self, prompt: str) -> list[str]:
        args = [self.cmd, "exec", "--json"]
        if self.model:
            args += ["--model", self.model]
        args.append(prompt)
        return args

    def run(self, prompt: str, workdir: str | Path) -> BackendResult:
        if self.skill_paths:
            preamble = "Read these skill files before deciding:\n" + "\n".join(
                f"- {p}" for p in self.skill_paths
            )
            prompt = f"{preamble}\n\n{prompt}"
        out = subprocess.run(
            self._args(prompt),
            capture_output=True,
            text=True,
            cwd=str(workdir),
            timeout=self.timeout,
        )
        return _with_failure(self.parse(out.stdout), out)

    @staticmethod
    def parse(raw: str) -> BackendResult:
        events = _jsonl(raw)
        res = BackendResult(text=raw, transcript=raw or None)
        for ev in events:
            if not isinstance(ev, dict):
                continue
            item = ev.get("item") if isinstance(ev.get("item"), dict) else ev
            if isinstance(item.get("text"), str) and item.get("type", "agent_message") in (
                "agent_message",
                "message",
            ):
                res.text = item["text"]
        return res


def make_backend(kind: str, **kwargs: Any) -> Backend:
    if kind == "claude":
        return ClaudeBackend(**kwargs)
    if kind == "opencode":
        return OpencodeBackend(**kwargs)
    if kind == "codex":
        return CodexBackend(**kwargs)
    raise ValueError(f"unknown backend kind {kind!r}")
