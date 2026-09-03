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
import shutil
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
    """The last top-level JSON object in the text, or None.

    "Top-level" means: not sitting inside an object already found. Once
    ``raw_decode`` reports where an object ends, the cursor jumps straight
    there — nothing inside that span is examined again, so a nested object
    (e.g. one of the "outputs" entries) is never treated as an independent
    candidate. This is what a trailing markdown code fence used to defeat:
    the fence broke an "object ends at the exact tail" check, and the old
    fallback then picked the last nested brace it found instead of the
    envelope containing it (G55 run, turn 7). A parse failure is a
    retryable turn, not a run failure — the caller records it and the next
    turn starts fresh.
    """
    decoder = json.JSONDecoder()
    last: dict | None = None
    i = 0
    while i < len(text):
        if text[i] != "{":
            i += 1
            continue
        try:
            obj, end = decoder.raw_decode(text, i)
        except json.JSONDecodeError:
            i += 1
            continue
        if isinstance(obj, dict):
            last = obj
        i = end
    return last


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


def _unwrap_mcp_result(payload: Any) -> Any:
    """Undo the MCP bridge's own double-encoding, if present.

    A real captured envelope (G55 run, turn 5) decodes once to
    ``{"result": "<json string>"}`` — a dict with exactly one key whose value
    is itself JSON text. That is the bridge's wrapper, not the tool's
    payload, so a second decode is needed before ``harvest_metrics`` or
    ``_find_key`` ever sees a numeric leaf. Any other shape (a plain dict, a
    ``{"result": <non-string>}``) is returned unchanged — this unwraps one
    specific artifact, not "result" as a generic envelope key a tool might
    legitimately use itself.
    """
    if (
        isinstance(payload, dict)
        and set(payload) == {"result"}
        and isinstance(payload["result"], str)
    ):
        try:
            return json.loads(payload["result"])
        except json.JSONDecodeError:
            return payload
    return payload


def _tool_payload(result: Any) -> Any:
    """Normalize a backend's tool_result content to a parsed object."""
    if isinstance(result, str):
        try:
            payload = json.loads(result)
        except json.JSONDecodeError:
            return None
        return _unwrap_mcp_result(payload)
    if isinstance(result, list):  # content blocks [{"type": "text", "text": ...}]
        text = "".join(b.get("text", "") for b in result if isinstance(b, dict))
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return None
        return _unwrap_mcp_result(payload)
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


def check_citations(cited: list[dict], tool_calls: list[dict]) -> list[dict]:
    """Record both sides of every cited value. Never refuse.

    'decision cited flag_fraction = 0.12; ms_flag_summary said 0.92' is a
    fact the record keeps; judging it is the next model's job, not the
    driver's.

    ``source`` is the model's own prose naming which tool call it read the
    value from (e.g. "ms_field_list on calibrators.ms, field 4") — not a
    file path, so it is not resolved as one. The check instead searches this
    turn's own ``tool_calls``, which already hold the measured values: every
    numeric fact the model could truthfully cite this turn is somewhere in
    there.
    """
    payloads = [_tool_payload(c.get("result")) for c in tool_calls]
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
        }
        key = str(c.get("name") or "").split(".")[-1]
        matches: list[Any] = []
        if key:
            for payload in payloads:
                if payload is not None:
                    matches.extend(_find_key(payload, key))
        rec["n_matches"] = len(matches)
        if matches:
            first = matches[0]
            if isinstance(first, dict) and "value" in first:
                first = first["value"]
            rec["found_value"] = first
        out.append(rec)
    return out


_BRIEF_TEMPLATE = """\
You are one decision point in a CASA reduction driven by an external loop.
You decide the single next stage; the loop executes it while you are gone.

Input: {input_path}
MS: {ms_path}
Work directory: {workdir}
Telescope: {telescope}
Free space in the work directory: {free_bytes}

Workflow status (ms_workflow_status, measured from disk):
{status_json}

Previous turn: {previous}

Do this, in order:
1. Consult the radio-interferometry skill for the stage the status names.
2. Inspect with read-only ms_inspect tools as needed.
3. Call exactly ONE writing tool with execute=False so it writes a script
   into the work directory. Do not execute anything yourself. The writing
   tools are the ms_modify server AND the ms_create server — at the
   import_asdm stage the tool you need is ms_import_asdm, from ms_create,
   and it takes the raw input path above, not an MS.
4. End your reply with one JSON object, nothing after it:
   {{"script": "<path to the generated script>",
     "tool": "<the tool you called>",
     "stage": "<the stage this advances>",
     "cited": [{{"name": "...", "value": ..., "source": "<file you read it from>"}}],
     "outputs": [{{"path": "<product the script will write>", "kind": "caltable|image|plot|ms"}}],
     "notes": "<one sentence>"}}
   Only "script" is required. At the import stage name the MS the script will
   write as an output with kind "ms": that is how the loop learns where the
   MS is, and the run cannot continue without it.
5. If the reduction is finished and no stage remains, reply instead with
   {{"done": true, "notes": "<why it is finished>"}} and name no script.
   Only you can say this: ms_workflow_status reports "selfcal_or_done" and
   cannot tell the two apart.
"""


