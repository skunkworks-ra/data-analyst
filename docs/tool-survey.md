# Tool-level survey (52 tools, 3 servers)

Date: 2026-07-30. Branch: `refactor/shared-tool-dispatch`.
Scope: all registered MCP tools audited on four axes: dead/broken, contract
compliance, payload cost, coverage vs the reduction workflow.

Findings are ranked by severity. Each carries a confidence level and a
concrete fix. Analysis by Opus; the fixes are written to be implementable
without re-deriving the analysis.

Registered inventory confirmed by parsing `@mcp.tool(name=...)`:
33 `ms_inspect` + 16 `ms_modify` + 3 `ms_create` = **52**.

---

## S1. Two broken write tools sitting in the read-only package

**Confidence: 100% (verified by import).**

`src/ms_inspect/tools/apply_flags.py` (224 lines) and
`src/ms_inspect/tools/split_field.py` (272 lines) both **fail on import**:

```
apply_flags BROKEN: ImportError cannot import name 'FlagBackupFailedError'
split_field BROKEN: ImportError cannot import name 'OutputPathExistsError'
```

Their exception classes were removed from `exceptions.py` (most likely in
`2204651 fix: error taxonomy`) without removing the modules. Neither is
registered in any server, neither has a test, and both implement **write**
operations (`flagdata` apply, `split`) inside the read-only package.

The live hazard is not the dead code, it is that two shipped tools point the
model at a tool that does not exist:

- `tools/flag_summary.py:10-11` "BEFORE `ms_apply_flags` ... AFTER `ms_apply_flags`"
- `tools/rfi.py:11` "Use `ms_apply_flags` to act on its output."

A model reading those docstrings will try to call `ms_apply_flags` and get
nothing back.

**Fix:** delete both modules; correct the two docstrings to name the real
tools (`ms_modify.ms_apply_rflag` / `ms_postcal_flag` for the rfi path,
`ms_apply_preflag` for the flag_summary path). If the `split` capability is
still wanted, it belongs in `ms_modify` as a new tool, not resurrected here.
Note `ms_apply_preflag` already does a calibrator split, so `split_field` may
be redundant; confirm before rebuilding it.

---

## S2. Contract drift: 9 of 33 inspect tools interpret

**Confidence: 100% on the enumeration, this is a policy decision not a bug.**

`CLAUDE.md` states the contract as absolute: "It never interprets, never
suggests a next step, never chains to another tool" and "Numbers, not
narratives." The newer tools do not honour it. Full list:

| Tool | Field | What it is |
|------|-------|-----------|
| `ms_workflow_status` | `next_recommended_step` | Literal next-step suggestion. `workflow_status.py:73-89` is a decision tree in a tool. |
| `ms_image_stats` | `detection`, `detection_pass` | Thresholded verdict (`image_stats.py:43-51`) |
| `ms_image_stats` | warning text | **Prose imperative.** `image_stats.py:294`: "do not report this as a detection." |
| `ms_pol_cal_feasibility` | `verdict`, `blocker`, `recommended_df_poltype` | Go/no-go gate (`pol_cal_feasibility.py:541-593`) |
| `ms_gaincal_snr_predict` | `recommendation_hint` | `tighten_solint` / `exclude_antennas_or_relax` (`:215-236`) |
| `ms_spw_amp_severity` | `severity` | Derived ratio, borderline. Defensible as a number. |
| `ms_refant` | `ranked` | A ranked recommendation is the tool's entire purpose |
| `ms_antenna_list` | `recommended_minblperant` | Heuristic parameter choice (`antennas.py:180-188`) |
| `ms_spectral_window_list` | `suggested.*` | Pre-built CASA selection strings (`spectral.py:219`) |
| `ms_flag_preflight` | `recommended_workers` | Compute-resource hint, not science. Benign. |

This is not accidental drift in one place. It is a consistent pattern in every
tool written after the original 13, which tells me the contract as written does
not match what the system actually needs. Two of these are genuinely useful and
hard to move (`ms_refant`, `ms_spectral_window_list.suggested`): a ranked refant
list and a ready-to-paste channel string save real tokens and real errors.

**The decision to make (yours, not mine):** amend the contract or enforce it.
My recommendation, ~75% confidence: amend it, with a bright line. Something like:

> A tool may return a **derived scalar or ranking** computed from its own
> measurements by a documented, deterministic rule, provided the inputs to that
> rule are also returned. A tool may never return prose telling the reader what
> to conclude or what to run next.

That keeps `refant`, `severity`, `recommended_minblperant`, `suggested`,
and the SNR hint (rename to a categorical, which it already is). It forces
exactly two changes:

1. **`ms_image_stats:294`** delete the prose imperative, keep `detection` +
   `detection_pass` + the threshold constants. This is the clearest violation
   in the codebase.
2. **`ms_workflow_status.next_recommended_step`** this one really is tool
   chaining, and it duplicates logic that lives in `00-playbook.md`. Either
   drop the field and let the playbook own sequencing, or accept it explicitly
   as a documented exception because it is genuinely useful for session
   resumption. I lean to keeping it and documenting the exception (~60%),
   because the alternative is the model re-deriving state every resume.

Whatever you choose, `CLAUDE.md` and `DESIGN.md` §1.1 must be updated to match,
because right now the doc is not describing the code.

---

## S3. `ms_spw_amp_severity` has an unbounded per-channel payload

**Confidence: 90%.**

`spw_amp_severity.py:337,367` emit `per_chan: chan_records` with no cap. Each
record has 5 to 6 keys (`chan`, `median`, `mad`, `min`, `max`, `peak_to_floor`,
`discardable_frac`). There is no `max_channels` or `include_per_chan`
parameter anywhere in the module.

