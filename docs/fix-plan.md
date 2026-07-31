# Fix plan — verified-only

Branch `fixes/verified-only`, based on `origin/telescope-profile` @ `d8733f0`
(Srikrishna Sekhar's TelescopeProfile work). Built off that branch, not `main`,
so it rebases trivially onto it.

An earlier branch (`refactor/shared-tool-dispatch`) is being scrapped. It mixed
verified fixes with two features built on unverified assumptions and one
unnecessary deletion. This plan carries over **only** what has evidence, and
records what was dropped and why.

---

## Evidence legend

Every item below carries one of these. Nothing goes in the branch without
`[RUN]` or `[READ]`.

| Tag | Meaning |
|-----|---------|
| `[RUN]` | I executed something that demonstrated it. Command recorded. |
| `[READ]` | Direct observation of code or official docs. Location recorded. |
| `[UNVERIFIED]` | Assumption. **Excluded** from this branch, or gated behind a loud failure. |

## Rules for whoever executes this plan

These exist because each was violated on the scrapped branch.

1. **No subagent claim about an external API is evidence.** If an agent says
   "casatasks returns X", execute it against the installed package before the
   code ships, or make the code fail loudly when X is absent. Two shipped bugs
   came from accepting such a claim.
2. **No `git` command that changes state, from any subagent.** No `stash`,
   `checkout`, `restore`, `rm`, `add`, `commit`, `reset`, `clean`. A subagent ran
   `git stash pop` on an unrelated stash and injected six files of someone else's
   work into the tree. Read-only git is fine.
3. **Test count and lint status are not evidence a feature works.** On the
   scrapped branch, 663 passing tests coexisted with a feature that silently
   returned nothing, because the tests fed the helper hand-built inputs in the
   shape the code wrongly assumed. State what a test actually covers.
4. **A wrong dictionary key must not degrade to `None`.** Assert the shape you
   expect and raise or flag `UNAVAILABLE`. Silent degradation is the failure mode
   the contract in item 12 exists to prevent.
5. **Do not touch `.claude/skills/radio-interferometry/wildcat/`.** See Excluded.
6. Small single-purpose commits, in the order below, no merge commits, no
   reformat-only churn. `pixi run check` and `pixi run test-unit` after each.

---

## IN SCOPE

Ordered safest first. Each is independent unless noted.

### 1. Delete two dead modules, fix two docstrings

`[RUN]` Both fail on import against this base:
```
$ pixi run python -c "import ms_inspect.tools.apply_flags"
ImportError: cannot import name 'FlagBackupFailedError' from 'ms_inspect.exceptions'
$ pixi run python -c "import ms_inspect.tools.split_field"
ImportError: cannot import name 'OutputPathExistsError' from 'ms_inspect.exceptions'
```
`[RUN]` Neither is registered: `grep -c "apply_flags\|split_field" src/ms_inspect/server.py` → `0`.
Both implement **write** operations (`flagdata` apply, `split`) inside the
read-only `ms_inspect` package.

`[READ]` Two live docstrings point the model at a tool that does not exist:
- `src/ms_inspect/tools/flag_summary.py:10-11` — "BEFORE / AFTER ms_apply_flags"
- `src/ms_inspect/tools/rfi.py:11` — "Use ms_apply_flags to act on its output"

Action: delete `src/ms_inspect/tools/apply_flags.py` and
`src/ms_inspect/tools/split_field.py`. Point the two docstrings at the real
`ms_modify` tools (`ms_apply_preflag` for flag_summary; `ms_apply_rflag` /
`ms_postcal_flag` for rfi), naming the server so the reader knows it is a
different one. Do not resurrect `split` — `ms_apply_preflag` already splits
calibrators.

### 2. Correct the `ms_shadowing_report` documentation

`[READ]` `src/ms_inspect/tools/shadowing.py` uses
`casatasks.flagdata(vis=..., mode='shadow', tolerance=..., action='calculate', savepars=False, flagbackup=False)`.
`action='calculate'` makes it read-only.
`[READ]` Five documents still describe `msmd.shadowedAntennas()`:
- `CLAUDE.md:209` (inventory table)
- `DESIGN.md:673`, `:674`, `:694`, `:837`
- `.claude/skills/radio-interferometry/01b-workflow-phase2.md:84`
- `.claude/skills/radio-interferometry/06-failure-modes.md:111`
- `docs/session_context.md:115` records the tool as non-functional

The skill file is the harmful one: it tells the model to distrust a working
measurement and check FLAG_CMD only.

Action: correct all of them to the real call. In `06-failure-modes.md`, the
`INFERRED` case is `casatasks` unavailable, not a missing msmd method. Mark the
`session_context.md` note stale and dated rather than deleting it.

**Do not claim the tool is verified working.** It has no integration coverage.
The claim being fixed is only about *which API it calls*.

### 3. Narrow two bare excepts in `workflow_status.py`

`[READ]` `src/ms_inspect/tools/workflow_status.py:35-36` and `:61-62` are
`except Exception:` / `pass`. A genuine read failure (lock, permissions, corrupt
subtable) becomes a false "stage not complete", which then drives
`next_recommended_step` to the wrong step — recommending work that is already
done, or skipping work that is not.

Action: catch only what legitimately means "has not happened yet" (a subtable
that does not exist). For anything else return the field with flag `UNAVAILABLE`
and the exception text in `note`, and make `next_recommended_step` say a probe
failed rather than inferring from a guess. Add a unit test that a read failure
yields `UNAVAILABLE`, not "incomplete".

### 4. Documentation facts that are wrong

All `[READ]` against this base:

| Location | Wrong | Correct |
|---|---|---|
| `CLAUDE.md:201`, `DESIGN.md:829` | "Layer 2 — Instrument Sanity (6 tools)" | 7. `CLAUDE.md`'s table already has 7 rows including `ms_flag_preflight` |
| `CLAUDE.md` repo tree | Missing 8 `tools/` modules, 7 `util/` modules, 3 `ms_modify/` modules, `ms_create/reduction_log.py`; ends at a `skill/SKILL.md` that does not exist | Rebuild from `ls`; skills live in `.claude/skills/` |
| `CLAUDE.md` env table | `RADIO_MCP_TEST_CALTABLE` absent | Used by integration tests in `tests/integration/test_tools.py` |
| `DESIGN.md` (search `ms_uv_coverage_stats`) | Cited as a Layer 3 tool | Does not exist. Mark not implemented |

### 5. Three registered tools no skill file names

`[RUN]` Set difference of `@mcp.tool(name=...)` across the three servers against
tool names mentioned anywhere in `.claude/skills/` and `.claude/commands/`:
`ms_sdm_summary`, `ms_reduction_log`, `ms_calsol_stats_detail`.

The model reliably reaches only for tools the skill names, so these are shipped
but unreachable in practice.

Action, skill files only:
- `ms_sdm_summary` as step 0 of `10-precal-workflow.md` (pre-conversion ASDM
  triage, no casatools needed, correct first call on a new dataset).
- `ms_reduction_log` append after each validated step, in `00-playbook.md`.
- `ms_calsol_stats_detail` as the drill-down when `ms_calsol_stats`'s bounded
  summary flags a problem, in `07-calibration-execution.md`.

**If you renumber steps in a skill file, grep for inbound `§Step N` references
first.** Renumbering `10-precal-workflow.md` broke three cross-references on the
scrapped branch.

### 6. Plugin install is broken — skill and command paths

This is the highest-value item. The plugin installs and the MCP tools work, but
all on-demand skill loading fails.

`[READ]` Official plugin docs, troubleshooting: *"plugins are copied to a cache,
so paths referencing files outside the plugin directory won't work."*
`[RUN]` On this base, repo-relative `.claude/skills/...` references:
`radio-interferometry/SKILL.md` 16, `ms-simulator/SKILL.md` 5, and one each in
`commands/{image,calibrate,polcal,precal}.md`. Total 25.

Installed from a marketplace the skill lives under `~/.claude/plugins/cache/`,
while those paths resolve against the *user's* working directory. A new user gets
working tools and working Phase 1 orientation, then silently loses every deeper
reasoning file.

`[READ]` The documented convention for a skill's own supporting files is a bare
relative filename: the skills docs show `- For complete API details, see
[reference.md](reference.md)`. That form works from both a clone and the plugin
cache, because `SKILL.md`'s own directory is the same in both.

Action:
- In the two `SKILL.md` files, strip the `.claude/skills/<name>/` prefix, leaving
  bare sibling filenames. Add one line stating the files are siblings of
  `SKILL.md` and must be resolved against it, not against the working directory.
- The four command files sit **outside** a skill directory, so relative-to-self
  has no meaning there. Have them load the `radio-interferometry` skill and read
  its supporting file, which resolves correctly because the skill knows its own
  location.

`[RUN]` Verification that worked on the scrapped branch, run from **outside** the
repo so a repo-relative path cannot accidentally succeed:
```
cd /home/pjaganna && claude --plugin-dir <repo> -p "Load the radio-interferometry skill, \
then read its 02-orientation.md supporting file and reply with ONLY the first markdown \
heading. If you cannot find it, reply FAILED <reason>" < /dev/null
```
Expected `# 02 — Orientation: Interpreting Phase 1 Output`. Repeat via the
`precal` command for the command-file path.

Also `[RUN]` in the same sweep: 17 `/project:<name>` references across
`CLAUDE.md` (5), `README.md` (6), `ms-simulator/SKILL.md` (1) and four command
files. That prefix is wrong in **both** contexts: project commands resolve as
`/<name>`, plugin commands as `/radio-analyst:<name>`. `[RUN]` A live
`--plugin-dir` load lists `radio-analyst:calibrate`, `:image`, `:inspect`,
`:polcal`, `:precal`, `:simulate`. `CLAUDE.md` also advertises
`/project:phase1` and `/project:phase2`; `[RUN] ls .claude/commands/` shows
neither exists. And `09b-polcal-reference.md` ships but is unlisted in
`SKILL.md`, so it is unreachable.

### 7. Plugin manifests — `claude plugin validate --strict` fails

`[RUN]` On this base:
```
$ claude plugin validate . --strict
⚠ plugins[0].authors: Unknown field 'authors' — did you mean 'author'?
✘ Validation failed
```
`[READ]` The manifest schema takes a single `author` **object**; `authors` is
unrecognised and silently ignored, so author attribution is currently dropped.
`[READ]` The community-marketplace review pipeline runs this same check, so this
is a submission gate, not cosmetics.

`[READ]` The marketplace entry fetches the plugin from a git URL pointing at its
own repository. For a same-repo plugin the docs specify a relative source
starting with `./`. The URL form clones twice, pins catalog and plugin
independently, and makes a local `/plugin marketplace add ./` test the pushed
default branch instead of the working tree.

Action: `authors` → `author` in both `.claude-plugin/plugin.json` and
`marketplace.json`; marketplace `source` → `"./"`; add `homepage`, `repository`,
`license`, `keywords`. Re-run `claude plugin validate . --strict` and require a
pass.

Note the schema takes one `author`, so the two-author array must collapse. Ask
before choosing how to attribute.

### 8. `serve*.sh` fails opaquely when pixi is absent

`[READ]` All three scripts run under `set -euo pipefail` and go straight into
`pixi install`. With no pixi on PATH the MCP server dies with a bare bash error
and Claude Code shows only "server failed".

Action: `command -v pixi` guard printing an actionable message naming
`https://prefix.dev` and the documented fallback `pip install ".[casa]"`
(`[READ]` `pyproject.toml [project.scripts]` provides `ms-inspect`, `ms-modify`,
`ms-create`). **stderr only** — these are stdio JSON-RPC servers and anything on
stdout corrupts the stream.

### 9. Contract rewrite in `DESIGN.md` and `CLAUDE.md`

Preshanth's explicit decision, in his words: *"Tools may return derived values,
rankings, and descriptive labels, with their inputs included. Tools may not
return gates, meaning any field whose semantic is 'you may or may not proceed.'
Gating requires knowing the science goal and the risk tolerance, and only the
skill has those."*

Motivation, also his: the SN1006 D-term call at 24 degrees of parallactic
coverage, recorded in `docs/session_context.md:65` as "marginal, solved per user
direction". A boolean could not express the right answer there.

The rule replaces "numbers, not narratives", which constrained output *type* and
so nominally banned derived quantities like a ratio of two measurements. Include:
the permitted/forbidden tables, the condition that inputs travel with outputs,
the loud-versus-silent failure argument, the preference for posterior
verification, and `ms_workflow_status.next_recommended_step` as the single named
exception (it gates on filesystem state, not a scientific claim, and fails
visibly).

Docs only. No code depends on this commit.

### 10. Remove the gate from `ms_image_stats`

`[READ]` `src/ms_inspect/tools/image_stats.py` returns `detection_pass`, a
boolean, and carries prose telling the reader what not to report. Constants are
`_P2N_FAIL = 5.0` and `_P2N_MARGINAL = 10.0`.

Action: drop `detection_pass` and the prose. Keep a `detection` label beside
`dynamic_range` and surface both constants so the label is checkable. Move the
go/no-go into `11-imaging.md`. Update `tests/unit/test_image_stats.py`.

Consider three label levels (`undetected` / `marginal` / `detection`) rather than
two, so the low constant is usable; with two levels both branches below 10 return
`marginal` and the 5 reference carries no information.

### 11. `ms_pol_cal_feasibility` → `ms_pol_cal_conditions`

`[READ]` `src/ms_inspect/tools/pol_cal_feasibility.py` returns `verdict`,
`blocker`, `xf_feasible`, `df_feasible`, `meets_threshold`,
`single_scan_sufficient`.

`[READ]` More important than the fields: the fallback block (search
`leakage_cal_alternatives`) **silently reassigns the leakage calibrator** to a
different field when the primary fails the PA threshold, recording it only in
`warnings`. A tool choosing a different calibrator on a hardcoded constant is a
calibration-strategy decision.

Action: rename module and tool. Drop the gate fields. Return `pa_spread_deg`,
`n_calibrator_scans`, the catalogue facts, `effective_role_at_band`, and the PA
thresholds as labelled constants with provenance. Replace the reassignment with
`leakage_cal_candidates`, every other field ranked by PA spread with `n_scans`
and `field_id`, enumerated unconditionally, changing nothing. Move the reasoning
into `09-polcal-execution.md` as a continuum with a stated consequence at each
coverage level.

**Keep `recommended_df_poltype`** — Preshanth's explicit decision. Decouple it
from the PA threshold: the old code computed
`(not df_known_pol) and meets_threshold` and returned `None` below the threshold,
which is wrong, because coverage determines how well constrained a `Df+QU` solve
is, not which poltype applies. Derive it from source knowledge only, and ship a
`recommended_df_poltype_basis` string so it is checkable.

Callers to update: `server.py`, `.claude/commands/polcal.md` (which branches on
the four verdicts), `SKILL.md` allowed-tools, `util/pol_calibrators.py`
docstring, `CLAUDE.md`, `DESIGN.md`, and the integration tests. New tests should
assert the **absence** of the gate fields.

### 12. Bound the `ms_spw_amp_severity` per-channel payload

`[READ]` The tool returns per-channel statistics; channel count is set by the
dataset, not the tool, so a wideband MS puts tens of thousands of records into
one MCP response. The measurement itself is memory-bounded via reservoir
sampling; the **response** is not.

Action: cap the `per_chan` drill-down with an explicit parameter. Never cap the
per-SpW aggregates (`band_floor`, `severity`, `estimated_discardable_frac`) —
those are what `13-postcal-rfi-flagging.md` consumes and they are small. Any
truncation must be visible: `PARTIAL` flag, the count dropped, and how to get the
rest. Follow the `ms_calsol_stats` → `ms_calsol_stats_detail` sidecar precedent
(`util/formatting.offload_detail`), and write the sidecar **only when truncation
actually occurs** — this is a read-only tool and should not write next to the
caller's MS for nothing.

### 13. `ms_refant` — geometry inputs only

`[READ]` `_geo_score` computes `(1 - distance/max_distance) * n_antennas` where
`max_distance` is set by the single most distant unflagged antenna. In an
extended configuration that antenna can be tens of kilometres out, so every
antenna in the core scores above ~0.94 × n and the geometry term collapses to a
near-binary "central or not". Because geometry and flagging are summed with equal
weight, the ranking near that boundary is decided by a saturated term. Nothing in
the current output makes this visible.

Action: add `distance_from_centre_m` per antenna and `max_distance_m` at top
level. Ranking, weighting and sort unchanged. Add a unit test with a synthetic
extended configuration asserting `geo_score` values collapse while
`distance_from_centre_m` still separates the antennas.

**Stop there.** See Excluded item C for the per-SpW work.

---

## EXCLUDED, and why

### A. `ms_polcal_recovery` — the whole module

Built on the scrapped branch, then found wrong. Preshanth's objection was
correct: **CORRECTED/MODEL does not test the flux scale.** The gains were solved
on that calibrator against that model, so amplitude gains absorb any model scale
error and the ratio is ≈ 1 by construction. It is specifically the one thing that
ratio cannot detect, and I labelled it "the flux-scale trap detector" in code, in
a commit message, in the skill file, and to him.

The same circularity applies to `evpa_difference_deg_vs_model`: Xf solves the
cross-hand phase to match the model's EVPA. And the catalogue comparison is
circular too whenever the model was set from that catalogue, which is the normal
case.

`[READ]` It also read `MODEL_DATA` with the `table` tool, so it only works with a
physical scratch column. `casatools.ms.getdata` accepts `'model_data'` and
`'residual_data'` and is what computes the virtual model — modern `setjy` defaults
to `usescratch=False`. My code instead warned the user to re-run `setjy` with
`usescratch=True`, working around my own wrong tool choice.

`[RUN]` And `_vector_average` writes `np.nan` where a correlation is fully
flagged; `json.dumps` emits bare `NaN`, which strict parsers reject.

The only non-circular content was D-term amplitudes against instrument
expectation, and residual Stokes V as a fit residual. That is a much smaller
tool. A real posterior flux check needs a source **not** used to set the
amplitude scale — the secondary after `fluxscale` against an independent value.
That is new work, not a reduction, and is not in this branch.

Proposal, for a later decision, not now: a small `ms_polcal_leakage_check`
returning per-antenna and per-SpW median |D| from a Df caltable plus residual
Stokes V, with the "few percent" expectation as a labelled constant, read through
the `ms` tool, NaN mapped to `null`, and no comparison against anything the solve
was told to match.

### B. Everything in `refant.py` per-SpW

`[READ]` The scrapped branch added `worst_spw_flag_frac`, `worst_spw_id`,
`median_spw_flag_frac`, `worst_spw_excess` via one `flagdata(mode='list')` call
carrying a named `mode='summary'` sub-command per SpW, then read the result as
`list_result.get(f"spw{spw_id}", {})`.

`[READ]` The official `flagdata` docstring's own example is:
```
s = flagdata(..., mode='list', inpfile=["mode='summary' name='InitFlags'", ...])
s['report0']['name'] : 'InitFlags'
s['report1']['name'] : 'Autocorr'
```
Keyed `report0`, `report1`, with `name` as a field **inside**. So the lookup
returns `{}` for every SpW, the surrounding `try/except` never fires because
nothing raises, and every antenna gets `n_spw_measured: 0` with all fields
`None`. The feature is dead and silent.

The docs are genuinely self-contradictory — the `name` parameter is described as
"to be used as a key in the returned Python dictionary" while the example shows
`report0` — so the correct form cannot be settled without a run against a real MS.

**SETTLED 2026-07-31.** `[RUN]` casatasks 6.7.5.18 against
`3c391_ctm_mosaic_10s_spw0.ms`. The return shape is **arity-dependent**, which
is why both statements in the docs are true:

```
flagdata(mode='list', inpfile=["mode='summary' name='S0' spw='0'"])
  -> flat summary dict; keys: antenna, array, correlation, field, flagged,
     name, observation, scan, spw, total, type;  result['name'] == 'S0'

flagdata(mode='list', inpfile=["mode='summary' name='S0' field='0'",
                               "mode='summary' name='S1' field='1'"])
  -> keys: ['report0', 'report1'];  result['report0']['name'] == 'S0'
```

So with one summary there is no `reportN` wrapper at all, and with two or more
there is. `result[f"spw{id}"]` is wrong in both cases. Any implementation must
handle both arities — or always emit at least two summaries — and must assert
the keys it expects rather than `.get(key, {})`.

`[RUN]` Also confirmed in the same session, for `_flag_score`, which is live
code today: `flagdata(mode='summary')` returns `result['antenna'][name] ==
{'flagged': float, 'total': float}`, with every antenna in the ANTENNA subtable
present (26/26). That assumption is no longer unverified.

The rest of item B still stands: worst-minus-median is the right statistic, and
none of it ships without being exercised on a real MS.

The underlying concern is real and Preshanth's refinement of it is right: an
antenna dead in one SpW of sixteen still ranks near the top of the aggregate flag
score, and because the refant is referenced per SpW that shows up much later as
an inter-SpW phase discontinuity. **And** the raw worst fraction is the wrong
statistic, because shadowing in a compact configuration flags an antenna
uniformly across every SpW and preferentially hits the central antennas geometry
ranks highest — so reading the raw worst would systematically reject the best
D-config candidates. The discriminating quantity is worst minus median.

None of that ships until the return contract is settled against a real MS. When
it is, the block must assert the keys it expects and flag `UNAVAILABLE` if they
are absent.

### C. The `wildcat/` deletion

`.claude/skills/radio-interferometry/wildcat/` (11 files) is unreachable from
`SKILL.md`. Deleting it on the scrapped branch caused: two modify/delete
conflicts against `telescope-profile`, six failures in
`tests/unit/test_skill_telescope_refs.py` (which hardcodes three `wildcat/`
paths), and the need for a conversation with Srikrishna, who was editing that
tree the same day — his commit `8163b2a` explicitly fixes the gridder bug in "the
`wildcat/` copy", so he was treating it as live.

Not a fix. Out of scope. **Leave the tree alone**, which also keeps his CI guard
passing untouched. Whether it should exist is a conversation between the two of
them.

### D. `${CLAUDE_PLUGIN_DATA}` relocation of the pixi environment

The problem is real and `[READ]`: `serve*.sh` resolves the manifest as
`$SCRIPT_DIR/../pixi.toml`, so under a plugin install `pixi install` builds
`.pixi/` and ~500 MB of casatools wheels into `${CLAUDE_PLUGIN_ROOT}`, which the
docs state changes on every plugin update and is collected about two weeks later,
with an explicit "treat it as ephemeral and don't write state there".
`${CLAUDE_PLUGIN_DATA}` is the documented persistent counterpart.

But the implementation rests on `[UNVERIFIED]` claims I published in my own voice
without running them: that pixi has no flag to relocate `.pixi/`, that
`PIXI_DETACHED_ENVIRONMENTS` is ignored, and that a symlinked manifest is
canonicalised back. Those came from a subagent report.

What I did verify `[RUN]`: the rebuild trigger must key on the plugin root, not
only on manifest content, because on an update `CLAUDE_PLUGIN_ROOT` changes while
`pixi.toml` stays byte-identical — a content-only check passes and leaves a
rewritten absolute path pointing at a collected directory. I confirmed both
directions on the scrapped branch (stale root forces a refresh, unchanged state
is a no-op).

Keep it out of this branch. It needs the pixi mechanism re-verified from scratch
and a genuine two-install test across a version bump, which nothing here has
done. Item 8 (the pixi-missing guard) is independent and stays in.

### E. The ALMA plan

Scoped in conversation to raw ASDM, 12-metre only, ACA later. One finding is
`[RUN]` solid and worth recording now: `src/ms_create/import_asdm.py` hardcodes
`ocorr_mode='co'`, `savecmds=True`, `applyflags=False` and has **no `asis` and no
`bdfflags` parameter**. `[READ]` `importasdm`'s docstring: `asis` "creates
verbatim copies of the ASDM tables in the output measurement set", default none;
`bdfflags` sets FLAG from the ASDM binary flags, default False. `[READ]`
`casatasks.wvrgcal` exists.

So an ALMA MS imported by our tool very likely cannot be prior-calibrated, and
ALMA online flags bypass the `.flagonline.txt` path `ms_online_flag_stats` reads.
`[UNVERIFIED]`: precisely which subtable `wvrgcal` needs, and the ALMA
polarisation sequence (`XYf+QU` then `Dflls` plus ambiguity resolution) which I
stated at about 85 percent confidence from memory.

No ALMA code in this branch.

---

## Commit order

1. Delete dead modules + fix two docstrings (item 1)
2. `workflow_status` bare excepts + test (item 3)
3. Documentation facts (items 2, 4)
4. Contract rewrite (item 9)
5. `image_stats` gate removal + test (item 10)
6. `refant` geometry inputs + test (item 13)
7. `spw_amp_severity` payload bound + tests (item 12)
8. `pol_cal_conditions` rename and gate removal + tests + skill (item 11)
9. Skill wiring for the three unreachable tools (item 5)
10. Plugin paths and `/project:` prefix (item 6)
11. Plugin manifests (item 7)
12. pixi-missing guard (item 8)

Items 1 to 9 are code and docs and can be verified with `pixi run check` plus
`pixi run test-unit`. Items 10 to 12 additionally need the `--plugin-dir` check
in item 6, run from outside the repo, and `claude plugin validate . --strict`.

## What "verified" means at the end

- `pixi run check` clean.
- `pixi run test-unit` passing, with a stated note on what the new tests cover
  and what they do not.
- `claude plugin validate . --strict` passing.
- The `--plugin-dir` skill-resolution check passing from outside the repo.
- **No integration coverage** for `ms_shadowing_report`, `ms_spw_amp_severity`,
  `ms_pol_cal_conditions`, or `ms_refant`. Anything touching a real MS is
  unverified against real data, and should be reported that way rather than as
  working.
