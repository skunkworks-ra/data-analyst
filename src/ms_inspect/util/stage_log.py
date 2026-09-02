"""
stage_log.py — the workdir stage log, written by generated scripts and read by
ms_workflow_status.

A reduction's state used to be inferred from the filesystem: ms_workflow_status
held a hardcoded list of caltable names and reported whichever ones it found.
That could not work, because every writing tool takes its caltable path as an
argument with no default, so the names are the caller's to choose. On the
2026-08-31 G55 run the tool looked for 'bandpass.B', 'gain.G' and
'gain.fluxscaled' while the run had written 'bandpass.b', 'gain.g' and
'flux.fluxscale', reported one caltable out of four, and froze its
recommendation for ten turns.

The log replaces inference with a record. Each generated script appends one
line per product it writes, AFTER CASA returns, so a line exists only if that
step actually completed. It is append-only: a retry adds a line rather than
destroying the previous one.

Placed in ms_inspect because it is the package ms_modify and ms_create both
already import from; ms_inspect never imports either of them. The snippet is
embedded verbatim into generated scripts, so it must stay dependency-free and
self-contained — same contract as pathguard.SAFE_RM_TABLE_SNIPPET.

Two limits, both deliberate:

- The check is existence only. A caltable directory appears the moment CASA
  starts writing it, so this does not prove the solve produced solutions. Row
  counts were considered and deferred until an empty-caltable failure is
  actually observed.
- A script killed outright (SIGKILL, an OOM, the -6 abort seen when the disk
  filled) writes no line at all. The log explains a failure; it does not detect
  every one. The driver's recorded exit code remains the outer truth.
"""

from __future__ import annotations

import json
from pathlib import Path

#: Filename, relative to the workdir. Shared by the writer snippet and the reader.
STAGE_LOG_NAME = "stage_log.jsonl"

#: Embedded verbatim in generated scripts. Call once after each product is
#: written. Opens, appends one line and closes — never holds a handle, because
#: a buffered write is lost when a job dies mid-stage, which is precisely the
#: case the line has to explain.
RECORD_STAGE_SNIPPET = '''\
def _record_stage(workdir, stage, product, measurement=None):
    """Append one line to stage_log.jsonl. Raise if the product is missing.

    Raising is the point: a multi-step script must not run its next step
    against a product the previous step failed to write.

    ``measurement`` carries what the stage actually changed, for the tools that
    modify an MS in place rather than writing a new table. For those the
    existence check is vacuous — the MS was there before the tool ran — so the
    measurement is the only real content of the line. Those scripts raise on
    their own after recording, because what counts as failure is the
    measurement, not the path.
    """
    import json
    import os
    from datetime import datetime, timezone

    exists = os.path.exists(product)
    entry = {
        "stage": stage,
        "product": product,
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "exists": exists,
    }
    if measurement is not None:
        entry["measurement"] = measurement
    if not exists:
        entry["error"] = "product not found after the step that writes it"
    with open(os.path.join(workdir, "stage_log.jsonl"), "a") as _fh:
        _fh.write(json.dumps(entry) + "\\n")
        _fh.flush()
        os.fsync(_fh.fileno())
    if not exists:
        raise RuntimeError(
            f"{stage}: expected product {product!r} does not exist; stopping "
            "rather than continuing with a missing input."
        )
'''


#: Pasted into the scripts that must measure what they changed. Kept here
#: rather than copied into each generator, so the three callers cannot drift.
TABLE_PROBE_SNIPPET = '''\
def _table_colnames(path):
    """Column names of a CASA table; empty list if it cannot be opened."""
    from casatools import table as _table

    tb = _table()
    try:
        tb.open(path, nomodify=True)
        return list(tb.colnames())
    except Exception:
        return []
    finally:
        try:
            tb.close()
        except Exception:
            pass


def _table_rows(path):
    """Row count of a CASA table; 0 if it cannot be opened."""
    from casatools import table as _table

    tb = _table()
    try:
        tb.open(path, nomodify=True)
        return int(tb.nrows())
    except Exception:
        return 0
    finally:
        try:
            tb.close()
        except Exception:
            pass
'''


def read_stage_log(workdir: str | Path) -> list[dict]:
    """Return the log's entries, oldest first. Absent log is an empty list.

    A malformed line is skipped rather than raising: the log is append-only and
    written by a job that may have been killed mid-line, so a truncated final
    line is an expected state, not a corruption.
    """
    path = Path(workdir) / STAGE_LOG_NAME
    if not path.is_file():
        return []
    entries: list[dict] = []
    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    return entries


def completed_stages(entries: list[dict]) -> set[str]:
    """Stages with at least one product recorded present.

    A stage that appears only with ``exists: false`` did not complete, and must
    not count: that entry is the record of its failure.
    """
    return {
        str(e.get("stage"))
        for e in entries
        if e.get("exists") is True and e.get("stage") is not None
    }


def products_for(entries: list[dict], stage: str) -> list[str]:
    """Products recorded present for one stage, oldest first, de-duplicated.

    A retry appends rather than overwrites, so the same product can appear more
    than once; the caller wants the set of paths, not the attempt count.
    """
    seen: list[str] = []
    for e in entries:
        if e.get("stage") == stage and e.get("exists") is True:
            product = e.get("product")
            if isinstance(product, str) and product not in seen:
                seen.append(product)
    return seen


# The in-process path (execute=True) bypasses the generated script entirely, so
# it needs the same write or a tool run that way records nothing. Rather than
# keep a second implementation that can drift from the snippet, execute the
# snippet and take the function it defines: one definition, two callers.
_snippet_ns: dict = {}
exec(RECORD_STAGE_SNIPPET, _snippet_ns)  # noqa: S102 - our own source, defined above

#: ``record_stage(workdir, stage, product)`` — identical to what the generated
#: scripts call, because it IS what they call.
record_stage = _snippet_ns["_record_stage"]
