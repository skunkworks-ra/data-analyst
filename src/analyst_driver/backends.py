"""
backends.py — run the model, once.

The driver does not read the model's stdout. Different CLIs wrap their output
differently, and parsing that wrapping is the first thing to break when you
switch tools. Instead the prompt names an output path, the model writes JSON
there, and the driver reads the file. Any CLI that can read a prompt and write
a file works, with no code change.

The prompt handed over is PROMPT.md and BRIEF.md concatenated into one file.
PROMPT.md comes first because it never changes within a run — a backend that
caches a prompt prefix then gets a hit on it at every step.
"""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path


class BackendError(RuntimeError):
    pass


def build_prompt(run_dir: Path, prompt_md: Path, brief_md: Path) -> Path:
    """Concatenate the static contract and the current brief. Stable part first."""
    out = run_dir / "turn_prompt.md"
    out.write_text(prompt_md.read_text().rstrip() + "\n\n---\n\n" + brief_md.read_text())
    return out


def run_model(cfg: dict, run_dir: Path, prompt_file: Path, decision_file: Path) -> None:
    """Invoke the configured backend once and wait for it to exit.

    This is the only call in the loop that costs real money or GPU time. The
    driver holds nothing else open while it runs, and the model process is gone
    before the next long job starts.
    """
    kind = cfg.get("kind", "claude")
    entry = cfg.get(kind)
    if entry is None:
        raise BackendError(f"config has no [backend.{kind}] section")

    subs = {
        "prompt_file": str(prompt_file),
        "rundir": str(run_dir),
        "decision": str(decision_file),
    }
    try:
        cmd = shlex.split(entry["cmd"].format(**subs))
        stdin_spec = entry.get("stdin", "").format(**subs)
    except (KeyError, IndexError, ValueError) as exc:
        # A literal brace in the command template collides with str.format.
        raise BackendError(
            f"[backend.{kind}] cmd is not a valid template ({exc}). "
            f"Known placeholders: {', '.join(sorted(subs))}. Double any literal brace."
        ) from exc
    timeout = int(entry.get("timeout_s", 1800))

    stdin_data = Path(stdin_spec).read_text() if stdin_spec else None

    before = decision_file.stat().st_mtime if decision_file.exists() else None

    try:
        proc = subprocess.run(  # noqa: S603 - command comes from the operator's config
            cmd,
            cwd=str(run_dir),
            input=stdin_data,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise BackendError(f"backend {kind} timed out after {timeout}s") from exc
    except FileNotFoundError as exc:
        raise BackendError(f"backend command not found: {cmd[0]!r}") from exc

    # Keep the transcript. It is not parsed, but it is the only record of what
    # the model said on its way to the decision, and it is worth having when a
    # decision looks strange.
    (run_dir / "last_backend_stdout.txt").write_text(proc.stdout or "")
    (run_dir / "last_backend_stderr.txt").write_text(proc.stderr or "")

    if not decision_file.exists():
        raise BackendError(
            f"backend {kind} exited {proc.returncode} without writing {decision_file.name}. "
            f"See last_backend_stderr.txt."
        )
    if before is not None and decision_file.stat().st_mtime == before:
        raise BackendError(
            f"backend {kind} left {decision_file.name} unchanged — it did not write a decision."
        )
