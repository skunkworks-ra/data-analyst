# 09 — Polarisation Calibration Execution

## Purpose

This document guides decisions during polarisation calibration steps that follow
the standard gain/bandpass/fluxscale sequence. These steps are required when the
science target needs polarimetric imaging (Stokes Q, U, V).

Prerequisites:
- `delay.K`, `bandpass.B`, `gain.G` tables must already exist in the workdir
- The polarisation angle calibrator model must be set via `ms_setjy_polcal`
- `ms_pol_cal_conditions` should be run to identify the available calibrators
  and the conditions bearing on the D-term strategy

---

## Conditions — read before proceeding

Run `ms_pol_cal_conditions`. It reports measurements, catalogue facts, and
reference values. **It returns no verdict.** The decision below is yours, because
what is achievable depends on the science goal, and the tool does not know it.

The tool used to return `verdict`, `xf_feasible`, `df_feasible` and
`meets_threshold`, and it silently substituted a different leakage calibrator when
a threshold failed. That is gone. A boolean cannot express the answer this data
usually supports: "24 degrees against a 45 degree reference, proceed but limit
fractional-polarization claims to the few percent level." When a gate like that
fails quietly, the polarization is simply never imaged and nothing records what
was given up.

### Step A — is there a Category A angle standard?

Read `pol_angle_calibrator.available` plus `category`. If a Category A standard is
present (3C286, 3C138, 3C48) and `ms_setjy_polcal` set its model, **run Xf**.

**Parallactic-angle coverage is irrelevant to Xf.** Xf solves the cross-hand phase
against the standard's known EVPA. Never withhold Xf for lack of PA spread.

If no angle standard was observed, skip Steps 1 and 4. D-terms still correct
instrumental leakage, but absolute EVPA stays uncalibrated, so the dataset cannot
support absolute-EVPA science. Say that explicitly in the report rather than
reporting a successful polarization calibration.

If `variability_warning` is set, the standard is variable or was in a flare.
Proceed, and annotate outputs with the source and dates. If the observation
predates the flare by more than six months the degradation is likely negligible.

### Step B — which Df path do the conditions support?

Choose from `leakage_calibrator.effective_role_at_band`, which is the role of the
source **at the observed frequency**, not its static catalogue role. 3C147 and
3C84 are zero-pol leakage calibrators only where their fractional polarization is
below `frac_pol_low_reference_pct`; above roughly 10 GHz they become polarized and
move into the angle-cal regime.

| Condition in the output | poltype | PA coverage needed |
|---|---|---|
| `effective_role_at_band == "leakage_zero_pol"` | `Df` | none, a single scan suffices |
| A known-pol source, including the angle cal via its own model | `Df` | none, two or more scans |
| Unknown polarization, e.g. the phase calibrator | `Df+QU` | this is the only path that needs PA spread |

### Step C — only for Df+QU, judge the PA coverage

Compare `pa_spread_deg` against the two returned constants,
`pa_spread_reference_deg` (60, the NRAO recommendation) and
`pa_spread_practical_floor_deg` (30, where D-term and Q,U separation becomes
degenerate). Provenance is in `pa_spread_reference_source`.

This is a continuum, not a threshold. Reason about it and state the consequence:

- **At or above 60 degrees.** Solve normally. No special caveat.
- **Between 30 and 60 degrees.** Solve, and treat the D-terms as approximate.
  Verify afterwards (Step D) and limit fractional-polarization claims to roughly
  the few percent level, since residual leakage sets the floor. Record the
  coverage you actually had in the report.
- **Below 30 degrees.** The separation is degenerate, so `Df+QU` will produce
  numbers that are not trustworthy on their own. Prefer a different path: use the
  angle calibrator's known model, or pick a better field from
  `leakage_cal_candidates`. If neither exists and you proceed anyway, say plainly
  that leakage is uncorrected at the level the science needs.
