# radio_ms_mcp — Design Document
## Phase 1: Measurement Set Inspection (Layers 1 & 2)

**Status:** Design — not yet implemented  
**Last revised:** 2026-03-11 (rev 2 — metadata failure modes, PA convention, parallel FLAG reads, resolved calibrators)  
**Scope:** Layer 1 (Orientation) and Layer 2 (Instrument Sanity) only.  
Layers 3–5 (Data Quality, Calibration Readiness, Imaging Preparation) are out of scope for Phase 1.

---

## 1. Philosophy

### 1.1 The Core Division

> **An MCP tool answers exactly one question with numbers.**  
> **The Skill answers: given these numbers, what do they mean, and what do I look at next?**

No tool gates. No tool suggests a next step. No tool chains to another.  
Interpretation, diagnostic reasoning, and workflow sequencing live exclusively in the Skill document.

**The gate rule.** Tools may return derived values, rankings, and descriptive
labels, with their inputs included. Tools may **not** return gates, meaning any
field whose semantic is "you may or may not proceed". Gating requires knowing
the science goal and the risk tolerance, and only the skill has those.

| Permitted | Forbidden |
|-----------|-----------|
| Derived scalars: `dynamic_range`, `severity`, `pa_spread_deg` | Boolean verdicts: `detection_pass`, `xf_feasible`, `meets_threshold` |
| Descriptive labels with their constants surfaced | `blocker` / `verdict` fields naming what stops you |
| Rankings with the ranked quantity attached | Substituting the tool's pick for the user's choice |
| Suggested parameter values, labelled as such | Withholding a value because a threshold was not met |

Inputs travel with outputs: a derived value ships the quantities and constants
it was computed from, so the skill can recompute it under a different tolerance.

The argument is about failure modes, not about output types. A gate that fails
loudly costs a CASA error and a retry. A gate that fails silently costs science
never attempted, with nothing in the output recording what was forgone, and the
threshold behind it was one constant chosen for a typical case. See
`docs/session_context.md:65` — D-terms at 24° of parallactic coverage, "marginal,
solved per user direction". That is why the design prefers **posterior
verification** (measure the result, report it) over **prior gating** (refuse to
compute).

The single named exception is `ms_workflow_status.next_recommended_step`: it
gates on filesystem state rather than a scientific claim, and it fails visibly,
returning `probe_failed_*` with an `UNAVAILABLE` field instead of inferring past
an unknown. A second exception needs both of those properties.

This mirrors the professional practice: CASA tools return measurements. The interferometrist's brain — encoded in the Skill — does the science.

### 1.2 Zen Principles Applied

- **One tool, one question.** If a tool is tempted to answer two questions, it should be two tools.
- **Measurements, not gates.** Tools return structured data, including derived
  values and labels, with the inputs behind them. The LLM decides whether to
  proceed. (This supersedes an earlier "numbers, not narratives" rule, which
  constrained output *type* and so nominally banned a derived quantity like a
  ratio of two measurements. Type was never the risk; gating is. See §1.1.)
- **Explicit uncertainty.** Every field that could not be retrieved carries a completeness flag. Silence is never used to indicate failure.
- **Provenance always.** Every returned value states which CASA call produced it, so results are independently verifiable.
- **Graceful degradation over hard failure.** A tool with partial data returns what it has plus a clear account of what is missing and why.
- **Fail loud on identity-critical metadata.** When missing metadata would make all telescope-specific interpretation wrong, raise an exception with a repair path. A confident wrong answer is worse than an honest failure. This applies specifically to telescope identity and antenna table completeness — do not guess, do not silently degrade, tell the user exactly what is missing and how to fix it.

### 1.3 Target Telescopes

Phase 1 is designed for and tested against:
- **VLA / JVLA / EVLA** (NRAO)
- **MeerKAT** (SARAO)
- **uGMRT** (NCRA-TIFR)

Data converted from UVFITS, MeasurementSet v2, and telescope-specific archive formats (NRAO Archive, MeerKAT archive, GMRT LTA) must all be handled.

---

## 2. CASA Access Layer

### 2.1 Two-Level Access Pattern

All data retrieval uses two CASA tools, applied in order of preference:

**Level 1 — `msmetadata` (msmd)**  
The programmatic metadata interface. Fast, safe, read-only. Used for all structural metadata: fields, scans, spectral windows, antennas, intents.

```
msmd.open(ms_path)
# ... queries ...
msmd.close()
```

**Level 2 — `casatools.table` (tb)**  
Direct subtable access. Used when `msmetadata` does not expose a quantity, or when raw column data is needed (UVW, FLAG column statistics, ANTENNA positions as ECEF XYZ arrays).

```
tb.open(ms_path + "/ANTENNA")
positions = tb.getcol("POSITION")   # ECEF XYZ, metres
tb.close()
```

`listobs` is **never used programmatically.** It is a human-readable formatter, not an API.

### 2.2 Subtable Map

The following subtables are accessed in Layers 1 and 2:

| Subtable | Access method | Used for |
|----------|---------------|----------|
| MAIN (implicit) | tb | Antenna ID cross-check, UVW column |
| ANTENNA | tb / msmd | Names, positions, stations, diameters |
| FIELD | msmd | Names, directions (J2000), source IDs |
| SPECTRAL_WINDOW | msmd / tb | Frequencies, bandwidths, channel counts |
| POLARIZATION | tb | Correlation types (XX/YY, RR/LL, XY/YX etc.) |
| DATA_DESCRIPTION | msmd | SpW ↔ polarization ID mapping |
| OBSERVATION | tb | Telescope name, observer, project, time range |
| SCAN (not always present) | msmd | Scan numbers, fields, intents, time ranges |
| FLAG_CMD | tb | Pre-existing online flag commands |
| HISTORY | tb | Processing history log (provenance) |

### 2.3 Transport Configuration

The server supports three deployment modes, selected at startup via environment variable `RADIO_MCP_TRANSPORT`:

| Mode | Transport | Use case |
|------|-----------|----------|
| `stdio` (default) | stdio | Local machine, Claude Desktop, single user |
| `http` | Streamable HTTP | Remote server, HPC cluster, multi-user |
| `auto` | stdio if interactive, http if daemonised | Flexible deployment |

Port for HTTP mode: `RADIO_MCP_PORT` (default: `8000`).

CASA must be importable in the same Python environment as the server. On HPC systems where CASA lives in a module, the recommended pattern is a thin wrapper script that loads the module before launching the MCP server.

---

## 3. Missing Metadata Strategy

Archival and converted data routinely has incomplete metadata. The following four failure modes are handled explicitly. All others surface as `UNAVAILABLE` with a raw error string.

### 3.1 Missing Scan Intents

**Symptom:** `msmd.intentsforfield()` returns empty sets, or all fields have intent `""`  
**Cause:** UVFITS conversion, early MeerKAT data, GMRT LTA exports  
**Strategy — the decision is per FIELD, not per MS:**
1. The field has intents → its role is derived from them, flagged `COMPLETE`.
2. The field has none, but its name matches the bundled catalogue (see §3.5) → the catalogue role, flagged `INFERRED`. The note must say this is what the source is *suitable for*, not evidence of how this observation used it.
3. Neither → `UNAVAILABLE`. No guess is substituted.

