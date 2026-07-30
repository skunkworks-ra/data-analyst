# Session context — SN1006 HI end-to-end reduction + tool hardening

Branch: `wildcat-skills`. Last commits:
`50d03ec` (reduction_log executable replay + tclean pblimit), `00b50c4` (new tools + fixes).
Both pushed to `origin/wildcat-skills`.

---

## 1. Why this session existed

Goal: prove the `ms_inspect` / `ms_modify` / `ms_create` MCP tools can actually
**reduce a dataset to an image**, and let the friction expose where the scaffold
is thin. Deliberately used a **novel, non-CASAguide** dataset (training data
contains the CASAguides, which would have masked real reasoning). Secondary goal:
capture the working path so a cheaper local model could replay ~50% of it.

Operating rules agreed during the session:
- **MCP-first.** Use an MCP tool when one exists. If forced to raw bash, stop and
  get permission, and log it as a gap.
- **execute=True** where possible, except heavy imaging (run as background job).
- **No throwaway scripts** — any check worth running becomes a tool.
- **Stale-server caveat:** the running MCP servers were started at session begin,
  so code changes this session were NOT live over MCP. New tools / fixed tools were
  driven via `pixi run python -c "from <pkg> import <mod>; <mod>.run(...)"`
  (authorized "B-mode" bypass). Next session, after a server restart, they are live.

## 2. The dataset

`/home/pjaganna/Data/measurement_sets/13A-154/` — two SDMs (repeat executions of
the same scheduling block `sb20914523`):
- `observation.56424.../13A-154.sb20914523.eb21465978...` — **EB1, used this session**
- `observation.56425.../13A-154.sb20914523.eb21478547...` — **EB2, not yet reduced**

Identity (from `ms_sdm_summary`, identical for both EBs):
- **EVLA, DnC config, L-band, single 2-MHz SPW, 128 ch × 15.625 kHz, full pol (RR RL LR LL).**
- Centered 1.4205 GHz = **HI 21 cm** → this is a **spectral-line** dataset.
- Target **G327.6+14.6 = SN 1006** (δ=−42°, ~30′ shell SNR). Observer: David Green.
- **3C286 (J1331+3030)** = flux + bandpass + pol-angle cal (1 scan only, 84° el).
- **J1454−4012** = phase cal (7 scans, 11–16° el).
- Everything except 3C286 sits at **~12° elevation** (far-southern source from VLA) →
  the defining challenge; DnC config is the response to it.

Field map after import (EB1): field 0 = J1454 dummy/setup scan, 1 = J1454 phase cal,
2 = SN1006 target, 3 = 3C286. (Two J1454 field entries; scan 1 is the throwaway dummy.)

## 3. Working reduction path (EB1) — all in `.../13A-154/reduction/`

Captured as a 16-step ledger in `reduction/reduction_log.jsonl`
(render → `reduction/reduction_replay.py`; NOTE the ledger used *shorthand* params,
so the replay is illustrative, not literally runnable — re-capture with verbatim
kwargs if a runnable artifact is needed).

1. `ms_sdm_summary` — identify dataset
2. `ms_import_asdm` (execute) → `sn1006_hi_eb21465978.ms` + 401 online flags
3. `ms_apply_preflag` cal_fields='1,3' (by ID to skip dummy), online flags → `calibrators.ms`, 3.4% flagged; dummy scan 1 flagged on main MS
4. `ms_generate_priorcals` → gain_curves.gc, requantizer.rq, antpos.ap (opac skipped, L-band)
5. `ms_setjy` Perley-Butler 2017 → 3C286 I = 14.98 Jy
6. **manual** `setjy(standard='manual')` constant pol model (9.67%, PA 33°, spix −0.46, reffreq 1.4205GHz) — setjy_polcal can't fit a 2-MHz window
7. `ms_gaincal` G0 (phase, '0:54~72', solint int) → initial_phase.G0
8. `ms_gaincal` K (delay, '0:6~120', inf, combine scan) → delay.K
9. `ms_bandpass` B (spw '0', inf, combine scan) → bandpass.B (6% flagged, phase RMS <1°, ea01 lost)
10. `ms_gaincal` KCROSS → crosshand.Kcross
11. `ms_gaincal` G ap (both cals, '0:6~120', inf) → gain.G
12. `ms_fluxscale` ref 3C286 transfer J1454 → gain.fluxscaled (J1454 = 0.805 Jy)
13. `ms_polcal` Df+QU on J1454 → leakage.Df (parang only 24° → **marginal**, solved per user direction)
14. `ms_polcal` Xf on 3C286 (with Df applied) → polangle.Xf
15. `ms_applycal` target, full 9-table stack, gainfield fluxscaled=J1454, interp linear, parang=True
16. `ms_tclean` IQUV, mfs, cell 4″, imsize 1280, gridder wproject (128 planes), weighting natural, hogbom, niter 5000, thr 1mJy → `sn1006_iquv_natural.*`

Refant = **ea15**. Dead/poor antennas: ea01, ea09, ea28 (100% flagged on target), ea22 (84%) — the bottom of the `ms_refant` ranking. ~22 good antennas, 66% data retained.

## 4. Results / QA

- **Calibration clean** (`ms_corrected_stats`, channel-averaged in-band 6–121):
  3C286 = 14.98 Jy / phase RMS 5.1° / 1.4% scatter (exact);
  J1454 = 0.84 Jy / 20° / 25% (expected at 12° el, not a fault).
