"""
verifier.py — numeric checks on a finished step.

The verifier decides NOTHING. It reads measurements.json, applies the rules in
verifier.yaml, and returns a verdict that goes into BRIEF.md as evidence. The
model reads that evidence and chooses. Prior gating would refuse to proceed;
this reports and lets the run continue.

A rule whose key is absent reports NOT_CHECKED. It never reports a pass. A
check that cannot fail is not evidence, so the brief must always show how much
the verifier actually examined.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import yaml

PASS = "PASS"
FAIL = "FAIL"
NOT_CHECKED = "NOT_CHECKED"

_OPS = {
    "lte": lambda a, b: a <= b,
    "gte": lambda a, b: a >= b,
    "lt": lambda a, b: a < b,
    "gt": lambda a, b: a > b,
    "eq": lambda a, b: a == b,
}


@dataclasses.dataclass
class Check:
    key: str
    verdict: str
    observed: float | None
    limit: float
    op: str
    severity: str
    message: str


@dataclasses.dataclass
class Verdict:
    checks: list[Check]

    @property
    def worst(self) -> str:
        if any(c.verdict == FAIL for c in self.checks):
            return FAIL
        if any(c.verdict == PASS for c in self.checks):
            return PASS
        return NOT_CHECKED

    @property
    def n_checked(self) -> int:
        return sum(1 for c in self.checks if c.verdict != NOT_CHECKED)


def load_rules(path: Path) -> list[dict[str, Any]]:
    return yaml.safe_load(path.read_text()).get("rules", [])


def _find(measurements: dict[str, Any], key: str) -> float | None:
    """Look up a key anywhere in a nested measurements dict.

    Tool envelopes wrap values as {"value": x, "flag": ...}, and nest data one
    level down. Search rather than hard-code a path, so the verifier keeps
    working when a tool's envelope shape changes.
    """
    stack: list[Any] = [measurements]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if key in node:
                v = node[key]
                if isinstance(v, dict) and "value" in v:
                    v = v["value"]
                if isinstance(v, int | float) and not isinstance(v, bool):
                    return float(v)
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return None


def check(rules: list[dict[str, Any]], tool: str, measurements: dict[str, Any]) -> Verdict:
    """Apply every rule that names `tool` in applies_to."""
    out: list[Check] = []
    for rule in rules:
        if tool not in rule.get("applies_to", []):
            continue
        key = rule["key"]
        limit = float(rule["value"])
        op = rule.get("op", "lte")
        observed = _find(measurements, key)
        if observed is None:
            verdict = NOT_CHECKED
        elif _OPS[op](observed, limit):
            verdict = PASS
        else:
            verdict = FAIL
        out.append(
            Check(
                key=key,
                verdict=verdict,
                observed=observed,
                limit=limit,
                op=op,
                severity=rule.get("severity", "warn"),
                message=" ".join(rule.get("message", "").split()),
            )
        )
    return Verdict(checks=out)


def render(v: Verdict) -> str:
    """One block for BRIEF.md section 6."""
    if not v.checks:
        return "verifier: no rule applies to this tool — 0 checks run"
    lines = [f"verifier: {v.worst} — {v.n_checked} of {len(v.checks)} checks ran"]
    for c in v.checks:
        if c.verdict == NOT_CHECKED:
            lines.append(f"  NOT CHECKED  {c.key} — absent from measurements.json")
        else:
            shown = f"{c.observed:.4g}"
            lines.append(f"  {c.verdict:<11}  {c.key} = {shown} ({c.op} {c.limit:g})")
            if c.verdict == FAIL and c.message:
                lines.append(f"               {c.message}")
    return "\n".join(lines)
