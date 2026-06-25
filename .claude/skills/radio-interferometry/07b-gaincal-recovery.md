# 07b — Gaincal Recovery Procedures

Read this only when a post-flight check in 07 Step 4b routes you to a Recovery
Tree (or to Escalation). The pre-flight classification, pre-flight checklist, and
the four post-flight checks live in 07-calibration-execution.md Step 4b; this file
holds the diagnostic procedures for when one of those checks fails.

The source-type table (Bright/Phase/Weak/Resolved thresholds) is in 07 Step 4b
"Source classification" — refer back to it where these trees say "source type".

---

## Recovery Tree 1: Caltable Not Produced

**Symptoms:** gaincal script completed, but `{WORKDIR}/gain.G` does not exist
OR exists but is empty (`n_total_solutions = 0`).

**Diagnostic path:**

1. **Check refant availability in the MS:**
   ```
   ms_antenna_list(ms_path={VIS})
   # Confirm {REFANT} name appears in antenna list
   ```
   If refant not found → use top 3 from `ms_refant` output, in order.

2. **Check prior caltables (delay.K, bandpass.B):**
   ```
   ms_verify_caltables(...)
   ```
   If either missing or empty → restart from Step 2 (delay solve).

3. **Check pre-solve flag fraction by field:**
   ```
   ms_flag_summary(ms_path={VIS}, field={FLUX_FIELD})
   ```
   - If > 50% of the data is flagged → escalate to **Pre-flagging review**
     (Section CALIBRATION_PREFLAG in 10-precal-workflow.md)
   - If < 50% but still high (30–50%) → gaincal may have failed due to insufficient SNR
     → Try **Retry option A** below

4. **Retry option A: Try alternative refants (recommended first)**
   - Run gaincal three times with refants from `ms_refant` ranked list (1st, 2nd, 3rd)
   - Each call: change only `refant={REFANT_N}` in the gaincal call; keep all other params
   - **If any produces a caltable:** use it and proceed to inspection
   - **If all three fail:** go to Retry option B