**Why per field.** An earlier revision gated step 2 on a whole-MS coverage threshold: below 50% of fields carrying intents, the catalogue fallback switched on for everything; at or above it, the fallback was unreachable. A single field missing its intents inside an otherwise well-populated MS therefore got no role at all, even where the catalogue could have answered. Coverage is a property of the MS; having intents is a property of the field, and only the second one should decide.

The 50% threshold survives as *reporting* only — it raises a warning that overall intent coverage is thin. It no longer steers behaviour.

**Intents lose to nothing, and beat the catalogue.** Where both speak and their role sets are **disjoint**, the intents win and a warning names both sides. Disjointness is the test, not inequality: a source the catalogue lists as flux *and* bandpass, used here as bandpass only, is a narrower truth rather than a contradiction. The case this exists for is a catalogue flux calibrator whose intents say `OBSERVE_TARGET` — real, and observed on ALMA 3C286 Band 6 data.

**Role vocabulary.** Intents name more roles than the catalogue can: `flux`, `bandpass`, `phase`, `amplitude`, `delay`, `polangle`, `polleakage`, `target`, `check`. The response field is therefore `field_role`, not `calibrator_role` — `target` is not a kind of calibrator. `catalogue_role` carries the catalogue's answer alongside it as a cross-check.

Technical intents that ride along on calibrators and targets alike — `CALIBRATE_ATMOSPHERE`, `CALIBRATE_POINTING`, `CALIBRATE_WVR` and friends — yield **no** role. They record what was measured, not what the field is for; mapping them would give every ALMA field a role.

### 3.2 Missing or Unknown Telescope Name

**Symptom:** `OBSERVATION::TELESCOPE_NAME` is empty, `"UNKNOWN"`, or absent  
**Cause:** UVFITS-converted MSs may carry no telescope name at all. Antenna names in UVFITS-origin data are frequently numeric strings (`"1"`, `"2"`, ...) with no station identifiers — antenna name pattern inference is therefore unreliable and is not attempted.

**Strategy:** Raise `INSUFFICIENT_METADATA` immediately.

```
error_type: INSUFFICIENT_METADATA
message: "TELESCOPE_NAME is missing or unrecognised. Cannot determine band, 
          primary beam, array configuration, or any telescope-specific quantity.
          
          To fix: set the telescope name in the OBSERVATION subtable:
            tb.open('<ms>/OBSERVATION', nomodify=False)
            tb.putcell('TELESCOPE_NAME', 0, 'VLA')   # or 'MeerKAT', 'GMRT'
            tb.close()
          
          Then re-run this tool."
```

No telescope-specific derived quantities are computed. No silent fallback. The user must supply the metadata before proceeding.

### 3.3 Incomplete Antenna Table

**Symptom:** Antenna IDs present in MAIN table (`ANTENNA1`, `ANTENNA2` columns) but absent from the ANTENNA subtable, **or** antenna names in ANTENNA subtable are purely numeric (UVFITS conversion artefact with no station metadata).

**Detection:**
- Numeric-only names: `all(name.isdigit() for name in antenna_names)`
- Orphaned IDs: `set(main_ant1) ∪ set(main_ant2)` not a subset of `set(antenna_subtable_rows)`

**Strategy:** Raise `INSUFFICIENT_METADATA` immediately.

```
error_type: INSUFFICIENT_METADATA
message: "Antenna table is incomplete or contains only numeric antenna names 
          (common in UVFITS-converted data). Cannot compute baseline lengths, 
          array configuration, shadowing, or flag fractions without a complete 
          antenna table.
          
          Orphaned antenna IDs found in MAIN table: [list]
          
          To fix: populate the ANTENNA subtable with correct names, stations, 
          and ECEF positions (ITRF or WGS84). Contact the observatory archive 
          or use the original telescope-format data before UVFITS conversion."
```

Partial antenna tables (some antennas fully described, some orphaned) follow the same pattern — raise rather than silently compute a subset. The subset result would be misleading: baseline statistics, for example, would be systematically biased toward shorter baselines if outer antennas are missing.

### 3.4 Invalid Field Coordinates

**Symptom:** Field directions are (0, 0) in J2000, or all fields share identical coordinates  
**Detection:** Any field with RA=0, Dec=0 to within 1 arcminute is flagged as suspect (the true position (0,0) — on the meridian at the equator — is essentially never a real astronomical target).  
**Strategy:**
1. Flag affected fields `COORD_SUSPECT`.
2. Return the raw coordinate value (do not silently replace).
3. Mark elevation, parallactic angle, and phase-cal separation as `UNAVAILABLE` for those fields.

### 3.5 Bundled Calibrator Catalogue

> **Canonical source: `src/ms_inspect/util/calibrators.py`.**
> The catalogue is a list of `CalibratorEntry` dataclasses in that module. It
> is not a JSON file and never has been. Read the entries there; this section
> describes only the schema and the rules that govern it.
>
> Earlier revisions of this document inlined a copy of the catalogue data. That
> copy drifted — it kept eight entries and a set of flux-standard strings CASA
> does not accept, long after the module had been corrected. Do not reintroduce
> a second copy here.

A compact, bundled catalogue covers the primary and bandpass calibrators for the supported telescopes, plus the solar-system bodies ALMA uses as flux standards. Scope is intentionally restricted to calibrators — phase calibrators are field-specific and not listed here. This catalogue is used for:
1. Intent inference (§3.1) — identifying calibrator fields when intents are absent
2. Flux model validation — checking that the named source has a known flux standard
3. Resolved source detection — warning when a calibrator requires a component model

#### Catalogue Schema

`CalibratorEntry` fields, in declaration order:

| field | type | meaning |
|---|---|---|
| `canonical_name` | `str` | the name reported back to the caller |
| `aka` | `list[str]` | alternative names and coordinate strings, matched after normalisation |
| `role` | `list[str]` | `'flux'`, `'bandpass'` — what the source is *suitable for*, not what a given observation used it for |
| `telescopes` | `list[str]` | `'VLA'`, `'MeerKAT'`, `'uGMRT'`, `'ALMA'` |
| `resolved` | `bool` | needs a component model on long baselines; drives §3.5 handling below |
| `flux_standard` | `str \| None` | CASA's own standard string, or `None` |
| `freq_range_ghz` | `tuple[float, float] \| None` | this source's validity range under its standard |
| `solar_system` | `bool` | moving target; position is not a discriminator |
| `constant_brightness_temperature` | `bool` | CASA models it at one brightness temperature, so no validity range exists |
| `notes` | `str \| None` | free text surfaced to the caller as a warning |
| `safe_uv_range_klambda` | `dict[str, UVRangeEntry]` | per-band safe maximum baseline; see below |
| `casa_model_available` | `bool` | a CASA component model ships for this source |
| `casa_model_name` | `str \| None` | that model's name, for the `setjy` call |

