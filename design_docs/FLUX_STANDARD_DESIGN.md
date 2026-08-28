# Flux standard handling — design note

**Status:** IMPLEMENTED 2026-08-25. All three work-order stages are done and
verified on real ALMA and VLA data (see "What shipped" at the foot of this file).
**Code branch:** `flux-standards`, off `main` (`50d23c5`).
**Why its own branch:** this changes how flux scales are chosen everywhere, not
just how they are reported. It is deliberately NOT on `alma-support`.

Surfaced by the ALMA 3C286 Band 6 test run (F-006), but none of it is
ALMA-specific — every defect below is live on VLA data today.

---

## 1. What is wrong now

### 1.1 The role and the flux standard are catalogue echoes, not derived values

`src/ms_inspect/tools/fields.py:136-152` looks the field name up in the bundled
catalogue and stamps `calibrator_role` and `flux_standard` `COMPLETE`. The
measurement set's own intents are read independently (`fields.py:73-80`) and
never compared against them. Consequences on the ALMA test data:

- `3C286` matches the catalogue → role `flux`, standard `Perley-Butler-2017`,
  both `COMPLETE`, on a 230 GHz observation whose intents say TARGET.
- `Ceres` is not in the catalogue → no role at all, despite its intents naming
  it the flux calibrator.

Intents ARE preferred, but only in "heuristic mode", which triggers when fewer
than 50% of fields carry intents (`fields.py:28,105-107`). With full intent
coverage the heuristic never fires — so good metadata is the case that fails.

**A wrong value flagged `COMPLETE` is worse than a missing one.** Both the
downstream consumers and the skill read that flag as permission to proceed.

### 1.2 `flux_standard` is decorative — setjy never reads it

`ms_setjy` selects flux fields by `role` only (`src/ms_modify/setjy.py:173`) and
applies a **single `standard` argument to the whole run**
(`setjy.py:30,67,94`; default `"Perley-Butler 2017"`). The catalogue's own
`flux_standard` value is never passed to CASA.

So a measurement set needing two different standards — the ALMA case exactly,
Ceres on a solar-system model plus a quasar on Perley-Butler — **cannot be
expressed**. Making the catalogue field conditional is only half the fix; the
standard must become per field in the execution tool too.

`setjy` is written per field with no `spw` argument, covering all windows in one
call. That part is correct and does not change.

### 1.3 Two catalogue entries name standards CASA does not accept

| entry | our value | CASA's actual string |
|---|---|---|
| PKS1934-638 | `Reynolds-1994` | `Stevens-Reynolds 2016` |
| PKS0408-65 | `Stevens-2004` | (no CASA standard — see below) |

Our strings are also hyphenated (`Perley-Butler-2017`) where CASA wants a space
(`Perley-Butler 2017`). Harmless while the value is unused; a hard failure the
moment it is threaded into `setjy`, which is what §2 does.

PKS0408-65 has no CASA flux standard at all. It must resolve to an explicit
manual-flux path, not to a standard string.

### 1.4 The catalogue holds 8 of CASA's 20 Perley-Butler 2017 sources

Present: 3C286, 3C48, 3C147, 3C138, Cassiopeia A, Cygnus A, Taurus A, Virgo A.

Missing: 3C123, 3C196, 3C295, 3C380, 3C353, 3C444, Hydra A, Hercules A,
Pictor A, Fornax A, J0133-3649, J0444-2809.

### 1.5 Validity ranges vary per source, not per standard

Perley-Butler 2017 spans 0.05–50 GHz **as a standard**, but individual sources
are valid over much narrower windows:

| source | valid range (GHz) |
|---|---|
| 3C48, 3C147, 3C286, 3C295, 3C196, 3C123 | 0.05–50 (3C123 from 0.06; 3C138 from 0.2) |
| Hydra A, Cygnus A | 0.05–12 |
| Hercules A, 3C444 | 0.2–12 |
| Taurus A, 3C380 | 0.05–4 |
| J0444-2809 | 0.2–2.0 |
| Virgo A | 0.05–3 |
| Pictor A, 3C353, J0133-3649, Cassiopeia A | 0.2–4 |
| Fornax A | 0.2–0.5 |

A single per-standard range would pass Virgo A at 10 GHz. **Per-source ranges
are a requirement, not a refinement.**