5. **Retry option B: Relax solution thresholds**
   - Change: `minsnr=3.0` (from 5.0) and `minblperant=3` (from 4)
   - Keep: same refant (use the ranked list's top antenna)
   - **If this produces a caltable:** note in summary as "degraded SNR, lower minblperant"
   - **If still fails:** escalate (see **Escalation** below)

6. **Escalation:** If all three refants + threshold relaxation fail, the gaincal
   solve is fundamentally broken. Possible causes:
   - Bandpass solve failed (re-do Step 3)
   - All major antennas are flagged (check for online flags or RFI; loop to precal)
   - Wrong field selection (re-check `{FLUX_FIELD}` from ms_field_list)
   - CASA/caltask version incompatibility (check CASA version)

---

## Recovery Tree 2: Low SNR (overall_snr_mean < 3.0)

**Symptoms:** gaincal produced a caltable, but `snr_mean < 3.0` or > 20% of
antennas have SNR < 2.0.

**Diagnostic path:**

1. **Assess the source and expected SNR:**
   - Use source classification (above) to decide if low SNR is expected
   - Bright cal (3C286, 3C147): SNR should be > 10; < 5 is bad
   - Phase cal (faint): SNR > 3 is acceptable
   - Weak source: SNR > 2 is acceptable if coverage is good
   - Resolved cal: SNR > 5 expected (extended structures need high SNR)

2. **Check if the problem is refant-dependent:**
   ```
   # Re-run gaincal with the 2nd and 3rd refants from ms_refant
   ```
   - **If SNR improves with a different refant:** use the better refant for all remaining steps
   - **If SNR remains low regardless of refant:** the data quality is poor; continue to next option

3. **Check if solint is too tight (too much vector-averaging):**
   - Current: `solint='inf'` (one solution over all scans)
   - **Retry with:** `solint='int'` (one solution per integration)
   - **Rationale:** Per-integration solutions have more data per fit; time variations
     are solved independently rather than averaged away
   - **Risk:** produces more solutions to flag; limits applycal interpolation
   - **If SNR improves:** use `solint='int'` and proceed
   - **If SNR stays low:** continue to next option

4. **Check bandpass quality:**
   - Return to Step 3 and inspect bandpass with `ms_calsol_stats`
   - If bandpass shows SNR < 5 itself → bandpass solve was weak
   - **Re-solve bandpass (Step 3)** with modified parameters, then re-attempt gain solve

5. **Check combine parameter (for weak sources only):**
   - Current: `combine=''` (no combining)
   - **Retry with:** `combine='scan'` (combine all scans on a calibrator)
   - **Rationale:** weak sources benefit from combining scans; single-scan solutions may have too few baselines
   - **Risk:** loses time resolution (may miss gain time-variation)
   - **Only for weak calibrators; do not use for flux calibrators**

6. **Escalation:** If SNR stays < 3 after trying different refants and modified solint:
   - The observation has insufficient data quality for reliable gain solutions
   - Possible causes: excessive RFI not caught by preflag, bad weather, equipment issue
   - **Action:** Document in summary, flag as "low SNR"; consider whether the dataset is usable

---

## Recovery Tree 3: Excessive Flag Jump (flag_delta > threshold)

**Symptoms:** gaincal completed and caltable exists, but `flag_after - flag_before`
exceeds the source-type threshold (e.g., > 8% for bright cal, > 12% for phase cal).

**Diagnostic path:**

1. **Classify the flag jump:**
   - Small jump (3–5% above threshold): Expected minor RFI detection; acceptable with note
   - Large jump (> 5% above threshold): Indicates real data-quality problem

2. **Check if the source is resolved:**
   - Use `ms_field_list` output and source catalogues (e.g., VLA calibrator manual)
   - Resolved sources (Cas A, Cyg A, Tau A, 3C84) are sensitive to UV range
   - **If resolved:** retry with tighter UV range
     ```
     # Add to gaincal call: uvrange='0~1000k' (or equivalent baseline limit)
     ```
   - **If point-like:** continue to next option

3. **Check for RFI in online flags:**
   ```
   ms_online_flag_stats(flag_file={ORIGINAL_ASDM}/.flagonline.txt)
   # Examine: reason_breakdown (look for RFI-like categories)
   ```
   - If heavy RFI flagging in online flags → data quality is already poor
   - Consider looping to precal for additional RFI excision

4. **Check per-antenna flag contribution:**
   ```
   ms_flag_summary(ms_path={VIS}, field={FLUX_FIELD}, per_antenna=True)
   # Identify antennas where flag_delta is largest
   ```
   - **If concentrated in 1–2 antennas:** suspect hardware issue on those antennas
     - Retry with `refant` set to an antenna NOT in the high-flag list
   - **If spread across all antennas:** systematic RFI; loop to precal

5. **Retry with longer solint (if source is unresolved):**
   - Current: `solint='inf'`
   - **Retry with:** `solint='10s'` or `solint='1min'` (example; adjust to data)
   - **Rationale:** shorter integrations capture more data per fit; longer solutions reduce outlier detection
   - **If flag_delta decreases:** use the longer solint
   - **If flag_delta unchanged:** the issue is not integration time; escalate

6. **Escalation:** If flag_delta remains > threshold after refant and solint tweaks:
   - Possible causes: strong RFI environment, broken receiver chain, sky interference
   - **Action:** Document the high flagging in the summary; consider whether the solutions are scientifically useful despite the flag delta

---

## Recovery Tree 4: Low Coverage (< 50% solutions)

**Symptoms:** gaincal produced a caltable, but `n_flagged_solutions / n_total_solutions > 0.5`
(more than half the solutions are flagged).

**Diagnostic path:**

1. **Check which antennas are missing:**
   ```
   ms_calsol_stats(caltable_path={WORKDIR}/gain.G)
   # Inspect: solutions_per_antenna (count non-flagged solutions per antenna)
   ```
   - **If 1–2 antennas are missing solutions:** likely refant issue or hardware problem
   - **If > 3 antennas missing:** data quality problem or field selection error

2. **Check if concentrated in a few antennas:**
   - **If yes:** try alternate refants (Recovery Tree 1, Retry A)
   - **If distributed:** continue to next option

3. **Relax minblperant:**
   - Current: `minblperant={MINBLPERANT}` (typically 4)
   - **Retry with:** `minblperant=3` (or lower)
   - **Rationale:** each antenna needs at least N baselines to contribute; lowering N allows peripheral antennas
   - **Risk:** weaker solutions with lower SNR
   - **If coverage improves to > 50%:** use this setting
   - **If still low:** continue to next option

4. **Check solint vs data density:**
   - If `solint='inf'` and the calibrator was observed in very few scans (< 3)
   - **Retry with:** `solint='int'` (per-integration solutions)
   - **Rationale:** more solutions per antenna across more integrations
   - **If coverage improves:** use per-integration solint
   - **If still low:** continue to next option

5. **Check prior caltables (delay.K, bandpass.B):**
   - Missing or bad prior solutions will cause downstream gaincal to fail
   - ```
     ms_verify_caltables(ms_path={VIS}, init_gain_table=..., bp_table={WORKDIR}/bandpass.B)
     ```
   - If bandpass is corrupt → restart from Step 3

6. **Escalation:** If coverage stays < 50% after all retries:
   - The dataset has fundamentally poor SNR or flagging
   - **Action:** Document and escalate to data-quality review

---

## Escalation criteria (hard stop conditions)

Stop and escalate to data-quality review if **any** of the following hold:

| Condition | Action |
|---|---|
| All 3 refants fail to produce a caltable | Check MS structure (antenna table, MAIN table consistency) |
| SNR stays < 2.0 after refant + solint + combine tries | Data quality too poor for this science goal |
| Bright flux calibrator flags jump > 20% | Strong RFI or hardware issue; loop to precal + online flags review |
| Coverage never reaches 50% | Possibly wrong field selection or MS corruption; verify with `listobs` |
| Refant not found in antenna list | MS antenna table is corrupt or incomplete |

When escalating: provide to the user:
- Which refant was tried, in order
- The SNR values or coverage % at each attempt
- The original `gain.G` output (if produced)
- A recommendation: retry preflag + RFI excision, or flag dataset as non-usable