def free_bytes(workdir: str | Path) -> int | None:
    """Free bytes on the filesystem holding workdir. None if it cannot be read.

    Reported as a measured number with no threshold and no refusal, consistent
    with "the driver may report a number, it may never name a verdict". The
    2026-08-31 G55 run halted when applycal_target aborted mid-write on a full
    filesystem (exit -6, FiledesIO::write, 1.3 MB free against a 310 GB target
    MS) and left a partial main table. Nothing in the brief had said so.

    Sizing the requirement per stage is the harder half and is not done here.
    """
    try:
        return shutil.disk_usage(str(workdir)).free
    except OSError:
        return None


def _format_bytes(n: int | None) -> str:
    """Bytes, plus GB, because a raw byte count is hard to compare to an MS size."""
    if n is None:
        return "unavailable (could not stat the work directory)"
    return f"{n} bytes ({n / 1e9:.1f} GB)"


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
        input_path=run.get("input_path") or run["ms_path"],
        ms_path=run["ms_path"] or "not imported yet",
        workdir=run["workdir"],
        telescope=run.get("telescope") or "unknown",
        free_bytes=_format_bytes(free_bytes(run["workdir"])),
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

        # Before import there is no MS, so the raw input is what gets probed.
        # ms_workflow_status reports next_recommended_step = "import_asdm" for
        # a path that is not a Measurement Set; it does not raise.
        return workflow_status.run(run["ms_path"] or run.get("input_path") or "", run["workdir"])

    def _adopt_ms(self, run_key: str, artifacts: list[dict]) -> None:
        """Learn where the MS is once an import turn has written one.

        The path is the model's claim, so it is taken only when something is
        really there: ``measure_artifact`` records size None for an absent
        path. A claim that produced nothing leaves ms_path empty, and the next
        turn senses the import stage again rather than chasing a bad path.
        """
        run = self.db._read_json(self.db._run_json(run_key))
        if run.get("ms_path"):
            return
        for a in artifacts:
            if a.get("kind") == "ms" and a.get("size") is not None:
                self.db.set_run_ms_path(run_key, a["path"])
                return

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
        if (decision or {}).get("done") is True:
            # A terminal marker, not a continuation stage. ms_workflow_status's
            # next_recommended_step answers "what to do next" — borrowing it
            # here mislabels the turn where the model says there is no next,
            # and skews the attempt count for whatever stage it names.
            stage = "done"
        else:
            stage = (
                (decision or {}).get("stage")
                or (data.get("next_recommended_step") if isinstance(data, dict) else None)
                or "unknown"
            )

        cited = (decision or {}).get("cited") or []
        extras = {
            "citations": check_citations(cited, result.tool_calls),
            "tool_calls": result.tool_calls,
            "transcript": result.transcript,
            "harvested_metrics": harvest_from_tool_calls(result.tool_calls),
            "backend_error": result.error,
            "backend_exit_code": result.exit_code,
        }
        common = dict(
            stage=stage,
            brief=brief,
            decision=decision,
            model=result.model,
            tokens_in=result.tokens_in,
            tokens_cache_read=result.tokens_cache_read,
            tokens_cache_creation=result.tokens_cache_creation,
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
            # "stopped" is a neutral terminal state, not a verdict: the driver
            # cannot independently confirm a reduction actually succeeded, only
            # that the model set done=true and the run is no longer active. The
            # 2026-08-31 G55 run used done=true to signal a halt it could not
            # recover from ("Halted, not complete" in its own notes) — the old
            # "completed" status contradicted that note. Read decision.notes
            # for what the model actually meant; the status field only says
            # the run stopped.
            self.db.set_run_status(run_key, "stopped")
            return {"action": "run_completed", "ordinal": ordinal}

        script = (decision or {}).get("script")
        script_path = None
        if script:
            script_path = Path(script)
            if not script_path.is_absolute():
                script_path = Path(run["workdir"]) / script_path
        if script_path is None or not script_path.exists():
            # The one refusal that is not a judgement: nothing to submit.
            if result.error:
                # The harness failed, which is not the same as a bad answer.
                # Say so, and say what it printed: this is the difference
                # between one legible turn and a hundred identical empty ones.
                reason = (
                    f"backend {self.backend.kind} failed (exit {result.exit_code}): {result.error}"
                )
            elif decision is None:
                reason = "decision did not parse"
            else:
                reason = f"decision names no script that exists: {script!r}"
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
        self._adopt_ms(run_key, artifacts)
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
