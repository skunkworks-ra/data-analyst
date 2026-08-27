"""analyst_driver — an external loop that runs a CASA reduction as a sequence of
long jobs, calling a model only at the decision points between them.

The model decides and exits; the loop submits the job and waits. They never run
at the same time, so a model is never held open across an eight-hour tclean.

Backends (claude/opencode/codex) and executors (local/slurm/htcondor) are
pluggable. No reduction logic lives here: the driver consumes the
radio-interferometry skill and the ms_inspect/ms_modify/ms_create MCP servers,
and never reasons about radio astronomy itself.
"""

__all__ = ["db"]