Four rules constrain the data, each enforced by a test in `tests/unit/test_calibrators.py`:

1. **`flux_standard` is spelled exactly as `setjy` accepts it** — a space before the year, not a hyphen. `'Perley-Butler 2017'`, not `'Perley-Butler-2017'`. `None` means CASA has no standard for the source, and it must be given an explicit manual flux density. It must never be routed to a standard string. PKS0408-65 is the case that forces this.

2. **`freq_range_ghz` is per source, not per standard.** Perley-Butler 2017 spans 0.05–50 GHz *as a standard*, but Fornax A within it is valid only over 0.2–0.5 GHz. A single per-standard range would pass Fornax A at 50 GHz. `None` means the range is **unknown to us** — callers must not read it as unbounded and must not gate on it.

3. **`constant_brightness_temperature` is the one case where a missing `freq_range_ghz` is a known answer.** CASA models Mars, the four Galilean moons, Titan and the four asteroids as a uniform disk at a single brightness temperature (`FluxCalc_SS_JPL_Butler.cc`, `compute_constant_temperature`), so it codes no frequency limit — there is nothing to extrapolate. Only Venus, Jupiter, Uranus and Neptune have a frequency-dependent temperature and therefore a range. The marker must not be read as "valid everywhere": the temperature was measured over some band, and a caller must report that no range check ran rather than that one passed.

4. **`solar_system` marks a moving target.** Both the name lookup and the VLA positional cross-match must skip the coordinate test rather than mismatch on it. Every solar-system body carries `resolved=True`: apparent diameter varies over the synodic cycle, and even the smallest is resolved on ALMA's long baselines, so `False` would fail silently in the dangerous direction.

#### Resolving a field's flux standard

`resolve_flux_standard(entry, min_ghz, max_ghz)` in `util/calibrators.py` is the single answer, called by both `ms_field_list` (which reports it) and `ms_setjy` (which acts on it). Two copies could disagree, and the tool that acts would be the one that is wrong.

It returns a `StandardResolution` carrying `standard`, `flag`, `note`, `range_checked` and `needs_manual_flux`. The cases, in order:

| condition | standard | flag | `range_checked` |
|---|---|---|---|
| no catalogue match | `None` | UNAVAILABLE | false |
| `flux_standard is None` (CASA has no model) | `None`, `needs_manual_flux` | **COMPLETE** | false |
| constant brightness temperature | the standard | COMPLETE | false |
| range known, frequency unreadable | the standard | INFERRED | false |
| observed span fully inside the range | the standard | COMPLETE | **true** |
| observed span not fully inside | `None` | UNAVAILABLE | **true** |

Two deliberate choices. A source CASA cannot model is flagged **COMPLETE**, not UNAVAILABLE: we know the answer and the answer is "there is no standard". Flagging it as a gap invites a caller to fill it with a fallback, which is the one thing that must not happen. And a span that **straddles** an edge fails rather than passing on the overlap — half a band is not a usable flux scale.

`range_checked` exists because `flag` cannot carry it. COMPLETE means both "checked and inside the range" and "no range exists to check", and those are different amounts of evidence. Every consumer surfaces it: `ms_field_list` as `flux_standard_range_checked` per field, `ms_setjy` as `n_range_checked`.

Shape of a single entry, for orientation only — the values below are illustrative, not current:

```python
CalibratorEntry(
    canonical_name="CygA",
    aka=["cyg-a", "cyga", "j1959+4044", "3c405", "cygnus-a", "cygnus a"],
    role=["flux"],
    telescopes=["VLA", "uGMRT"],
    resolved=True,
    flux_standard="Perley-Butler 2017",
    freq_range_ghz=(0.05, 12.0),
    notes="Cygnus A. Double-lobed radio galaxy. ...",
    safe_uv_range_klambda={
        "L-band (1-2 GHz)": UVRangeEntry(max_klambda=50.0, reference="McKean et al. 2016"),
    },
    casa_model_available=True,
    casa_model_name="3C405_CygA",
)
```

#### Resolved Calibrator Handling Logic

When a field name matches a resolved calibrator (`resolved: true`):

1. **Compute the observation's max baseline in kλ** using the SpW centre frequency and the baseline lengths from §6.2.
2. **Look up `safe_uv_range_klambda`** for the matching band.
3. **If max baseline ≤ safe max kλ:** return a warning that the source is extended but the array is in a safe regime for this frequency.
4. **If max baseline > safe max kλ:** raise a `CALIBRATOR_RESOLVED_WARNING` — not a hard exception, but a prominently flagged warning:
   ```
   warning: "CygA is resolved at your maximum baseline (185 kλ at L-band).
             Safe UV range for L-band: ≤50 kλ (McKean et al. 2016).
             Do NOT use setjy with a point-source model.
             Use: setjy(vis=..., field='CygA', model='3C405_CygA')
             CASA component model available: 3C405_CygA"
   ```
5. **If band not in `safe_uv_range_klambda`:** warn that the safe UV range is unknown for this frequency, state that a component model must be provided before calibration, and flag as `UNAVAILABLE` for that specific quantity.

Matching uses the `aka` list, case-insensitive, with leading/trailing whitespace stripped and common separators (`-`, `+`, `_`, space) normalised.

---

## 4. Completeness Flag Schema

Every field in every tool response carries one of five completeness flags:

| Flag | Meaning |
|------|---------|
| `COMPLETE` | Retrieved directly from the MS without ambiguity |
| `INFERRED` | Not present in the MS; derived heuristically (with stated method) |
| `PARTIAL` | Present but incomplete (e.g., only some antennas have positions) |
| `SUSPECT` | Present but fails a sanity check (e.g., coordinates at origin) |
| `UNAVAILABLE` | Could not be retrieved; reason stated |

These flags appear at the field level in JSON responses and as inline markers `[INFERRED]`, `[SUSPECT]` etc. in Markdown responses.

---

## 5. Layer 1 Tools — Orientation

### 5.1 `ms_observation_info`

**Question answered:** Who observed this, when, with what telescope, and for how long?

**CASA calls:**
- `tb.open(ms + "/OBSERVATION")` → `TELESCOPE_NAME`, `OBSERVER`, `PROJECT`, `TIME_RANGE`
- Missing telescope → §3.2 inference

**Returns:**
```json
{
  "telescope_name":   { "value": "VLA",        "flag": "COMPLETE" },
  "observer":         { "value": "J. Doe",     "flag": "COMPLETE" },
  "project_code":     { "value": "17A-001",    "flag": "COMPLETE" },
  "obs_start_utc":    { "value": "2017-03-15 10:23:01 UTC", "flag": "COMPLETE" },
  "obs_end_utc":      { "value": "2017-03-15 14:51:33 UTC", "flag": "COMPLETE" },
  "total_duration_s": { "value": 16112,        "flag": "COMPLETE" },
  "history_entries":  { "value": 42,           "flag": "COMPLETE" }
}
```

