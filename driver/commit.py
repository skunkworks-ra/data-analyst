"""
commit.py — everything that must change together, changed in one call.

Each wake of the model changes several things on disk: the decision file gains
its provenance block, the ledger gains a line, the replay script is re-rendered
and the run directory is committed. If those writes lived in four places, one
code path would eventually skip one, the replay script would stop matching the
ledger, and nobody would notice for a week.

So there is exactly one function. Call it, or write none of it.

The git commit is of the RUN DIRECTORY — a data directory under [run].root. It
is never the code repository.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _unwrap(v: Any) -> Any:
    """Tool envelopes wrap values as {'value': x, 'flag': ...}."""
    return v["value"] if isinstance(v, dict) and "value" in v else v


def stamp_provenance(decision_file: Path, provenance: dict[str, Any]) -> None:
    """Add the driver's half of the decision record.

    The model writes the decision. The driver writes everything you have to be
    able to trust: when, which backend, which model, which inputs. Never let
    the model author a field you will later rely on.
    """
    d = json.loads(decision_file.read_text())
    payload = {"decision": {k: v for k, v in d.items()}, "provenance": provenance}
    decision_file.write_text(json.dumps(payload, indent=2) + "\n")


def append_ledger(
    run_dir: Path,
    tool: str,
    params: dict[str, Any],
    outputs: dict[str, Any],
    rationale: str,
) -> bool:
    """Append one KNOWN-GOOD call to reduction_log.jsonl.

    Only successful steps go in. The ledger is the clean path through the data,
    not the search for it — dead ends live in decisions/ instead. Returns False
    when the ms_create package is not importable, so the caller can warn rather
    than crash a live run.
    """
    try:
        from ms_create.reduction_log import run as ledger_run
    except ImportError:
        return False
    ledger_run(
        action="append",
        workdir=str(run_dir),
        tool=tool,
        params=params,
        outputs=outputs,
        rationale=rationale,
        status="ok",
    )
    return True


def render_replay(run_dir: Path) -> bool:
    """Re-render the replay script from the ledger.

    This is what makes the run reproducible without any model: the recorded
    calls supply the parameters, and the script runs them in order.

    ms_reduction_log writes reduction_replay.py itself and returns its PATH in
    the envelope, not its text. Do not write that return value to a file — an
    earlier version did, and produced a replay.py containing one path.
    """
    try:
        from ms_create.reduction_log import run as ledger_run
    except ImportError:
        return False
    if not (run_dir / "reduction_log.jsonl").exists():
        return False
    resp = ledger_run(action="render", workdir=str(run_dir))
    written = Path(str(_unwrap(resp.get("data", {}).get("replay_script", ""))))
    return written.is_file()


def git_commit(run_dir: Path, message: str) -> bool:
    """Commit the run directory. Initialises it on first use.

    Scoped to run_dir by -C, so it can never touch the code repository.
    """
    git_dir = run_dir / ".git"
    try:
        if not git_dir.exists():
            subprocess.run(["git", "-C", str(run_dir), "init", "-q"], check=True)  # noqa: S603,S607
        subprocess.run(["git", "-C", str(run_dir), "add", "-A"], check=True)  # noqa: S603,S607
        res = subprocess.run(  # noqa: S603,S607
            ["git", "-C", str(run_dir), "commit", "-q", "-m", message],
            capture_output=True,
            text=True,
        )
        # An empty commit returns 1 and is not an error worth stopping a run for.
        return res.returncode == 0
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def commit_turn(
    *,
    run_dir: Path,
    step: int,
    decision_file: Path,
    provenance: dict[str, Any],
    completed: dict[str, Any] | None,
    use_git: bool,
) -> list[str]:
    """The one call. Returns a list of non-fatal warnings.

    `completed` describes the step that just finished OK, if any — it is what
    goes into the ledger. The decision being stamped is the NEXT step, which
    has not run yet. Those two are one step apart on purpose: a decision earns
    its ledger line only after its job succeeds.
    """
    warnings: list[str] = []

    provenance = {"utc": _now(), "step": step, **provenance}
    stamp_provenance(decision_file, provenance)

    if completed:
        ok = append_ledger(
            run_dir,
            tool=completed["tool"],
            params=completed.get("params", {}),
            outputs=completed.get("outputs", {}),
            rationale=completed.get("rationale", ""),
        )
        if not ok:
            warnings.append("ms_create.reduction_log not importable — ledger not updated")
        elif not render_replay(run_dir):
            warnings.append("replay.py not re-rendered")

    label = provenance.get("tool") or provenance.get("action") or "decision"
    if use_git and not git_commit(run_dir, f"step {step:03d}: {label}"):
        warnings.append("git commit made no change or git is unavailable")

    return warnings