- **`pa_spread_deg` is `UNAVAILABLE`.** Coverage is unknown, which is not the same
  as insufficient. Read the note, and do not report the dataset as unsuitable on
  the strength of a failed measurement.

`leakage_cal_candidates` lists every other field ranked by PA spread, each with
`n_scans`. The tool no longer switches calibrators for you. A candidate with more
coverage is not automatically better: it also needs to be bright enough to solve
on, and unresolved. Choose deliberately and record why.

### Step D — verify afterwards rather than gating beforehand

Prefer a posterior check to prior permission. After `applycal` with `parang=True`,
compare the recovered values against the catalogue on the pol calibrator: Stokes I
against its catalogue flux density, fractional polarization and EVPA against
`frac_pol_pct` and `pol_angle_deg`, and the D-term amplitudes against the expected
few percent. A failure there is loud, specific about what broke, and continuous.
That is the check worth trusting.


---

## Tool API reference

The following table maps each polcal step to the exact MCP tool and key parameters:

| Step | Tool | Key parameters | Note |
|------|------|----------------|------|
| Angle model setup | `ms_setjy_polcal` | `field`, `reffreq_ghz`, `polindex_deg=3`, `polangle_deg=4` | Run once per angle cal; populates MODEL for Df/Xf solves |
| Cross-hand delay | `ms_gaincal` | `gaintype='KCROSS'`, `smodel=[1,0,1,0]`, `combine='scan,spw'` | Must run before D-terms; wideband combine recommended |
| D-term leakage | `ms_polcal` | `poltype='Df'` or `'Df+QU'`, `solint='inf'`, `combine='scan'` | `Df` for known-pol/zero-pol sources (no PA needed); `Df+QU` only for unknown-pol sources (needs ≥30° PA) |
| Position angle | `ms_polcal` | `poltype='Xf'`, `solint='inf'`, `combine='scan'` | Run whenever a Cat A pol standard is present (`pol_angle_calibrator.category == 'A'`); independent of PA coverage; requires D-terms in gaintable |
| Apply all tables | `ms_applycal` | Pass all 7 tables: priorcals → K → B → G → Kcross → D → X | Table order is required by RIME; `parang=True` mandatory |

---

## Step 1 — Set the polarisation calibrator model

Before any polcal solve, populate the MODEL column for the angle calibrator
with `ms_setjy_polcal`. The generated script is a self-contained
**probe → fit → apply**:

1. **Probe** — runs `setjy(standard='Perley-Butler 2017', usescratch=False)` once
   per SPW (wide SPWs are equipartitioned into chunks ≥ `min_chunk_mhz`, default
   32 MHz) and harvests the returned model Stokes I per frequency.
2. **Fit** — fits `flux@reffreq` + `spix` from those `(freq, I)` points (degree
   adapts to the number of samples), and fits `polindex`/`polangle` from the
   pol-property catalogue (default epoch `2019`).
3. **Apply** — `setjy(standard='manual', fluxdensity=[I,0,0,0], spix, reffreq,
   polindex, polangle, scalebychan=True, usescratch=True)`.

```python
# Generated by ms_setjy_polcal — run this first. The script probes
# Perley-Butler Stokes I at run time; the manual setjy it ends with looks like:
setjy(
    vis=ms_path,
    field='3C286',
    standard='manual',
    fluxdensity=[flux_at_ref, 0, 0, 0],  # probed from Perley-Butler 2017
    spix=spix,                            # fit from the probe (log-polynomial)
    reffreq='1.5GHz',
    polindex=[0.099, ...],                # ascending [c0, c1, ...], fraction
    polangle=[0.575, ...],                # ascending [c0, c1, ...], radians
    scalebychan=True,
    usescratch=True,
)
```

**Do not hand-write these coefficients.** Always use the script generated by
`ms_setjy_polcal`. Stokes I comes from CASA's own Perley-Butler 2017 standard
(the pol-property catalogue carries fractional polarisation and angle only — no
Stokes I), and the pol terms come from the catalogue with the correct
ascending-order convention and RM-wrapped nodes excluded. This is why the tool
must run against the MS, not a static table: it needs CASA to evaluate
Perley-Butler over the actual SPWs.