**Edge cases:** OBSERVATION subtable may have multiple rows (e.g., concatenated MSs). Return all rows; flag as `PARTIAL` if TIME_RANGE rows are non-contiguous.

---

### 5.2 `ms_field_list`

**Question answered:** What are the observed fields, their sky positions, and their calibration roles?

**CASA calls:**
- `msmd.fieldnames()` → names
- `msmd.phasecenter(field_id)` → J2000 RA/Dec (radians)
- `msmd.intentsforfield(field_id)` → intent strings
- Intent absent → §3.1 heuristic + calibrator catalogue §3.5

**Returns:** Array of field objects:
```json
{
  "field_id": 0,
  "name": "3C286",
  "ra_j2000_deg":  { "value": 202.7845, "flag": "COMPLETE" },
  "dec_j2000_deg": { "value": 30.5092,  "flag": "COMPLETE" },
  "intents":       { "value": ["CALIBRATE_FLUX", "CALIBRATE_BANDPASS"], "flag": "COMPLETE" },
  "calibrator_match": { "value": "3C286", "flag": "INFERRED",
                        "note": "Matched by field name to bundled catalogue" }
}
```

**Edge cases:** Mosaic fields (many pointings with same source name, different coords) — group by source name, report N pointings.

---

### 5.3 `ms_scan_list`

**Question answered:** What is the time-ordered sequence of scans — field, duration, intent?

**CASA calls:**
- `msmd.scansforfield(field_id)` → scan numbers per field
- `msmd.timesforscan(scan_num)` → timestamps (MJD seconds)
- `msmd.intentsforscans([scan_num])` → per-scan intents
- `msmd.fieldsforscan(scan_num)` → field ID per scan

**Returns:** Ordered array of scan records:
```json
{
  "scan_number": 1,
  "field_id": 0,
  "field_name": "3C286",
  "intent":        { "value": "CALIBRATE_FLUX", "flag": "COMPLETE" },
  "start_utc":     { "value": "2017-03-15 10:23:01 UTC", "flag": "COMPLETE" },
  "end_utc":       { "value": "2017-03-15 10:33:01 UTC", "flag": "COMPLETE" },
  "duration_s":    { "value": 600, "flag": "COMPLETE" },
  "integration_s": { "value": 2,   "flag": "COMPLETE" },
  "spw_ids":       { "value": [0,1,2,3], "flag": "COMPLETE" }
}
```

**Edge cases:** Subscans (some telescopes split scans into subscans). Aggregate to scan level; note subscan count.

---

### 5.4 `ms_scan_intent_summary`

**Question answered:** How is the total observing time distributed across calibration and science intents?

**CASA calls:** Aggregated from scan list data (no new CASA calls beyond §5.3).

**Returns:**
```json
{
  "total_duration_s": 16112,
  "by_intent": [
    { "intent": "CALIBRATE_FLUX",      "total_s": 900,  "fraction": 0.056 },
    { "intent": "CALIBRATE_BANDPASS",  "total_s": 600,  "fraction": 0.037 },
    { "intent": "CALIBRATE_PHASE",     "total_s": 2400, "fraction": 0.149 },
    { "intent": "OBSERVE_TARGET",      "total_s": 12212,"fraction": 0.758 }
  ],
  "intent_completeness": "COMPLETE"
}
```

**Note:** If intents were inferred (§3.1), the `intent_completeness` field is `INFERRED`.

---

### 5.5 `ms_spectral_window_list`

**Question answered:** What is the frequency and channel structure of each spectral window?

**CASA calls:**
- `msmd.nspw()` → count
- `msmd.chanfreqs(spw_id)` → channel centre frequencies (Hz)
- `msmd.chanwidths(spw_id)` → channel widths (Hz)
- `msmd.bandwidths(spw_id)` → total SpW bandwidth (Hz)
- `tb.open(ms + "/POLARIZATION")` → correlation type codes → mapped to labels (XX, YY, RR, LL, XY, YX, RL, LR)
- `tb.open(ms + "/DATA_DESCRIPTION")` → SpW ↔ polarization ID mapping
- Band name: `_freq_to_band(centre_freq, telescope)` (internal helper, §3.2 context)

**Returns:** Array of SpW objects:
```json
{
  "spw_id": 0,
  "centre_freq_hz":  { "value": 1.4e9,   "flag": "COMPLETE" },
  "centre_freq_fmt": "1.4000 GHz",
  "total_bw_hz":     { "value": 128e6,   "flag": "COMPLETE" },
  "n_channels":      { "value": 64,      "flag": "COMPLETE" },
  "channel_width_hz":{ "value": 2e6,     "flag": "COMPLETE" },
  "freq_min_hz":     { "value": 1.336e9, "flag": "COMPLETE" },
  "freq_max_hz":     { "value": 1.464e9, "flag": "COMPLETE" },
  "correlations":    { "value": ["RR","RL","LR","LL"], "flag": "COMPLETE" },
  "band_name":       { "value": "L-band (1–2 GHz)", "flag": "COMPLETE" }
}
```

**Edge cases:**
- Channels of unequal width (rare, but some GMRT data): return per-channel array, flag as `PARTIAL`.
- Frequency-averaged SpW (1 channel): flag for calibrators only — warn that no per-channel bandpass is possible.

---

### 5.6 `ms_correlator_config`

**Question answered:** What are the correlator dump time and polarization basis?

**CASA calls:**
- `msmd.exposuretime(scan_num)` → integration/dump time (seconds) — use first science scan
- Polarization basis inferred from correlation types (§5.5): `RR/LL/RL/LR` = circular, `XX/YY/XY/YX` = linear
- `msmd.nfields()`, `msmd.nscans()` as sanity context

**Returns:**
```json
{
  "dump_time_s":       { "value": 2.0,       "flag": "COMPLETE" },
  "polarization_basis":{ "value": "circular", "flag": "COMPLETE" },
  "full_stokes":       { "value": true,       "flag": "COMPLETE",
                         "note": "All four correlation products present" },
  "n_fields":  4,
  "n_scans":  22
}
```

---

## 6. Layer 2 Tools — Instrument Sanity

### 6.1 `ms_antenna_list`

**Question answered:** Which antennas are in this dataset, where are they, and are they fully described?

**CASA calls:**
- `tb.open(ms + "/ANTENNA")` → `NAME`, `STATION`, `POSITION` (ECEF XYZ, metres), `DISH_DIAMETER`, `MOUNT`
- Cross-check antenna IDs in MAIN table vs ANTENNA subtable → §3.3

**Returns:** Array of antenna objects:
```json
{
  "antenna_id": 0,
  "name":     { "value": "ea01",    "flag": "COMPLETE" },
  "station":  { "value": "W36",     "flag": "COMPLETE" },
  "x_m":      { "value": -1601185.4,"flag": "COMPLETE" },
  "y_m":      { "value": -5041978.1,"flag": "COMPLETE" },
  "z_m":      { "value":  3554876.5,"flag": "COMPLETE" },
  "diameter_m":{ "value": 25.0,     "flag": "COMPLETE" },
  "mount":    { "value": "ALT-AZ",  "flag": "COMPLETE" }
}
```

