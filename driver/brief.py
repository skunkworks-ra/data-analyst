"""
brief.py — BRIEF.md, the model's entire view of the world at one wake.

One rule controls the size: inline what the model needs to decide, and link
everything else. The brief is mostly a table of contents. A lookup costs the
model tokens inside its turn, not a step, so it can open any file it wants.

Section order is deliberate. Sections 1 to 4 are byte-identical at every wake
of a run, so a backend that caches a prompt prefix gets a hit on them. The
changing sections come last.

There is no budget section. The driver enforces the step cap and the wall clock
silently. A model that knows it is short of steps starts to trade away science
to finish, which is worse than a run that parks.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from validate import precondition_status

SEVERE_FILES = ("casa.log", "stderr", "stdout")


def _read_json(p: Path) -> dict[str, Any]:
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _fmt_params(params: dict[str, Any], width: int = 46) -> str:
    s = " ".join(f"{k}={v!r}" for k, v in params.items())
    return s if len(s) <= width else s[: width - 1] + "…"


def first_severe(step_dir: Path) -> str:
    """Point at the exact file and line of the first SEVERE message.

    Never inline the content. A CASA step writes four files and the real error
    is in a different one each time, so the model would otherwise open all four
    from the top. One file at one line number removes that waste.
    """
    for name in SEVERE_FILES:
        f = step_dir / name
        if not f.exists():
            continue
        try:
            for i, line in enumerate(f.read_text(errors="replace").splitlines(), start=1):
                if "SEVERE" in line:
                    return f"{f.name}:{i}"
        except OSError:
            continue
    return "no SEVERE line found — read stderr and stdout"


INLINE_LIST_MAX = 8


def _plain(v: Any) -> Any:
    """Strip the {'value': x, 'flag': ...} envelope wrapper."""
    return v["value"] if isinstance(v, dict) and "value" in v else v


def _one_line(entry: Any) -> str:
    if not isinstance(entry, dict):
        return str(entry)
    bits = []
    for k, v in entry.items():
        v = _plain(v)
        if isinstance(v, dict | list):
            continue
        bits.append(f"{k}={v:.4g}" if isinstance(v, float) else f"{k}={v}")
    return " ".join(bits)


def _render_measurements(meas: dict[str, Any]) -> str:
    """Show the numbers, not the JSON.

    A raw dump of a probe blows the brief apart — per_antenna alone is 27
    entries of nested envelope for a full VLA run. Scalars go inline because
    they drive the decision. A long array becomes a count plus a pointer,
    because the model can open the file if it needs the detail.
    """
    lines: list[str] = []
    for key, raw in meas.items():
        val = _plain(raw)
        if isinstance(val, list):
            if len(val) <= INLINE_LIST_MAX:
                lines.append(f"    {key}:")
                lines.extend(f"      {_one_line(e)}" for e in val)
            else:
                lines.append(f"    {key}: {len(val)} entries — see measurements.json")
        elif isinstance(val, dict):
            inner = _one_line(val)
            lines.append(f"    {key}: {inner}" if inner else f"    {key}: see measurements.json")
        elif isinstance(val, float):
            lines.append(f"    {key} = {val:.6g}")
        else:
            lines.append(f"    {key} = {val}")
    return "\n".join(lines) or "    (empty)"


# -- sections -----------------------------------------------------------


def _section_data(ms_rows: list[dict[str, Any]], active_ms: str, instrument: str) -> str:
    out = [f"## 2. The data                      [refreshed every wake]\n\n{instrument}\n"]
    out.append(f"  {'MS':<24} {'fields':<34} {'flagged':>8}  active")
    for r in ms_rows:
        frac = r.get("flag_fraction")
        shown = f"{frac * 100:.1f}%" if isinstance(frac, int | float) else "unknown"
        active = "YES" if r["path"] == active_ms else "no"
        out.append(f"  {r['name']:<24} {r.get('fields', '?'):<34} {shown:>8}  {active}")
    out.append("\nfull summary: cache/ms_summary.json   (you may re-probe with ms-inspect)")
    return "\n".join(out)


def _section_tools(
    whitelist: dict[str, Any], run_dir: Path, active_ms: Path, tools_done: list[str]
) -> str:
    out = ["## 3. Tools you may call            [stable]\n"]
    for name, entry in whitelist["tools"].items():
        unmet = [
            label
            for req in entry.get("requires", [])
            for met, label in [precondition_status(req, run_dir, active_ms, tools_done)]
            if not met
        ]
        status = "MET" if not unmet else f"NOT MET — needs {unmet[0]}"
        out.append(f"  {name:<26} {status}")
        out.append(f"  {'':<26} {entry.get('note', '')}")
    out.append("\nfull schemas: driver/whitelist.yaml")
    out.append("Do not pass ms_path, workdir or execute. The driver sets them.")
    return "\n".join(out)


def _section_order(recipe: dict[str, Any]) -> str:
    order = recipe.get("order", [])
    wrapped, line = [], "  "
    for tool in order:
        piece = tool + " → "
        if len(line) + len(piece) > 78:
            wrapped.append(line.rstrip())
            line = "  "
        line += piece
    wrapped.append(line.rstrip(" →").rstrip())
    return (
        "## 4. The usual order               [a map, not a rule]\n\n"
        + f"{recipe.get('description', '')}\n\n"
        + "\n".join(wrapped)
        + "\n\nLeave this order when the data says to, and say why in your rationale."
    )


def _section_history(steps: list[dict[str, Any]], full_tail: int) -> str:
    """One line per step. Older runs of consecutive OK steps fold into one line.

    Folding is a plain rule, not a second model call. It keeps this section
    flat as the run grows, which is the only part of the brief that would
    otherwise grow without bound.
    """
    out = ["## 5. What has happened             [one line per step]\n"]
    head, tail = (steps[:-full_tail], steps[-full_tail:]) if len(steps) > full_tail else ([], steps)

    if head:
        i = 0
        while i < len(head):
            if head[i].get("result") == "OK":
                j = i
                while j < len(head) and head[j].get("result") == "OK":
                    j += 1
                if j - i > 1:
                    first, last = head[i]["step"], head[j - 1]["step"]
                    tools = ", ".join(dict.fromkeys(s["tool"] for s in head[i:j]))
                    out.append(f"  {first}-{last} all OK — {tools}")
                    i = j
                    continue
            s = head[i]
            out.append(
                f"  {s['step']:<3} {s['tool']:<26} {_fmt_params(s.get('params', {}))} "
                f" {s.get('result', '?'):<7} {s.get('headline', '')}"
            )
            i += 1

    for s in tail:
        out.append(
            f"  {s['step']:<3} {s['tool']:<26} {_fmt_params(s.get('params', {}))} "
            f" {s.get('result', '?'):<7} {s.get('headline', '')}"
        )
    if not steps:
        out.append("  (nothing yet — this is the first step)")
    out.append("\nscripts, logs and measurements: steps/NNN-<tool>/")
    return "\n".join(out)


def _section_last(
    last: dict[str, Any] | None,
    step_dir: Path | None,
    verdict_text: str,
    prev_rationale: str,
) -> str:
    out = ["## 6. The last step                 [the only step shown in detail]\n"]
    if last is None:
        out.append("  (none — this is the first step of the run)")
        return "\n".join(out)

    out.append(
        f"  step {last['step']} · {last['tool']} · {_fmt_params(last.get('params', {}), 60)}"
    )
    out.append(f"  result {last.get('result', '?')} · {last.get('duration', '?')}")

    meas = _read_json(step_dir / "measurements.json") if step_dir else {}
    out.append("\n  measurements:")
    out.append(
        _render_measurements(meas) if meas else "    (none — no probe is configured for this tool)"
    )

    out.append("\n  " + verdict_text.replace("\n", "\n  "))

    if prev_rationale:
        out.append("\n  your rationale at that step, in full:")
        out.append('    "' + prev_rationale.strip() + '"')

    if step_dir:
        files = sorted(p.name for p in step_dir.iterdir() if p.is_file())
        out.append(f"\n  files in {step_dir.name}/: {' · '.join(files)}")
        if last.get("result") == "FAILED":
            out.append(f"  FAILED · {step_dir.name}/{first_severe(step_dir)} · first SEVERE line")
    return "\n".join(out)


def _section_rejected(refusals: list[str]) -> str:
    out = ["## 7. Rejected this step            [usually empty]\n"]
    if not refusals:
        out.append("  (none)")
    else:
        for i, r in enumerate(refusals, start=1):
            out.append(
                f"  attempt {i} was refused:\n" + "\n".join(f"    {ln}" for ln in r.splitlines())
            )
        out.append("\n  Fix that exact problem. Do not resubmit the same decision.")
    return "\n".join(out)


# -- entry point --------------------------------------------------------


def render(
    *,
    run_dir: Path,
    run_id: str,
    step: int,
    goal: str,
    instrument: str,
    ms_rows: list[dict[str, Any]],
    active_ms: Path,
    whitelist: dict[str, Any],
    recipe: dict[str, Any],
    steps: list[dict[str, Any]],
    tools_done: list[str],
    last: dict[str, Any] | None,
    last_step_dir: Path | None,
    verdict_text: str,
    prev_rationale: str,
    refusals: list[str],
    decision_path: Path,
    full_tail: int = 10,
) -> Path:
    parts = [
        f"# BRIEF — run {run_id} — step {step}",
        "",
        f"Write your decision to: `{decision_path.relative_to(run_dir)}`",
        f"All paths below are relative to: `{run_dir}`",
        "",
        f"## 1. Goal                          [stable]\n\n{goal}",
        "",
        _section_data(ms_rows, str(active_ms), instrument),
        "",
        _section_tools(whitelist, run_dir, active_ms, tools_done),
        "",
        _section_order(recipe),
        "",
        _section_history(steps, full_tail),
        "",
        _section_last(last, last_step_dir, verdict_text, prev_rationale),
        "",
        _section_rejected(refusals),
        "",
    ]
    out = run_dir / "BRIEF.md"
    out.write_text("\n".join(parts))
    return out