**`usescratch=True` is mandatory here and forces a consistency requirement on
the whole MS.** `ms_setjy_polcal` always uses `usescratch=True` because virtual
models (`usescratch=False`) fail on source models with non-zero rotation measure
— a known CASA bug. But `usescratch` cannot be mixed within one MS: the first
`usescratch=True` call creates the physical `MODEL_DATA` column, and every
downstream task then reads `MODEL_DATA` for *all* fields. Any field whose model
was written virtually (`usescratch=False`) is left at the default `MODEL_DATA=1
Jy`, which silently corrupts the flux scale (fluxscale comes out
order-of-magnitude low).

**Therefore, when polcal is in scope, the flux/bandpass cals must be set with
`ms_setjy(usescratch=True)` too** — run `ms_setjy` with `usescratch=True`
**strictly before** `ms_setjy_polcal` so the entire MS uses one consistent
physical `MODEL_DATA`. See skill 07 Step 6 (fluxscale) for the sanity check.

**Ordering is not commutative — never run the two in parallel.** `MODEL_DATA` is
last-writer-wins per (field, spw). The pol-angle calibrator (3C286 / 3C138 /
3C48) is *also* a standard flux/BP calibrator, so `ms_setjy`'s automatic field
selection includes it. If a plain `ms_setjy(usescratch=True)` pass lands on that
field **after** `ms_setjy_polcal`, it overwrites the polarized model with a
Stokes-I-only one — silently. This is the G55 failure: a parallel `ms_setjy`
re-set Stokes-I models on both 3C286 entries after the polarized model, wiping it.

**When the pol-angle cal overlaps a flux/BP cal (the usual case), prefer the full
Stokes model for that field and skip the Stokes-I write on it:** pass the
overlapping field to `ms_setjy(exclude_fields=...)` so the plain pass omits it,
and let `ms_setjy_polcal` (also `usescratch=True`) be the *only* writer of its
model. The excluded field still gets a consistent physical `MODEL_DATA` from
`ms_setjy_polcal`, so the whole-MS `usescratch` consistency (above) is preserved.

**Verify before moving on.** After the setjy steps, run
`ms_verify_model(field='<all cals>', polcal_fields='<pol-angle cal>')`. It flags
any field pinned at the `MODEL=1 Jy` default (unwritten → flux-scale trap) and —
for the `polcal_fields` — a missing polarization signature (zero cross-hands),
which is exactly a Stokes-I clobber of the polarized model.

> **Before editing or debugging a setjy model, read `09b-polcal-reference.md`.**
> Stokes I (`spix`), `polindex` and `polangle` use *three different* polynomial
> forms and variables, and all three are **per-band** local expansions about
> `reffreq` — not global wideband fits. Conflating these is the most common
> setjy-manual error.

---

## Step 2 — Cross-hand delay (Kcross)

Cross-hand delay is a single delay offset between the R and L (or X and Y) feeds.
It must be solved before D-terms because it otherwise aliases into the D-term solution.

```python
gaincal(
    vis=ms_path,
    caltable='kcross.K',
    field='{ANGLE_CAL_FIELD}',
    gaintype='KCROSS',
    solint='inf',
    combine='scan',           # per-SPW (default); add ',spw' only for multiband
    refant='{REFANT}',
    smodel=[1, 0, 1, 0],      # [I, Q, U, V] — non-zero U to force cross-hand signal
    gaintable=['{PRIORCALS}', 'delay.K', 'bandpass.B', 'gain.G'],
    parang=True,
)
```

**Per-SPW vs multiband — a deliberate choice, not a default.** Two valid options:

- **Per-SPW** (`combine='scan'`): one Kcross solution per SPW. No spwmap needed at
  apply. Simplest; use unless you have a specific reason to combine.