For the datasets in the docs this is survivable (128 channels). For the
MeerKAT 32k-channel mode named in `DESIGN.md` §6.6 as a target configuration,
this is roughly 32,768 records per SpW, tens of SpWs. That is a response that
will not fit in a context window, from a tool whose whole job is triage before
flagging.

Note the tool is careful about *memory* (reservoir sampling) but not about
*output size*, which suggests the payload dimension was simply not considered.

**Fix:** add `include_per_chan: bool = False` and `max_chan_records: int = 256`.
When over the cap, return the N worst channels by `peak_to_floor` plus a count
of what was omitted, and push the full array to a sidecar via the existing
`offload_detail()` helper. The per-SpW summary is what the skill actually
consumes (see `13-postcal-rfi-flagging.md`); `per_chan` is drill-down.

Related: only 2 of 33 inspect tools (`antennas`, `geometry`) use
`offload_detail()`, despite it being purpose-built for exactly this and
documented in `formatting.py:152-169`. `flag_summary` (`per_spw` +
`per_antenna`), `residual_stats`, and `rfi` are the next candidates.

---

## S4. Three shipped tools are invisible to the skill

**Confidence: 100% (set difference of registered names vs skill mentions).**

Registered but referenced by **no** skill file:

- `ms_sdm_summary` (the pre-import triage tool, built last session)
- `ms_reduction_log` (the working-calls ledger, built last session)
- `ms_calsol_stats_detail`

The model only reliably reaches for tools the skill names. `ms_sdm_summary` is
the correct *first* call on any new dataset and nothing tells the model that.
`ms_reduction_log` was built specifically so a cheaper model could replay a
reduction, and it is never invoked.

**Fix:** add `ms_sdm_summary` as step 0 of `10-precal-workflow.md`; add
`ms_reduction_log` append calls to the playbook after each validated step; add
`ms_calsol_stats_detail` as the drill-down branch in `07-calibration-execution.md`.
Cheap, high leverage, ~30 lines of skill edits total.

---

## S5. Doc drift that will mislead

**Confidence: 100%.**

1. **`ms_shadowing_report` is documented wrong.** `CLAUDE.md` and `DESIGN.md`
   §6.5 both say it uses `msmd.shadowedAntennas()`, and
   `docs/session_context.md` §6 records it as non-functional. It was rewritten:
   `shadowing.py:59-66` uses `casatasks.flagdata(mode='shadow',
   action='calculate')`, which is a supported read-only API. I flagged this as
   broken in my first pass on the strength of the session note; that was wrong,
   the note is stale. Needs verification against a real MS (no integration
   coverage), but the code is not a stub. Update all three documents.
2. **`06-failure-modes.md:114`** still tells the model "Only FLAG_CMD shadow
   entries are reported. Check manually by ..." which is no longer true.
3. **Duplicate skill tree.** `.claude/skills/radio-interferometry/wildcat/`
   (11 files) parallels the numbered tree and is unreachable from `SKILL.md`,
   yet `docs/handoff.md:29` cites `wildcat/00-core.md` as authoritative for the
   CASA concurrency limit. Note that concurrency limit is now enforced in code
   by the shared dispatch lock, so the guidance is partly obsolete either way.

   **Decided 2026-07-30: delete.** The `handoff.md` citation was rewritten to
   point at the dispatch lock in `util/dispatch.py`, leaving zero inbound
   references to the tree from anywhere in the repo. It is unreachable from
   `SKILL.md`, so it is never loaded, and it is a second voice on subjects the
   numbered tree already covers (`wildcat/02-phase2.md` on PA conventions,
   `wildcat/00-core.md` on concurrency) which becomes actively wrong the moment
   someone wires it back up. It is committed (`06d6ae8`), so `git revert` or a
   path checkout restores it if the compact-variant idea is revived; in that
   case it needs to be reachable and maintained, not parked.

   Executed: `git rm -r .claude/skills/radio-interferometry/wildcat/` (11 files).

---

## S6. Minor code issues

- `antennas.py:186`: `len(main_ids - {a for a in main_ids if False})` is a
  no-op set comprehension, equals `len(main_ids)`. Confusing, replace.
- `antennas.py:189-190`: `orphaned_antenna_ids` and
  `antenna_table_completeness` are hardcoded. This is **correct by
  construction** (the orphan check at `:116-127` raises before reaching the
  dict), but it reads like a bug. Add a one-line comment, or derive them.
- `intents.py:214`, `workflow_status.py:35,61`, `calsol_plot.py:121` swallow
  bare `Exception` with `pass`. In `workflow_status` that silently converts a
  real read failure into a false "stage not complete", which then drives
  `next_recommended_step` to the wrong answer. Worth narrowing at minimum in
  `workflow_status`.

---

## Recommended order

1. **S1** delete the two broken modules, fix the two misleading docstrings. Pure
   subtraction, no risk, removes a live trap for the model. (Sonnet, ~20 min)
2. **S4** wire the three orphan tools into the skill. Highest value per line
   changed. (Sonnet, ~30 min)
3. **S3** bound the `spw_amp_severity` payload. (Sonnet, ~1 hour)
4. **S5** doc corrections, plus a decision from you on `wildcat/`. (Sonnet once
   you decide)
5. **S2** contract decision. **Needs you.** Everything else is mechanical; this
   one sets the direction for every tool written from here.
6. **S6** cleanups, fold into whichever branch touches those files.

Not covered here and still open from the earlier review: the multi-MS
`ms_tclean` change that unblocks two-EB imaging, the PA `validation_status:
PENDING` contradiction, and whether long CASA solves should run in the server
process at all.