Plus a summary block:
```json
{
  "n_antennas_in_antenna_table": 27,
  "n_antennas_in_main_table":    27,
  "orphaned_antenna_ids":        [],
  "antenna_table_completeness":  "COMPLETE"
}
```

---

### 6.2 `ms_baseline_lengths`

**Question answered:** What are the baseline lengths in this array, and what resolution do they imply?

**CASA calls:**
- Antenna ECEF positions from §6.1 (tb, ANTENNA subtable)
- Max baseline: `max over all (i,j) pairs of ||pos_i - pos_j||`
- Min baseline: `min over all (i,j) pairs of ||pos_i - pos_j||` (excluding zero = autocorrelations)
- Derived: angular resolution (arcsec) = `λ / B_max` at centre frequency of each SpW  
  `λ / B_min` = maximum recoverable angular scale (largest angular scale, LAS)

**Note:** This tool computes from antenna positions, not from the UVW column. The UVW column reflects the actual projected baselines during observation — that would be a Layer 3 tool (`ms_uv_coverage_stats`, **not implemented** — no
such tool is registered on any of the three servers). Position-based baseline lengths give the physical maximum; UVW-based gives the actual UV sampling. Both are needed. This tool is Layer 2 (instrument sanity); the UV coverage tool is Layer 3.

**Returns:**
```json
{
  "n_baselines": 351,
  "min_baseline_m":   { "value": 35.1,    "flag": "COMPLETE" },
  "max_baseline_m":   { "value": 36403.4, "flag": "COMPLETE" },
  "median_baseline_m":{ "value": 9823.1,  "flag": "COMPLETE" },
  "per_spw_derived": [
    {
      "spw_id": 0,
      "centre_freq_hz": 1.4e9,
      "resolution_arcsec_approx": { "value": 1.32, "flag": "COMPLETE",
        "note": "θ ≈ λ/B_max; ignores weighting and taper" },
      "las_arcsec_approx": { "value": 1372.0, "flag": "COMPLETE",
        "note": "θ ≈ λ/B_min; maximum recoverable scale" }
    }
  ]
}
```

---

### 6.3 `ms_elevation_vs_time`

**Question answered:** At what elevations were each field observed, and when was the telescope below a safe elevation threshold?

**CASA calls:**
- Field J2000 coordinates from §5.2
- Array centre position: mean of antenna ECEF positions → convert to geodetic (lat/lon)
- Scan time ranges from §5.3
- Elevation computed using `astropy.coordinates` (SkyCoord + EarthLocation + AltAz frame) — **not** CASA, to avoid CASA's measures daemon dependency which is fragile in remote/HPC environments
- Default warning threshold: 20° elevation (configurable parameter)

**Returns:** Per-field, per-scan elevation statistics:
```json
{
  "field_id": 2,
  "field_name": "J1407+2827",
  "scans": [
    {
      "scan_number": 5,
      "start_utc": "2017-03-15 11:02:00 UTC",
      "end_utc":   "2017-03-15 11:06:00 UTC",
      "el_start_deg": { "value": 42.3, "flag": "COMPLETE" },
      "el_end_deg":   { "value": 44.1, "flag": "COMPLETE" },
      "el_min_deg":   { "value": 42.3, "flag": "COMPLETE" },
      "below_threshold": false
    }
  ],
  "threshold_deg": 20.0
}
```

**Edge cases:** Invalid field coordinates (§3.4) → elevation `UNAVAILABLE` for that field.

---

### 6.4 `ms_parallactic_angle_vs_time`

**Question answered:** How much parallactic angle rotation occurred for each field — relevant to polarization calibration and feed response?

**CASA calls / computation:**  
Parallactic angle computed via `astropy` using:

```python
from astropy.coordinates import SkyCoord, EarthLocation, AltAz
from astropy.time import Time
import astropy.units as u

# Hour angle from LST - RA
ha = lst - ra  # both in radians

# Sky-frame parallactic angle (North through East)
pa_sky = np.arctan2(
    np.cos(lat) * np.sin(ha),
    np.sin(lat) * np.cos(dec) - np.cos(lat) * np.sin(dec) * np.cos(ha)
)
```

#### Convention Offset — Critical Detail

**astropy** returns the sky-frame PA: angle of the great circle to NCP, measured North through East.  
**CASA** returns the feed-frame PA: angle of the receptor reference direction relative to the sky, which for ALT-AZ mounts with standard feed orientation differs from the sky-frame PA by −π/2.

The relationship for ALT-AZ mounts is:
```
PA_feed (CASA convention) = PA_sky (astropy) − π/2
```

This offset is **mount-type and telescope dependent.** The correction table is:

| Telescope | Mount | Offset from astropy PA_sky |
|-----------|-------|---------------------------|
| VLA | ALT-AZ | −π/2 |
| MeerKAT | ALT-AZ | −π/2 |
| uGMRT | ALT-AZ | −π/2 |
| WSRT (legacy) | Equatorial | 0 (PA constant, feed rotates mechanically) |

**Both values are returned.** The user (or downstream Skill reasoning) chooses which is appropriate for the task. D-term solving requires the feed-frame PA range.

**Validation requirement (pre-ship):** Before this tool ships, the PA_feed output must be cross-validated against `casatools.measures` for a reference VLA observation with known scan times and field coordinates. The test case and offset correction must be confirmed empirically to ≤0.1° accuracy.

**Returns:** Per-field summary:
```json
{
  "field_id": 0,
  "field_name": "3C286",
  "mount_type": "ALT-AZ",
  "pa_sky_start_deg":  { "value": -42.1, "flag": "COMPLETE",
    "note": "astropy sky-frame PA, North through East" },
  "pa_sky_end_deg":    { "value":  31.7, "flag": "COMPLETE" },
  "pa_sky_range_deg":  { "value":  73.8, "flag": "COMPLETE" },
  "pa_feed_start_deg": { "value": -132.1, "flag": "COMPLETE",
    "note": "Feed-frame PA = PA_sky − 90°, matches CASA convention for ALT-AZ" },
  "pa_feed_end_deg":   { "value": -58.3,  "flag": "COMPLETE" },
  "pa_feed_range_deg": { "value":  73.8,  "flag": "COMPLETE" },
  "convention_offset_deg": -90.0,
  "convention_note": "PA_feed = PA_sky - 90.0 degrees (ALT-AZ mount, VLA convention)",
  "validation_status": "PENDING"
}
```

**Note for equatorially-mounted dishes:** PA_feed is constant throughout the observation. Flag `pa_feed_range_deg = 0.0` and note that feed rotation is handled mechanically — the parallactic angle coverage criterion for D-term solving does not apply.

---

### 6.5 `ms_shadowing_report`

**Question answered:** Were any antennas shadowed by others during the observation, and how much data is affected?

