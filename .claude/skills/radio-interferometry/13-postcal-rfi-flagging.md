# 13 — Post-Calibration RFI Flagging & SpW Triage

## Purpose

After the final applycal, flag residual RFI on the phase calibrator AND the
science target, and decide which SpWs are RFI-dominated (drop) versus
salvageable (flag and keep). The pre-cal pipeline (Skill 10) flags calibrators
only; this stage extends flagging to the fields that were never cleaned, and
makes the salvage-vs-drop call per SpW.

Time-resolved RFI (intermittent-vs-persistent within a channel) is NOT covered
by the severity tool — it pools over time. Handle that with a focused
time-resolved pass on the grey-tier SpWs, after first-pass imaging.

---

## Core principle

**The tool measures. This skill decides the cut.** `ms_spw_amp_severity`
returns robust numbers; the drop/flag decisions and the empirical
image-and-compare step live here. There are no hardcoded thresholds — every cut
is read off the shape of *this dataset's* own distribution.

---

## Prerequisites

| Requirement | Why |
|---|---|
| Final applycal complete | Severity is measured on CORRECTED_DATA |
| `applycal` run with `applymode='calonly'` | Our flagging owns the FLAG column; calibration flags must not pre-empt it |
| CORRECTED populated on all fields | We measure all fields, including target + phase cal |

If applycal was run in default mode, caltable flags have already been folded in.
Re-run in `calonly` so the post-cal flag decisions here are the sole authority
over the FLAG column.

---

## Step 1 — Measure SpW severity

Run `ms_spw_amp_severity(ms_path, datacolumn='CORRECTED_DATA')` (all fields).

Key returned fields, per SpW:

| Field | Meaning |
|---|---|
| `band_floor` | Robust SpW floor (median of per-channel medians) |
| `clean_floor_anchor` | Robust floor reference taken from the quietest SpWs |
| `severity` | `band_floor` normalised to the clean reference. The drop signal. |
| `estimated_discardable_frac` | Fraction of unflagged elements above floor+Nσ. The localized-RFI magnitude. |
| `per_chan[].discardable_frac` | Same, per channel — localizes the contamination |

`severity` is anchored to the *clean* SpWs, so it stays correct even when much
of the band is contaminated (a contaminated overall median would understate it).

---

## Step 2 — Triage by severity

Do NOT use fixed severity cutoffs. The clean SpWs cluster near severity ≈ 1 by
construction (they define the anchor). Read the triage off the *shape of the
distribution* for this dataset:

| Where the SpW sits | Interpretation | Action |
|---|---|---|
| Clear outlier, far above the clean cluster | Uniformly RFI-dominated | **Drop** |
| Between the cluster and the outliers, no clear gap | Grey tier | **Defer** — image first (Step 4) |
| In the clean cluster | Defines the anchor | Keep |

The robust call is the *gap*: sort SpWs by severity and look for the break
between the bulk that sits near 1 and the handful that stand off well above it.
Those standing off are the drop tier, however large or small the numbers are in
this band. A dataset with no clear gap has no clear drop tier — say so rather
than forcing a cut.

`estimated_discardable_frac` is read the same way — relative to the spread
across this dataset's SpWs, not against an absolute threshold.

> Illustration only (not thresholds). On one L-band dataset the sorted
> severities showed a clean cluster near 1, a grey tier a few× above, and three
> SpWs standing far off — those three were dropped and the image improved. The
> numbers are dataset-specific; the *gap* is what drove the decision.

---

## Step 3 — Flag localized RFI on the keepers

For SpWs you keep, `estimated_discardable_frac` and per-channel
`discardable_frac` show where the contamination sits. Flag those channels
(or run rflag/tfcrop on CORRECTED for the phase cal + target) rather than
dropping the SpW — this preserves bandwidth and SNR.

`ms_postcal_flag` applies, in one pass: a per-SpW robust clip
(`clip_sigma`, default 5 → ceiling = median + 5·1.4826·MAD per SpW), then
tfcrop + rflag, then the manual SpW drop. The robust clip is the principled
replacement for a flat clip ceiling: it adapts to each SpW's own floor and
removes the single strong outliers that imprint sinusoidal **ripples/striping**
across the image (one bad uv sample = one 2-D sine wave). If you see regular
stripes in a low-noise image, suspect a surviving strong-amplitude outlier and
tighten the clip.

> **Extended-source warning.** The robust clip is **uv-blind**. On an extended
> source the per-SpW median is noise-dominated, so a 5σ ceiling can land near
> the genuine short-spacing flux and clip real emission — watch the image peak
> before/after. Scope the clip to long baselines with `uvrange` (e.g.
> `'>2klambda'`) so the short spacings are left to rflag, or raise `clip_sigma`.
> On a faint/compact field this caveat does not apply.

---

## Step 4 — Image, then revisit the grey tier

Image with the clear-drop SpWs (Step 2) excluded. Dropping RFI-dominated
bandwidth typically *raises* sensitivity despite the lost channels: a uniformly
contaminated SpW adds more noise than the signal its bandwidth contributes.

Then, for the grey tier, compare images with/without each grey SpW, or run the
time-resolved per-channel pass on just those SpWs to separate intermittent
bursts (rflag in time, keep most data) from persistent contamination (flag the
channel). Take the call from evidence, not from severity alone.

---

## Known limitation

`ms_spw_amp_severity` has no time axis. A channel that is bad 5% of the time
and one bad 100% of the time produce the same `discardable_frac`. The
intermittent-vs-persistent distinction requires binning on the TIME column;
that is a separate tool, run only on the grey-tier SpWs to keep it cheap.
