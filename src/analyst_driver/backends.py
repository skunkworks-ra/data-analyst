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


class Backend(Protocol):
    kind: str

    def run(self, prompt: str, workdir: str | Path) -> BackendResult: ...


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


class ClaudeBackend:
    kind = "claude"

    def __init__(
        self,
        cmd: str = "claude",
        mcp_config: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
    ):
        self.cmd = cmd
        self.mcp_config = mcp_config
        self.model = model
        self.timeout = timeout

    def _args(self, prompt: str) -> list[str]:
        args = [self.cmd, "-p", "--output-format", "stream-json", "--verbose"]
        if self.mcp_config:
            args += ["--mcp-config", self.mcp_config]
        if self.model:
            args += ["--model", self.model]
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
        return self.parse(out.stdout)

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
        return self.parse(out.stdout)

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
        return self.parse(out.stdout)

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
