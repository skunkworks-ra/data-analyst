---
description: Run a full Phase 1 (orientation) and Phase 2 (instrument sanity) analysis
             on a CASA Measurement Set using the ms_inspect MCP tools. Produces a
             structured data quality report with a go/no-go calibration recommendation.
allowed-tools: ms_observation_info, ms_field_list, ms_scan_list, ms_scan_intent_summary,
               ms_spectral_window_list, ms_correlator_config, ms_antenna_list,
               ms_baseline_lengths, ms_elevation_vs_time, ms_parallactic_angle_vs_time,
               ms_shadowing_report, ms_antenna_flag_fraction
---

Run a complete Phase 1 + Phase 2 interferometric data quality analysis on this
Measurement Set: $ARGUMENTS

Follow the workflow in the radio-interferometry skill exactly:

**Phase 1 — run in this order:**
1. ms_observation_info
2. ms_field_list
3. ms_scan_list
4. ms_scan_intent_summary
5. ms_spectral_window_list
6. ms_correlator_config

**Phase 2 — run in this order (only if Phase 1 passes identity checks):**
7. ms_antenna_list
8. ms_baseline_lengths
9. ms_elevation_vs_time
10. ms_parallactic_angle_vs_time
11. ms_shadowing_report
12. ms_antenna_flag_fraction

After all 12 tools, produce the structured report defined in
04-diagnostic-reasoning.md with sections:
1. Dataset identity
2. Field summary
3. Spectral configuration
4. Data quality summary table (pass/fail per check)
5. Go / No-go calibration recommendation
