"""The turn (PLAN.md steps 4, 5): sense, decide, read the result, dispatch,
record — plus the pure functions each stage uses.

The driver stays alive and waits for its jobs (user decision, 2026-08-31).
``Loop.step`` runs one whole turn for one run, including the wait; with
``block=False`` it advances a run only when its job has finished, so
``run --all`` can interleave several runs.

The driver may report a number. It may never name a verdict. ``check_citations``
records both sides of every cited value and refuses nothing; the only stop is
a decision that names no usable script. ``outcome`` is mechanics, not science:
exit code 0 is ``accepted``, anything else is ``failed``.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from analyst_driver.backends import Backend, BackendResult
from analyst_driver.db import DriverDB, measure_artifact, utcnow_iso
from analyst_driver.executors import Executor
from analyst_driver.owner import set_owner_job

# --------------------------------------------------------------- pure pieces


def parse_decision(text: str) -> dict | None:
    """The last well-formed JSON object in the text, or None.

    A parse failure is a retryable turn, not a run failure — the caller
    records it and the next turn starts fresh.
    """
    decoder = json.JSONDecoder()
    starts = [i for i, ch in enumerate(text) if ch == "{"]
    for i in reversed(starts):
        try:
            obj, end = decoder.raw_decode(text[i:])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and i + end == len(text.rstrip()):
            return obj
    # no object ends at the tail; accept the last complete one anywhere
    best = None
    for i in starts:
        try:
            obj, _ = decoder.raw_decode(text[i:])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            best = obj
    return best


def _as_number(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, int | float):
        return None
    return float(v)


def harvest_metrics(payload: Any, prefix: str) -> list[dict]:
    """One row per numeric leaf; the name is the tool plus the key path.

    The driver holds no list of interesting measurements. An ``{"value": ...,
    "flag": ...}`` envelope becomes one row carrying the flag — including
    value None with an UNAVAILABLE flag, which must not vanish.
    """
    rows: list[dict] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            if "value" in node and ("flag" in node or _as_number(node["value"]) is not None):
                num = _as_number(node["value"])
                if num is not None or node.get("flag") is not None:
                    rows.append(
                        {
                            "name": path,
                            "value": num,
                            "unit": node.get("unit"),
                            "flag": node.get("flag"),
                        }
                    )
                return
            for k, v in node.items():
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")
        else:
            num = _as_number(node)
            if num is not None:
                rows.append({"name": path, "value": num, "unit": None, "flag": None})

    walk(payload, prefix)
    return rows


def _tool_payload(result: Any) -> Any:
    """Normalize a backend's tool_result content to a parsed object."""
    if isinstance(result, str):
        try:
            return json.loads(result)
        except json.JSONDecodeError:
            return None
    if isinstance(result, list):  # content blocks [{"type": "text", "text": ...}]
        text = "".join(b.get("text", "") for b in result if isinstance(b, dict))
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None
    return result