- **Image** (`ms_image_stats` on `.image`): RMS **1.57 mJy/beam**, peak 46 mJy, **DR ≈ 29**,
  beam **113″ × 43″, PA −15°** (huge elongation = low-el DnC N–S foreshortening).
- Caveats: LAS ≈ 18′ < 30′ shell → large-scale flux resolved out; pol is **angle-only
  in practice** (Df marginal at 24° parang → Q/U angle OK, fractional pol leakage-limited ~few %).

## 5. Tools built / fixed this session (committed)

New:
- `ms_sdm_summary` (`ms_create/sdm_summary.py`) — pre-conversion ASDM inspection.
- `ms_corrected_stats` (`ms_inspect/tools/corrected_stats.py`) — per-field parallel-hand
  amp/phase QA, **vector-averaged over channels** so faint sources aren't noise-biased.
- `ms_reduction_log` (`ms_create/reduction_log.py`) — working-calls ledger (append/render/list),
  render now emits **executable** `importlib.import_module(mod).run(**params)`.

Fixed:
- `ms_apply_preflag` split read CORRECTED pre-bandpass → `datacolumn='data'`.
- `ms_polcal` execute path passed `parang` to casatasks.polcal (rejected) → removed.
- `ms_calsol_stats` compact mode dumped 124k chars → per-antenna scalar arrays (~50× smaller).
- `ms_tclean` → added `pblimit` param (default **−0.01**, keeps PB-sidelobe outliers visible).

Docs: `11-imaging.md` now always sizes images out to the **first PB sidelobe** (diameter 3×FWHM).

## 6. Scaffold gaps still OPEN (documented, not fixed)

- ~~**token footprint (broad)**~~ — **CLOSED.** `compact_fields()`
  (`ms_inspect/util/formatting.py`) drops `flag` when COMPLETE and collapses
  `{value:v}`→`v` at the serialization boundary, and all three servers apply it in
  `_run_tool`. The `run()` dict contract is unchanged. Remaining token work is
  per-tool payload shaping (`offload_detail`), not the envelope.
- **setjy_polcal epoch is MJD-conditional**, NOT a blanket 2019: obs before 2019 → 2013 values,
  ≥2019 → 2019. Catalogue currently bundles only the 2019 epoch; needs the 2013 epoch too.
  (SN1006 is MJD 56424 = 2013, so 2013 is the *correct* epoch; we used 2019 as the only one
  bundled — negligible for 3C286 which is epoch-stable.)
- **HI cube path:** `ms_tclean` has specmode='cube' but no `restfreq`/`start`/`width`/`nchan`,
  and there's no `uvcontsub`. The originally-stated spectral-line gap, untouched.
- **VLA calibrator catalogue invalid south of δ=−40°** — blind to southern sources
  (role inference / cross-match). (A leakage-cal fallback was added upstream in `1e56663`.)
- **No multiscale deconvolver / `scales`** in `ms_tclean` (only hogbom/mtmfs) — extended
  emission cleaning imperfect; used hogbom.
- **`gridder='widefield'` not exposed** (only standard/wproject/awp2) — used wproject.
- **`ms_shadowing_report` non-functional** in this CASA version (msmd.shadowedAntennas missing,
  geometric fallback unimplemented) — blind to shadowing at compact config / low el.

## 7. Converged design decisions for the TWO-EB combination (next big task)

- **Calibrate each EB SEPARATELY** (gains/bandpass/atmosphere differ per night), then
  **image them TOGETHER**. This is the agreed approach.
- CASA `tclean` accepts `vis=[ms1, ms2]` natively → **no concat tool needed**. The real gap is
  **`ms_tclean` only accepts a single ms_path string** — extend it to accept a **list of MSs**
  (validate CORRECTED in each). That is the one change that enables image-together.
- Joint imaging improves uv coverage / sensitivity but does NOT improve the per-EB leakage solve
  (still 24° parang each). A better leakage would require solving D-terms on combined data — a
  separate deliberate choice.

## 8. Resume menu (pick one to start next session)

- **(a)** Extend `ms_tclean` to accept a list of MSs → enables calibrate-separate / image-together.
- **(b)** Calibrate **EB2** (`eb21478547`) separately by replaying the validated path — the
  original "big test" that the recipe generalizes.
- **(c)** "See the image + outliers": re-image EB1 with `pblimit=-0.01` (now available) and add a
  small **image→PNG export tool** (no ad-hoc script) so the map and outliers are viewable.
- Plus the housekeeping gaps in §6 (token footprint, setjy_polcal epoch, HI cube).

## 9. Environment / gotchas

- Run code via **`pixi run python ...`** (the casatools env). `pixi run check` = ruff lint+format;
  `pixi run test-unit` = unit suite (currently **496 passing**).
- `pixi run check` flags 3 PRE-EXISTING unformatted files (`fields.py`, `pol_cal_feasibility.py`,
  `phase_cal_catalog.py`) from an earlier commit — NOT ours; left out of scope.
- Pre-existing uncommitted local changes (`.mcp.json`, `pixi.lock`, `pyproject.toml`) and stray
  `mosaic_*.svg` were intentionally NOT committed — leave them.
- Blanket MCP allow rules are in `.claude/settings.local.json` (gitignored): `mcp__ms-inspect`,
  `mcp__ms-modify`, `mcp__ms-create` — no per-call prompts (incl. write tools).
- All reduction products (MS, caltables, scripts, images, ledger) live in
  `/home/pjaganna/Data/measurement_sets/13A-154/reduction/` — OUTSIDE the repo.