**CASA calls:**
- One `casatasks.flagdata(vis=..., mode='list', action='calculate', savepars=False, flagbackup=False)` run carrying three agents: `mode='summary' name='shadow_before'`, `mode='shadow' tolerance=...`, `mode='summary' name='shadow_after'`. The shadow contribution is `after − before`, overall and per antenna. `action='calculate'` computes in memory; nothing is written to the MS.
- FLAG_CMD subtable: check for pre-existing shadow flags applied online

If `casatasks` cannot be imported, or the `flagdata` call raises, `method` is
flagged `INFERRED` and only the FLAG_CMD entries are reported. If the run
returns no usable summary pair, `method` is `UNAVAILABLE` — never a zero. There
is no geometric fallback.

**Why the leading summary.** `[RUN]` 2026-07-31, casatasks 6.7.5.18, 3C391
D-config: `flagdata(mode='shadow', action='calculate')` on its own returns an
**empty dict**, because `action='calculate'` emits a report only when the run
includes a summary agent. The old code read that empty dict as
`shadow_flag_fraction = 0.0`, COMPLETE. A trailing summary alone is not enough
either: it counts the flags already in the MS (34% on that dataset), so only
the difference isolates the shadow agent.

**Verified.** `[RUN]` same MS and CASA version. Control: swapping the shadow
agent for `mode='manual' antenna='0'` moves the delta by 13,339,392 of
216,417,024, so the trailing summary demonstrably sees the middle agent's work.
With the shadow agent the delta is 0, corroborated by the geometry (minimum
projected baseline 28.0 m against 25 m dishes) and by `action='apply'` on a
scratch copy. `tolerance_m` is **not** verified: the delta did not respond to
tolerances from 0 to 1e6 m, so a non-default tolerance ships flagged `SUSPECT`.

**Returned fields** (names read from `tools/shadowing.py`):

| Field | Type | Notes |
|---|---|---|
| `shadowing_detected` | field(bool \| null) | `null` + `UNAVAILABLE` when unmeasured. `true` from FLAG_CMD alone is flagged `PARTIAL` |
| `shadow_flag_fraction` | field(float \| null) | `n_shadow_flagged / n_total_rows` |
| `n_shadow_flagged`, `n_total_rows` | int \| null | `null`, never `0`, when unmeasured |
| `tolerance_m` | float | as passed in |
| `method` | field(str) | which path produced the numbers, and its flag |
| `shadowed_antennas` | list | `antenna_name`, `shadow_flag_fraction`, `n_flagged`, `n_total` |
| `flag_cmd_shadow_entries` | list | `row`, `reason`, `command`, `time` |
| `n_flag_cmd_shadow_entries` | int | |

---

### 6.6 `ms_antenna_flag_fraction`

**Question answered:** What fraction of data is already flagged per antenna, before any calibration flagging is applied?

**CASA calls:**
- `tb.open(ms)` → `FLAG` column — shape `[n_corr, n_chan, n_row]`
- `tb.getcol("ANTENNA1")`, `tb.getcol("ANTENNA2")` → row-to-antenna mapping
- `tb.open(ms + "/FLAG_CMD")` → pre-existing online flag commands, counted per antenna

#### Parallel Read Strategy

The FLAG column of a large MS (VLA wideband, MeerKAT 32k channel mode) can be tens to hundreds of GB. Sequential row-by-row reads are prohibitively slow. Strategy:

1. **Determine total row count:** `tb.nrows()`
2. **Partition into N row-range chunks** where N = `min(n_cpu, RADIO_MCP_WORKERS, 8)`. Default workers controlled by `RADIO_MCP_WORKERS` env var (default: `4`).
3. **Each worker** opens the table independently (`tb.open()` is re-entrant for read-only access in CASA 6.x) and reads its row range:
   ```python
   chunk_flags = tb.getcolslice(
       "FLAG",
       blc=[0, 0],      # all correlations, all channels
       trc=[-1, -1],
       startrow=chunk_start,
       nrow=chunk_size
   )
   # Returns shape [n_corr, n_chan, chunk_size]
   chunk_ant1 = tb.getcol("ANTENNA1", startrow=chunk_start, nrow=chunk_size)
   chunk_ant2 = tb.getcol("ANTENNA2", startrow=chunk_start, nrow=chunk_size)
   ```
4. **Each worker returns** per-antenna flagged count and total count arrays.
5. **Main process aggregates** across all workers: `flag_fraction[ant] = total_flagged[ant] / total_rows[ant]`.
6. **Progress reporting** via MCP Context (`ctx.report_progress`) after each chunk completes.

**Note on autocorrelations:** Rows where `ANTENNA1 == ANTENNA2` are autocorrelations. These are excluded from the flag fraction computation unless explicitly requested — they are not used in calibration or imaging.

**Returns:**
```json
{
  "per_antenna": [
    {
      "antenna_id": 0,
      "antenna_name": "ea01",
      "flag_fraction": { "value": 0.032, "flag": "COMPLETE" },
      "n_flagged_rows": 1024,
      "n_total_rows": 31744,
      "n_flag_commands_online": 0
    },
    {
      "antenna_id": 5,
      "antenna_name": "ea06",
      "flag_fraction": { "value": 0.891, "flag": "COMPLETE" },
      "n_flagged_rows": 28288,
      "n_total_rows": 31744,
      "n_flag_commands_online": 12
    }
  ],
  "overall_flag_fraction": { "value": 0.087, "flag": "COMPLETE" },
  "autocorrelations_excluded": true,
  "n_workers_used": 4,
  "flag_source": "FLAG column (parallel read) + FLAG_CMD subtable"
}
```

---

## 7. Response Contract

### 7.1 All Tools

Every tool response is a JSON object with this envelope:

```json
{
  "tool": "ms_antenna_list",
  "ms_path": "/data/obs/target.ms",
  "status": "ok",
  "completeness_summary": "COMPLETE",
  "data": { ... },
  "warnings": [],
  "provenance": {
    "casa_calls": ["tb.open(ANTENNA)", "tb.getcol(NAME, STATION, POSITION, DISH_DIAMETER, MOUNT)"],
    "casa_version": "6.6.1"
  }
}
```

`completeness_summary` is the worst-case flag across all fields: if any field is `SUSPECT`, the summary is `SUSPECT`.

`warnings` is a list of strings for non-fatal issues (e.g. "2 antennas have placeholder positions").

On tool error:
```json
{
  "tool": "ms_antenna_list",
  "ms_path": "/data/obs/target.ms",
  "status": "error",
  "error_type": "MS_NOT_FOUND",
  "message": "Measurement Set not found at /data/obs/target.ms. Check the path — MS directories contain a table.info file.",
  "data": null
}
```

### 7.2 Error Types

| Code | Meaning |
|------|---------|
| `MS_NOT_FOUND` | Path does not exist |
| `NOT_A_MEASUREMENT_SET` | Path exists but is not an MS (missing table.info) |
| `SUBTABLE_MISSING` | Expected subtable (e.g. ANTENNA) absent |
| `INSUFFICIENT_METADATA` | Identity-critical metadata absent — telescope name unknown, or antenna table incomplete/numeric-only. Tool cannot proceed safely. Repair path included in message. |
| `CALIBRATOR_RESOLVED_WARNING` | Non-fatal: calibrator is resolved at this array configuration. Component model required. |
| `CASA_NOT_AVAILABLE` | casatools not installed or not importable |
| `CASA_OPEN_FAILED` | casatools raised an exception on open |
| `COMPUTATION_ERROR` | Internal error during derived quantity computation |