Source: CASA docs, "Flux Calibrator Models"
(https://casadocs.readthedocs.io/en/stable/notebooks/memo-series.html).

### 1.6 Solar-system objects are absent entirely

Butler-JPL-Horizons 2012 covers Venus, Mars, Jupiter, Uranus, Neptune; Io,
Europa, Ganymede, Callisto, Titan; Ceres, Pallas, Vesta, Juno, Lutetia. The
docs recommend it over the earlier Butler-JPL-Horizons 2010.

Ceres — this dataset's flux calibrator — is on that list.

---

## 2. The design

### 2.1 Decisions taken (user, 2026-08-07)

1. **Intents override, catalogue is retained and labelled.** When intents exist
   they determine the role. The catalogue answer stays visible in the record as
   a cross-check rather than being discarded.
2. **Disagreement raises a warning.** Intent-derived role vs catalogue role
   mismatch is reported, not silently resolved.
3. **The flux standard becomes conditional on observing frequency.** Reading
   the frequency is cheap and it is what resolves which scale applies.

Applies to ALL telescopes. The intent-vs-name contradiction is a real defect on
VLA data too, so this needs VLA regression coverage.

### 2.2 Resolution order for `flux_standard`

1. Field is a solar-system object → `Butler-JPL-Horizons 2012`.
2. Catalogue source, observing frequency inside that source's range → the
   catalogue standard.
3. Catalogue source, frequency outside the range → **no standard**, with a note
   naming both the range and the observing frequency. Not `COMPLETE`.
4. No catalogue match → unchanged (`UNAVAILABLE`).

### 2.3 Solar-system objects in the catalogue

Added as catalogue entries, with the position field carrying an explicit
"solar system" marker instead of coordinates. Two reasons, the second of which
is the load-bearing one:

- It states that position is not the discriminator for these objects.
- `fields.py:155` runs a VLA positional cross-match on every field. A moving
  target would either miss or land on an unrelated source. The marker lets both
  the name lookup and the cross-match skip it deliberately.

### 2.4 `ms_setjy` becomes per field

The single run-level `standard` argument becomes a per-field resolution using
§2.2. The existing argument is retained as an override for the whole run.
PKS0408-65 and any other source with no CASA standard must route to an explicit
manual flux, never to a standard string.

---

## 3. Work order

Catalogue first. The setjy change has nothing correct to read until the data is
right.

1. **Catalogue data.** Per-source frequency ranges; fix the two wrong standard
   strings and the hyphenation; add the 12 missing Perley-Butler sources; add
   the 15 solar-system objects with the position marker.
2. **`ms_field_list`.** Intent-derived role with catalogue cross-check and
   mismatch warning; conditional flux standard; read the observing frequency.
3. **`ms_setjy`.** Per-field standard resolution; manual-flux path for sources
   with no CASA standard.

**The hard part is stage 2**, because it changes existing VLA behaviour. It
needs VLA regression tests before the change, not after.

Other `cal_lookup` callers to check for fallout: `gaincal_snr_predict.py`,
`ms_modify/intents.py`.

## 4. Branch overlap to watch

`fields.py` is touched by both this branch and the ALMA role work on
`alma-support`. Land one before starting the other, or expect a conflict in the
role-resolution block.


---

## What shipped (2026-08-25)

All three stages of §3, plus two catalogue corrections §1 did not know about.

### Stage 1 — catalogue data

- Four solar-system bodies gained real validity ranges, read from CASA source
  (`FluxCalc_SS_JPL_Butler.cc`), not the docs, which state no numbers: Venus
  0.303–350, Jupiter 4.84–299.8, Uranus 4.84–428.3, Neptune 4–1000 GHz.
- **New field `constant_brightness_temperature`** on the ten bodies CASA models
  as a uniform disk at one temperature (Mars, Io, Europa, Ganymede, Callisto,
  Titan, Ceres, Pallas, Vesta, Juno). They have no range because there is
  nothing to extrapolate — a different statement from `freq_range_ghz=None`
  ("unknown to us"). **User decision: this produces a NOTE, not a warning** —
  it is a CASA modelling choice, not a metadata problem, and warning would fire
  on every ALMA dataset. The note still says the temperature was measured
  somewhere and that using it far from there is an error the gate cannot see.
- **CORRECTION to §1.6 — CASA has no model for Lutetia.** `setObjNum`
  (`FluxCalc_SS_JPL_Butler.cc:100-148`) matches 19 names and Lutetia is not one;
  `setjy` fails on it with "no flux density model … not even a rudimentary one".
  Its `flux_standard` is now `None`, so it routes to the manual-flux path. §1.6
  listed it as covered, from the docs. It is not.
- CASA also knows Mercury, Triton, Pluto, Victoria and Davida, which the
  catalogue omits. Deliberate — nobody flux-calibrates on them — and now
  recorded in the source comment so it is not re-derived.
- PKS1934-638 gained `(1.0, 50.0)`, labelled in its note as OURS not CASA's:
  `FluxStdsQS2.cc:190-210` codes a break at 11.1496 GHz and no bounds at all.

### Stage 2 — one shared resolver

`resolve_flux_standard(entry, min_ghz, max_ghz)` in `util/calibrators.py`,
returning `StandardResolution(standard, flag, note, range_checked,
needs_manual_flux)`. Six cases, table in `design_docs/DESIGN.md` §3.5. Two calls only:
`ms_field_list` reports it, `ms_setjy` acts on it. **Deliberately not in
`fields.py`** — two copies could disagree and the tool that acts would be the
wrong one.

Two design choices worth not re-deriving:

- A source CASA cannot model is flagged **COMPLETE**, not UNAVAILABLE. We know
  the answer and it is "there is no standard". UNAVAILABLE invites a caller to
  fill the gap with a fallback, which is the one thing that must not happen.
- **`range_checked` is separate from `flag` because `flag` cannot carry it.**
  COMPLETE means both "checked and inside the range" and "no range exists to
  check". Different amounts of evidence, indistinguishable from the standard
  alone. Surfaced as `flux_standard_range_checked` per field in `ms_field_list`
  and `n_range_checked` in `ms_setjy`.

### Stage 3 — `ms_field_list` and `ms_setjy`

- `_field_frequencies` moved out of `tools/fields.py` into
  `util/frequencies.py` as `field_frequencies`, so `ms_setjy` imports a util
  rather than reaching into an inspect tool.
- `ms_setjy.standard` default changed `"Perley-Butler 2017"` → `""`, meaning
  resolve per field. A non-empty value is now a whole-run OVERRIDE that skips
  the frequency gate and says so in the response (`standard_mode`).
- New `manual_flux={field: {fluxdensity, spix, reffreq}}`. Only supplied keys
  are emitted — a defaulted spix would be indistinguishable from a measured one.
  Without an entry the field is SKIPPED, never given a substitute standard.
- `skipped_no_standard` is a separate list from `skipped_fields`. "Not a flux
  calibrator" and "a flux calibrator we could not scale" are different problems
  and merging them hides the second.
- The per-field note travels into the generated `setjy.py` as a comment.
- The `execute=True` path drives off the SAME plan list as the script. It was
  the path that writes MODEL_DATA and it previously had its own standard.

### Verified on real data

| MS | result |
|---|---|
| ALMA X10a (Band 6) | Ceres → `Butler-JPL-Horizons 2012`, COMPLETE, `range_checked=False`, note not warning. 3c286 at 223–243 GHz → NO standard, warning naming 0.05–50 GHz against the span. `setjy.py` contains only Ceres. |
| VLA 3C391 (C-band) | J1331+3030 (3C286) at 4.54–7.56 GHz → `Perley-Butler 2017`, COMPLETE, `range_checked=True`, no warning. Script unchanged apart from the added note comment. |

829 unit tests pass (was 795), ruff clean.

### Skill files corrected

`07-calibration-execution.md` carried a band→standard table telling the agent
to pass `standard=` on every run. That is now an override that **skips the
gate**, so the table would have bypassed the whole change on every reduction.
Replaced with "do not pass `standard`", what to read in the response, and when
an override is legitimate (VLA P-band 3C147 → Scaife-Heald 2012).
`10-precal-workflow.md` Step 4 previously said a missing flux field means a
name mismatch; it can now also mean no standard applies at that frequency,
which re-running will not fix.

### Still open

- `astroquery` for the live ALMA calibrator catalogue — unverified that it
  exposes a flux query at all, and whether a network call belongs in an MCP
  tool. Bundled table stays canonical.
- An unknown moving target still has no catalogue entry, so
  `ms_modify/intents.py:110` can write an intent from a meaningless cone
  search. Needs CASA's FIELD-subtable ephemeris marker; belongs with the ALMA
  work, not here.