- **Multiband** (`combine='scan,spw'`): one solution across all SPWs for higher SNR,
  treating the cross-hand delay as a single instrumental term. **VLA-specific** (it
  relies on the VLA's discrete-SPW structure; it does not transfer to MeerKAT/uGMRT).
  The combined table holds a single SPW, so **every downstream use must pass
  `spwmap=[[0,0,…0]]`** (length = n_spw) — in the `ms_polcal` Df/Xf solves that take
  it as a prior, and in the final `ms_applycal`. Omitting spwmap silently leaves
  SPWs 1..N uncorrected. The `spwmap` argument on `ms_gaincal`/`ms_polcal`/
  `ms_applycal` exists for exactly this; it defaults to identity (per-SPW behaviour).

Do not assume multiband; decide per dataset.

**Expected value:** Kcross amplitude should be < 2 ns for VLA. Larger values
indicate a real feed misalignment or a calibration error earlier in the chain.

---

## Step 3 — D-term leakage calibration

D-terms quantify leakage between the two polarisation feeds per antenna per frequency.

### Df vs Df+QU — decision table

Pick the strategy by what is **known** about the source, not by PA coverage. PA
coverage is required **only** for the unknown-pol (`Df+QU`) path. Derive the path
yourself from `effective_role_at_band` per Step B above; the tool no longer
recommends one.

| Situation | Use | PA coverage |
|---|---|---|
| Zero-pol primary leakage cal (Cat C: 3C84, 3C147, OQ208, J0713+4349, J2355+4950) | `poltype='Df'` | not needed (single scan) |
| Angle cal with known model — 3C286 / 3C138 / 3C48 (model from `fit_from_catalogue`) | `poltype='Df'` | not needed (≥2 scans) |
| Any catalogue source with known Q,U | `poltype='Df'` | not needed |
| Source of **unknown** Q,U (e.g. the phase cal) | `poltype='Df+QU'` | **required**: ≥ threshold (default 30°; NRAO suggests 60°), ≥3 scans |
| Unknown-pol source **below** the PA threshold | neither — flag; pick a known-pol source instead | — |

`poltype='Df'` solves for D-terms assuming the source Q,U are known from the
`setjy(standard='manual')` model (or that the source is unpolarised). This is the
default for the primary angle cal and for zero-pol leakage cals — **no parallactic
coverage is required**.

`poltype='Df+QU'` simultaneously solves for D-terms and the source Q,U, for a
source whose polarisation is unknown. This is the **only** path that needs PA
coverage (≥ threshold). The recovered Q and U provide a built-in sanity check:
- If the source is unpolarised: recovered Q/U ≈ noise per SPW — solution is valid
- If the source is polarised: Q/U show coherent frequency structure — the
  recovered values are the true source polarisation, inspectable per SPW

```python
polcal(
    vis=ms_path,
    caltable='dterms.D',
    field='{LEAKAGE_CAL_FIELD}',
    poltype='Df',              # or 'Df+QU' — see table above
    solint='inf',
    combine='scan',
    refant='{REFANT}',
    gaintable=['{PRIORCALS}', 'delay.K', 'bandpass.B', 'gain.G', 'kcross.K'],
    parang=True,
)
```

### D-term quality thresholds

| D-term amplitude | Assessment |
|---|---|
| < 5% | Good — typical for VLA |
| 5–10% | Marginal — check for antenna-specific outliers |
| 10–20% | Elevated — may indicate feed misalignment on specific antennas |
| > 20% | Flag the affected antenna; do not include in applycal |

A small number of antennas with D-terms slightly above 10% is acceptable if the
rest of the array is clean. An array-wide D-term above 10% is a systematic problem.

**Before applying `dterms.D`, flag its solutions** with `ms_flag_caltable`
(rflag, sigma=5.0) to remove RFI-contaminated outlier leakage solutions — see
"Caltable solution flagging" in 07-calibration-execution.md.

---

## Step 4 — Position angle calibration (Xf)

Xf calibrates the absolute orientation of the polarisation angle on the sky.
It uses a source with a well-known and stable EVPA.

**Always run this step when a Category A pol standard is present**
(`pol_angle_calibrator.available` with `category == 'A'`). Xf solves the R–L phase against that source's known EVPA model — it does
**not** require parallactic-angle coverage. Do not skip Xf for lack of PA spread.

**Preferred sources (VLA):**
- 3C286 — stable PA ~33° at all bands; preferred
- 3C138 — PA ~−14° at L-band; variable on years timescale, use with caution
- 3C48 at S-band — PA modelled from catalogue; use when 3C286/3C138 not observed

```python
polcal(
    vis=ms_path,
    caltable='polangle.X',
    field='{ANGLE_CAL_FIELD}',
    poltype='Xf',
    solint='inf',
    combine='scan',
    refant='{REFANT}',
    gaintable=['{PRIORCALS}', 'delay.K', 'bandpass.B', 'gain.G', 'kcross.K', 'dterms.D'],
    parang=True,
)
```

**Expected residual:** PA residual from Xf should be < 5°. Residual > 10° indicates:
- Wrong polangle model in the setjy step (check polangle[0] in radians)
- Remaining ionospheric Faraday rotation (especially at L-band)
- Source variability (3C138 can vary by ~10° on year timescales)

---

## Step 5 — Apply polarisation calibration tables

`parang=True` is mandatory in applycal for polarimetric data. Without it, the
parallactic angle correction is not applied and the D-term corrections are wrong.

```python
applycal(
    vis=ms_path,
    field='{ALL_FIELDS}',
    gaintable=[
        '{PRIORCALS}',
        'delay.K',
        'bandpass.B',
        'gain.G',
        'kcross.K',
        'dterms.D',
        'polangle.X',
    ],
    calwt=False,
    parang=True,            # CRITICAL — must be True for polarimetric data
    flagbackup=True,
)
```

**Table order matters:** apply in the order listed. Applying Kcross before D-terms
and D-terms before Xf is required by the RIME (Radio Interferometer Measurement Equation).

---

## Calibration table naming convention (polcal tables)

| Table | Name pattern |
|---|---|
| Cross-hand delay | `kcross.K` |
| D-terms (leakage) | `dterms.D` |
| Position angle | `polangle.X` |

These are written alongside `delay.K`, `bandpass.B`, `gain.G` in `/data/jobs/{WORKFLOW_ID}/`.

---

## Placeholder reference

| Placeholder | Source |
|---|---|
| `{ANGLE_CAL_FIELD}` | `ms_pol_cal_conditions` → angle calibrator field name |
| `{LEAKAGE_CAL_FIELD}` | `ms_pol_cal_conditions` → leakage calibrator field name (often same as angle cal) |
| `{REFANT}` | `ms_refant` — same reference antenna used throughout |
| `{PRIORCALS}` | from `ms_verify_priorcals.priorcals_list` |
| `{ALL_FIELDS}` | `ms_field_list` → all field IDs, comma-separated |

---

## Decision summary

| Check | Good | Action if not good |
|---|---|---|
| PA coverage (Df+QU only) | ≥ 30° (NRAO: 60°) | Below floor: don't attempt Df+QU; use a known-pol/zero-pol source with plain Df instead |
| Kcross amplitude | < 2 ns | Flag in summary; check if earlier calibration is correct |
| D-term amplitude (median) | < 5% | Flag in summary; if < 10%, proceed with caution |
| D-term outlier antennas | 0–1 | Flag affected antennas; exclude from applycal if > 20% |
| Df+QU recovered Q/U | ≈ noise (unpolarised) or coherent (polarised) | Unexpected pattern → check PA coverage, Kcross solution |
| Xf PA residual | < 5° | Check polangle[0] vs catalogue; check for RM at L-band |
| parang=True in applycal | Always | Never omit — silent polarisation calibration failure |