---

## 8. Tool Inventory Summary

### Layer 1 — Orientation (6 tools)

| Tool | Primary CASA API | Key derived quantities |
|------|-----------------|----------------------|
| `ms_observation_info` | tb → OBSERVATION subtable | Total duration, UTC range |
| `ms_field_list` | msmd.fieldnames, msmd.phasecenter, msmd.intentsforfield | Calibrator role (with fallback), sky coords |
| `ms_scan_list` | msmd.timesforscans, msmd.intentsforscans, msmd.fieldsforscan | Duration per scan, integration time |
| `ms_scan_intent_summary` | Aggregated from scan list | Time fractions per intent |
| `ms_spectral_window_list` | msmd.chanfreqs, msmd.chanwidths; tb → POLARIZATION, DATA_DESCRIPTION | Band name, channel width, correlation products |
| `ms_correlator_config` | msmd.timesforscans (dump time); tb → POLARIZATION | Dump time, polarization basis, full-Stokes flag |

### Layer 2 — Instrument Sanity (7 tools)

| Tool | Primary CASA API | Key derived quantities |
|------|-----------------|----------------------|
| `ms_antenna_list` | tb → ANTENNA subtable | ECEF positions, orphan check |
| `ms_baseline_lengths` | Computed from antenna positions | λ/B_max resolution, LAS per SpW |
| `ms_elevation_vs_time` | astropy (field coords + array geodetic pos + scan times) | Low-elevation warnings per scan |
| `ms_parallactic_angle_vs_time` | astropy | PA range per field, D-term solvability note |
| `ms_shadowing_report` | casatasks.flagdata(mode='list', action='calculate') [summary, shadow, summary] — read-only | Shadow flag fraction overall and per antenna, as the before/after difference |
| `ms_antenna_flag_fraction` | tb → FLAG column (chunked) | Per-antenna flag fraction, online flag commands |

**Phase 1 (Layers 1 & 2): 13 tools** — the tables above plus `ms_flag_preflight`.

> **Note on scope.** This design document was originally written for Phase 1
> (Layers 1 & 2) only. The `ms_inspect` server has since grown to **33 tools**
> as later calibration/imaging phases were implemented, and the read-only
> contract now spans the whole reduction. The remaining `ms_inspect` tools are
> catalogued below; `ms_modify` (§8b) and `ms_create` (§8c) are full MCP servers
> in their own right. Sections §9–§11 describe the original Phase 1 boundary and
> are retained for historical context.

### Instrument sanity, extended

| Tool | Primary CASA API | Key derived quantities |
|------|-----------------|----------------------|
| `ms_flag_preflight` | tb → FLAG shape/rows (no read) | Row count, data volume, runtime estimate, recommended `n_workers` |

### Calibration inspection

| Tool | What it returns |
|------|----------------|
| `ms_calsol_stats` | Per-(antenna, SpW, field) stats from G/B/K caltables — flagged fraction, SNR, amp/phase arrays, delays |
| `ms_calsol_stats_detail` | Deep-dive reader over the `.calsol_stats.npz` sidecar; full per-solution detail (`low_snr`/`amp_outliers`/`antenna`) beyond the bounded summary |
| `ms_calsol_plot` | Bokeh HTML dashboard from a single caltable (reads caltable columns directly; view routed by VisCal type) |
| `ms_plot_caltable_library` | Batch-plot an explicit list of caltables in one call; partial-success per table |
| `ms_gaincal_snr_predict` | Predict per-(antenna, SpW) SNR for a candidate solint from SEFD + MS metadata |
| `ms_verify_caltables` | Structural validation of `init_gain.g` + `BP0.b` from initial bandpass |

### Pre-calibration inspection

| Tool | What it returns |
|------|----------------|
| `ms_verify_import` | Filesystem check: MS exists + `table.info` valid + `.flagonline.txt` non-empty |
| `ms_verify_model` | Per-field MODEL_DATA sanity after setjy/setjy_polcal — default-pinned, out-of-band, and (for pol cals) missing-polarization smell tests |
| `ms_online_flag_stats` | Parse `.flagonline.txt` — command counts, antennas flagged, reason breakdown, time range |
| `ms_flag_summary` | Per-field/SpW flag fractions from `flagdata(mode='summary')` |
| `ms_verify_priorcals` | Check prior caltables (gc, opac, rq, ap) exist and are non-empty |

### Instrument and RFI inspection

| Tool | What it returns |
|------|----------------|
| `ms_refant` | Ranked reference-antenna list by geometry + flag-fraction heuristics |
| `ms_rfi_channel_stats` | Per-channel flag fractions; persistent RFI bands |
| `ms_spw_amp_severity` | Robust per-channel amplitude stats aggregated per SpW; RFI severity + estimated discardable fraction (reservoir-sampled) |
| `ms_pol_cal_conditions` | Pol calibrator identification, catalogue properties at the observed band, and per-field parallactic-angle spread ranked; no verdict |
| `ms_residual_stats` | CORRECTED − MODEL amplitude distribution per SpW (pre-rflag threshold guide) |
| `ms_corrected_stats` | Per-field parallel-hand amplitude + phase RMS of a data column, vector-averaged over the channel range (post-applycal sanity) |
| `ms_phase_cal_lookup` | Cross-match a sky position against the NRAO VLA phase-calibrator catalog; nearest source, flux, UV limits, per-config quality codes |

### Imaging inspection

| Tool | What it returns |
|------|----------------|
| `ms_image_stats` | Robust MAD-based RMS, peak, dynamic range, restoring beam, per-plane detection verdict for cubes / IQUV |

### Pipeline / workflow

| Tool | What it returns |
|------|----------------|
| `ms_workflow_status` | State probe over MS + workdir: which pipeline stages are complete + `next_recommended_step` |

**Total `ms_inspect`: 33 tools.**

---

## 8b. Write Utilities (ms_modify)

The companion `ms_modify` package **writes** to the MS. It is a full FastMCP
server in its own right (`ms_modify.server`, port 8001) exposing **16 tools**;
the same functions are also callable programmatically by skills and scripts
(e.g. for metadata repair before calibration can proceed).

