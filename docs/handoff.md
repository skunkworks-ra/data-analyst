# Unattended pipeline hand-off

How a fresh Claude Code session resumes a reduction run without permission
babysitting.

## Permissions

`.claude/settings.json` (project scope, checked in) pre-approves:

- All `ms-inspect`, `ms-modify`, `ms-create` MCP tools.
- Bash, narrowly: `pixi run python *` (generated pipeline scripts),
  `pgrep *` + `rm -f */table.lock` (stale CASA lock cleanup),
  `nproc` (OMP_NUM_THREADS), `ls *` / `du *` (MS directory inspection),
  plus the pre-existing pixi check/test entries.

Deliberately not pre-approved: bare `rm`/`mv`/`cp`, shell redirects,
`python -c` inline code, Edit/Write of repo files. Those still prompt —
a session reaching for them mid-run should be looked at.

## Resuming a run

1. Read `progress.md` and `observations.md` in the run's notes directory —
   these carry phase status, decisions, and known data pathologies.
2. Do not re-derive completed phases; trust the recorded caltable list and
   flag versions in `<ms>.flagversions`.
3. Before touching the MS: check for a stale `table.lock` (`pgrep` for owning
   process; if none, remove the lock).
4. Keep MS-opening tool calls sequential — see the concurrency limit in
   skill `wildcat/00-core.md`.