def harvest_from_tool_calls(tool_calls: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for call in tool_calls:
        payload = _tool_payload(call.get("result"))
        if payload is None:
            continue
        rows.extend(harvest_metrics(payload, call.get("tool") or "tool"))
    return rows


def _find_key(node: Any, key: str) -> list[Any]:
    found: list[Any] = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key:
                found.append(v)
            found.extend(_find_key(v, key))
    elif isinstance(node, list):
        for v in node:
            found.extend(_find_key(v, key))
    return found


def check_citations(cited: list[dict], workdir: str | Path) -> list[dict]:
    """Record both sides of every cited value. Never refuse.

    'decision cited flag_fraction = 0.12; the file says 0.92' is a fact the
    record keeps; judging it is the next model's job, not the driver's.
    """
    out: list[dict] = []
    for c in cited:
        if not isinstance(c, dict):
            continue
        rec = {
            "name": c.get("name"),
            "cited_value": c.get("value"),
            "source": c.get("source"),
            "found_value": None,
            "n_matches": 0,
            "error": None,
        }
        src = c.get("source")
        if src:
            path = Path(src)
            if not path.is_absolute():
                path = Path(workdir) / path
            try:
                payload = json.loads(path.read_text())
                key = str(c.get("name") or "").split(".")[-1]
                matches = _find_key(payload, key) if key else []
                rec["n_matches"] = len(matches)
                if matches:
                    first = matches[0]
                    if isinstance(first, dict) and "value" in first:
                        first = first["value"]
                    rec["found_value"] = first
            except (OSError, json.JSONDecodeError) as exc:
                rec["error"] = f"{type(exc).__name__}: {exc}"
        out.append(rec)
    return out


_BRIEF_TEMPLATE = """\
You are one decision point in a CASA reduction driven by an external loop.
You decide the single next stage; the loop executes it while you are gone.

MS: {ms_path}
Work directory: {workdir}
Telescope: {telescope}

Workflow status (ms_workflow_status, measured from disk):
{status_json}

Previous turn: {previous}

Do this, in order:
1. Consult the radio-interferometry skill for the stage the status names.
2. Inspect with read-only ms_inspect tools as needed.
3. Call exactly ONE ms_modify tool with execute=False so it writes a script
   into the work directory. Do not execute anything yourself.
4. End your reply with one JSON object, nothing after it:
   {{"script": "<path to the generated script>",
     "tool": "<the ms_modify tool you called>",
     "stage": "<the stage this advances>",
     "cited": [{{"name": "...", "value": ..., "source": "<file you read it from>"}}],
     "outputs": [{{"path": "<product the script will write>", "kind": "caltable|image|plot|ms"}}],
     "notes": "<one sentence>"}}
   Only "script" is required.
5. If the reduction is finished and no stage remains, reply instead with
   {{"done": true, "notes": "<why it is finished>"}} and name no script.
   Only you can say this: ms_workflow_status reports "selfcal_or_done" and
   cannot tell the two apart.
"""


def render_brief(run: dict, status_payload: dict, previous_turn: dict | None) -> str:
    if previous_turn is None:
        previous = "none — this is the first turn."
    else:
        jobs = previous_turn.get("jobs") or []
        last_job = jobs[-1] if jobs else {}
        previous = (
            f"stage={previous_turn.get('stage')}"
            f" outcome={previous_turn.get('outcome')}"
            f" exit_code={last_job.get('exit_code')}"
            f" logs={last_job.get('log_paths')}"
        )
    return _BRIEF_TEMPLATE.format(
        ms_path=run["ms_path"],
        workdir=run["workdir"],
        telescope=run.get("telescope") or "unknown",
        status_json=json.dumps(status_payload, indent=1, sort_keys=True, default=str),
        previous=previous,
    )


def _wall_time(submitted_at: str | None, finished_at: str | None) -> float | None:
    if not submitted_at or not finished_at:
        return None
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    try:
        return (
            datetime.strptime(finished_at, fmt) - datetime.strptime(submitted_at, fmt)
        ).total_seconds()
    except ValueError:
        return None


# ---------------------------------------------------------------- the loop


class Loop:
    def __init__(
        self,
        db: DriverDB,
        backend: Backend,
        executor: Executor,
        *,
        max_turns: int = 100,
        poll_interval: float = 60.0,
    ):
        self.db = db
        self.backend = backend
        self.executor = executor
        self.max_turns = max_turns
        self.poll_interval = poll_interval

    # sense — ground truth only, no model
    def sense(self, run: dict) -> dict:
        from ms_inspect.tools import workflow_status

        return workflow_status.run(run["ms_path"], run["workdir"])

    def _last_turn(self, run_key: str) -> dict | None:
        ordinal = self.db.next_ordinal(run_key) - 1
        if ordinal < 1:
            return None
        return self.db._read_json(self.db._turn_json(run_key, ordinal))

    def step(self, run_key: str, *, block: bool = True) -> dict:
        """Advance one run by at most one turn. Returns what happened."""
        run = self.db._read_json(self.db._run_json(run_key))
        if run["status"] != "active":
            return {"action": "skipped", "status": run["status"]}

        last = self._last_turn(run_key)
        if last is not None and last["state"] == "submitted":
            return self._settle(run_key, last, block=block)

        if self.db.next_ordinal(run_key) > self.max_turns:
            self.db.set_run_status(run_key, "needs_human")
            return {"action": "needs_human", "reason": f"max_turns={self.max_turns} reached"}

        return self._decide_and_dispatch(run_key, run, last, block=block)

    def _decide_and_dispatch(
        self, run_key: str, run: dict, last: dict | None, *, block: bool
    ) -> dict:
        status_payload = self.sense(run)
        brief = render_brief(run, status_payload, last)
        result: BackendResult = self.backend.run(brief, run["workdir"])
        decision = parse_decision(result.text or "")
        ordinal = self.db.next_ordinal(run_key)

        data = status_payload.get("data") if isinstance(status_payload, dict) else {}
        stage = (
            (decision or {}).get("stage")
            or (data.get("next_recommended_step") if isinstance(data, dict) else None)
            or "unknown"
        )

        cited = (decision or {}).get("cited") or []
        extras = {
            "citations": check_citations(cited, run["workdir"]),
            "tool_calls": result.tool_calls,
            "transcript": result.transcript,
            "harvested_metrics": harvest_from_tool_calls(result.tool_calls),
        }
        common = dict(
            stage=stage,
            brief=brief,
            decision=decision,
            model=result.model,
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
        )

        if (decision or {}).get("done") is True:
            # The model declares the reduction finished; the driver records it
            # and stops. The driver never decides this itself — the terminal
            # answer from ms_workflow_status is "selfcal_or_done", and choosing
            # between those two is science.
            self.db.record_turn(
                run_key,
                ordinal,
                jobs=[],
                extras={**extras, "stop_reason": "model declared the run complete"},
                **common,
            )
            self.db.complete_turn(
                run_key, ordinal, outcome="accepted", metrics=extras["harvested_metrics"]
            )
            self.db.set_run_status(run_key, "completed")
            return {"action": "run_completed", "ordinal": ordinal}

        script = (decision or {}).get("script")
        script_path = None
        if script:
            script_path = Path(script)
            if not script_path.is_absolute():
                script_path = Path(run["workdir"]) / script_path
        if script_path is None or not script_path.exists():
            # The one refusal that is not a judgement: nothing to submit.
            reason = (
                "decision did not parse"
                if decision is None
                else f"decision names no script that exists: {script!r}"
            )
            self.db.record_turn(
                run_key, ordinal, jobs=[], extras={**extras, "stop_reason": reason}, **common
            )
            self.db.complete_turn(
                run_key, ordinal, outcome="failed", metrics=extras["harvested_metrics"]
            )
            return {"action": "turn_failed", "ordinal": ordinal, "reason": reason}

        job_dir = self.db._run_dir(run_key) / "jobs" / f"{ordinal:04d}"
        handle = self.executor.submit(script_path, job_dir)
        # The owner file carries the job separately from the driver: a SLURM
        # job outliving its driver is normal, and only the job id lets a later
        # invocation adopt it instead of resubmitting.
        set_owner_job(self.db._run_dir(run_key), handle.get("job_id"))
        record = self.db.record_turn(run_key, ordinal, jobs=[handle], extras=extras, **common)
        return self._settle(run_key, record, block=block)

    def _settle(self, run_key: str, turn: dict, *, block: bool) -> dict:
        """Wait for the turn's job, then record what happened."""
        job = dict(turn["jobs"][-1])
        state = self.executor.poll(job)
        while state in ("pending", "running"):
            if not block:
                return {"action": "waiting", "ordinal": turn["ordinal"], "state": state}
            time.sleep(self.poll_interval)
            state = self.executor.poll(job)

        code = self.executor.exit_code(job)
        job["exit_code"] = code
        job.setdefault("finished_at", None)
        if not job["finished_at"]:
            job["finished_at"] = utcnow_iso()

        decision = turn.get("decision") or {}
        artifacts = []
        script = decision.get("script")
        if script:
            artifacts.append(measure_artifact(script, "script"))
        for out in decision.get("outputs") or []:
            if isinstance(out, dict) and out.get("path"):
                artifacts.append(measure_artifact(out["path"], out.get("kind") or "product"))

        outcome = "accepted" if state == "done" else "failed"
        self.db.complete_turn(
            run_key,
            turn["ordinal"],
            outcome=outcome,
            jobs=[job],
            artifacts=artifacts,
            metrics=turn.get("harvested_metrics") or [],
            wall_time_s=_wall_time(job.get("submitted_at"), job.get("finished_at")),
        )
        set_owner_job(self.db._run_dir(run_key), None)
        return {
            "action": "completed",
            "ordinal": turn["ordinal"],
            "outcome": outcome,
            "exit_code": code,
        }

    def run_all(self, run_keys: list[str]) -> dict:
        """Advance every run until each is blocked, done, or needs a human.

        Interleaves runs: a run waiting on a job does not stop the others.
        """
        results: dict[str, dict] = {}
        active = list(run_keys)
        while active:
            progressed = False
            waiting: list[str] = []
            for key in active:
                res = self.step(key, block=False)
                results[key] = res
                if res["action"] == "waiting":
                    waiting.append(key)
                elif res["action"] in ("completed", "turn_failed"):
                    progressed = True
                    waiting.append(key)  # not terminal; step again next sweep
            active = waiting
            if active and not progressed:
                time.sleep(self.poll_interval)
        return results