| Tool | What it does |
|------|-------------|
| `ms_set_intents` | Populate STATE subtable + STATE_ID from calibrator-catalogue matching (see below) |
| `ms_apply_preflag` | Deterministic pre-cal flagging (online + shadow + clip + tfcrop) + calibrator split |
| `ms_generate_priorcals` | Generate gc/opac/rq/ap prior caltables via `gencal` |
| `ms_setjy` | Set flux models; the standard is resolved per field from that field's own frequency (§3.5); `exclude_fields` protects an overlapping pol-angle cal |
| `ms_setjy_polcal` | Set polarization-angle models for pol calibrators |
| `ms_initial_bandpass` | gaincal → bandpass → applycal; populates CORRECTED |
| `ms_apply_initial_rflag` | rflag + tfcrop on CORRECTED − MODEL residuals; requires explicit `field` |
| `ms_postcal_flag` | Post-cal RFI flagging (per-SpW robust clip → tfcrop + rflag → manual drop-tier); requires explicit `field` |
| `ms_flag_caltable` | Autoflag a caltable's solutions (mode auto-routed from VisCal) |
| `ms_apply_rflag` | General-purpose rflag pass |
| `ms_gaincal` | Phase/amp/cross-hand-delay gain calibration (incl. `gaintype='KCROSS'`) |
| `ms_polcal` | Polarization calibration: D-term leakage (Df/Df+QU) or position angle (Xf) |
| `ms_bandpass` | Bandpass calibration |
| `ms_fluxscale` | Bootstrap flux scale from a flux standard |
| `ms_applycal` | Apply caltables; write CORRECTED_DATA |
| `ms_tclean` | Generate (and optionally execute) a validated tclean imaging script |

All modify tools default to `execute=False` (emit a reviewable script) and
accept `execute=True` to run in-process.

---

## 8c. Ingestion Utilities (ms_create)

The `ms_create` package converts raw ASDM data to Measurement Sets and tracks
the reduction. It is a FastMCP server (`ms_create.server`, port 8002) with
**3 tools**:

| Tool | What it does |
|------|-------------|
| `ms_sdm_summary` | Pre-conversion ASDM inspection (read-only, no casatools): telescope, band, per-SPW line/continuum, sources + intents, scan balance |
| `ms_import_asdm` | Convert ASDM → MS (`ocorr_mode='co'`, `savecmds=True`, `applyflags=False`); writes `import_asdm.py` + `.flagonline.txt` |
| `ms_reduction_log` | Working-calls ledger: shuttle known-good calls into a per-reduction JSONL recipe (`append`/`render`/`list`) |

---

## 8d. `set_intents` detail (ms_modify)

### `set_intents` (ms_modify/intents.py)

**Problem solved:** Many MSes (UVFITS imports, older VLA archives, GMRT LTA
exports) lack scan intent metadata. The `ms_inspect` tools detect this
(heuristic mode in `ms_field_list`, §3.1) but can only infer intents
read-only. `set_intents` actually populates the STATE subtable so that
downstream calibration tools see real intents.

**Intent assignment pipeline:**
1. Primary calibrator catalogue match (`calibrators.lookup`) → flux/bandpass intents
2. VLA calibrator positional cross-match (`vla_calibrators.cone_search`, 5 arcsec radius) → phase intent
3. No match → `OBSERVE_TARGET#ON_SOURCE`

**Guard:** If ≥50% of fields already have non-empty intents, raises
`IntentsAlreadyPopulatedError`. This prevents accidental overwrites of
legitimate intent metadata.

**Write sequence:**
1. Clear and repopulate STATE subtable (OBS_MODE, CAL, SIG, SUB_SCAN, FLAG_ROW, REF)
2. Bulk-update STATE_ID column in MAIN table via `tb.putcol`
3. STATE table is closed before MAIN table is opened (avoids lock contention)

**Limitations:**
- Assigns one intent set per field (assumes intent doesn't vary by scan for the same field)
- Not atomic — partial failure leaves inconsistent MS, but the guard prevents re-run on partially-written state
- VLA cone search requires network on first call; gracefully falls back to `OBSERVE_TARGET` if unavailable

---

## 9. Out of Scope for Phase 1 (historical)

> These were the Phase 1 boundaries as originally scoped. Calibration table
> creation/application (`ms_modify`, §8b) and imaging (`ms_tclean`,
> `ms_image_stats`) have since been implemented. The list below is retained to
> document the original design intent.

The following were explicitly deferred beyond Phase 1:

- Any visibility data access (amplitudes, phases, closure quantities) — Layer 3
- Calibration table creation or application — Layer 4
- Imaging parameter recommendation — Layer 5
- VizieR / SIMBAD cross-matching (live external queries)
- Self-calibration loops
- Spectral line identification
- Wide-field mosaicking strategies

---

## 10. Resolved Decisions and Open Questions

### Resolved

| # | Decision | Resolution |
|---|----------|-----------|
| 1 | astropy vs CASA measures for elevation/PA | **astropy** — avoids CASA measures daemon fragility on HPC. PA offset correction table required per telescope. Cross-validation test case required before ship. |
| 2 | Missing telescope name strategy | **Raise `INSUFFICIENT_METADATA`** — no inference from antenna names. Antenna names in UVFITS-converted data are numeric and non-inferrable. User must supply telescope name manually. |
| 3 | Incomplete antenna table strategy | **Raise `INSUFFICIENT_METADATA`** — partial computation would bias baseline statistics. Fail loud with repair path. |
| 4 | FLAG column large-data strategy | **Parallel reads via `multiprocessing`** — N workers (default 4, max 8) each read a row-range chunk via `tb.getcolslice()`. Controlled by `RADIO_MCP_WORKERS` env var. |
| 5 | Resolved calibrators in catalogue | **Warn with UV range and model guidance** — catalogue includes Cas A, Cyg A, Tau A, Vir A with `safe_uv_range_klambda` per band. Raise `CALIBRATOR_RESOLVED_WARNING` when max baseline exceeds safe range. |
| 6 | Multi-MS handling | **Pinned** — deferred. Single MS per tool call for Phase 1. |
| 7 | Calibrator catalogue scope | **Restricted to primary/bandpass calibrators** — phase calibrators are field-specific and not catalogued here. |

### Open

1. **Python version constraint:** casatools 6.6.x requires Python 3.8. Target 3.8 minimum, test on 3.10 with compatibility note. Confirm before implementation starts.
2. **PA validation test case:** Identify a reference VLA observation (publicly available from NRAO archive) with known scan times and a bright polarised calibrator to use as the ground-truth cross-validation case for the astropy ↔ CASA PA offset.

---

## 11. What the Skill Document Will Cover

The companion `SKILL.md` for this MCP will encode the interferometrist's reasoning process in five sections:

1. **The MS data model** — what the tables and columns are, how they relate, why the MAIN table is huge and the subtables are small
2. **How to read Layer 1 output** — what a healthy scan schedule looks like, red flags (no flux calibrator, bandpass cal too short, target:cal time ratio extremes)
3. **How to read Layer 2 output** — healthy antenna count, what high per-antenna flag fraction means, elevation rules of thumb, when parallactic angle coverage matters
4. **Telescope-specific priors** — VLA array configs and what they imply, MeerKAT L vs UHF band differences, uGMRT sub-array modes, known RFI environments
5. **The diagnostic decision tree** — after calling all 12 tools, what combinations of outputs should trigger what next actions (Layer 3 tools, or intervention before proceeding)

The Skill does not call tools. It teaches the LLM how to interpret what the tools return.

---

*End of Phase 1 Design Document*