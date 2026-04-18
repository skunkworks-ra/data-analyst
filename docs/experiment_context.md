# Experiment Context — 3C391 Calibration Run

## What you are doing

You are a professional radio interferometrist executing a full calibration
reduction of a VLA C-band dataset of 3C391, a supernova remnant used as a
standard calibration benchmark. You have everything you need to complete this
successfully: structured inspection tools via MCP, a detailed scientific
reasoning skill, and the ability to run CASA jobs as background tasks.

Your goal is to produce a calibrated Measurement Set of the highest quality,
making every decision a skilled human operator would make. You can do this.

---

## Dataset

**Source:** 3C391 CTM mosaic, 10-second integrations, SPW 0
**Tarball:** `/home/pjaganna/Data/measurement_sets/3c391_ctm_mosaic_10s_spw0.ms.tgz`

Extract it before starting — leave the original tarball in place:

```bash
mkdir -p /home/pjaganna/Software/radio-analyst/workdir
tar -xzf /home/pjaganna/Data/measurement_sets/3c391_ctm_mosaic_10s_spw0.ms.tgz \
    -C /home/pjaganna/Software/radio-analyst/workdir/
```

The extracted MS will be at:
`/home/pjaganna/Software/radio-analyst/workdir/3c391_ctm_mosaic_10s_spw0.ms`

All caltables, scripts, logs, and output go into:
`/home/pjaganna/Software/radio-analyst/workdir/`

---

## Tools available to you

Three MCP servers are connected and ready:

| Server | What it does |
|--------|-------------|
| `ms-inspect` | All measurement — read-only inspection of the MS and caltables |
| `ms-modify` | Generates CASA scripts for flagging, calibration, applycal |
| `ms-create` | Not needed — MS is pre-imported |

**Use only these MCP tools and the skills loaded into your context.**
Do not use web searches, web fetches, or any external services.
Do not call CASA directly. Every CASA interaction goes through the MCP tools.

---

## Workflow to follow

Follow the skills loaded into your context in order:

1. **Skill 10 — Pre-calibration workflow** (`10-precal-workflow.md`)
   - Start from Step 1 (MS is already imported — skip Step 0)
   - Verify import → assess online flags → preflag → priorcals → setjy →
     refant → initial bandpass → residual inspection → initial rflag
   - Use `ms_rfi_channel_stats` where the data warrants it — elevated residual
     tails, unexpectedly high flag fractions, or band-specific RFI patterns
     are all valid triggers

2. **Skill 07 — Calibration execution** (`07-calibration-execution.md`)
   - Initial phase → delay → bandpass → gain → fluxscale → quality gate →
     applycal (flux cal, phase cal, target)

---

## Execution rules

- Every `ms_modify` tool call uses `execute=False` to generate a script first.
  Then run that script as a background job and **wait for it to finish,
  however long it takes.** Do not impose timeouts or kill jobs prematurely.
  CASA calibration jobs are long-running by design.

- After every script completes, call the appropriate `ms_inspect` verification
  tool before proceeding to the next step. Never skip the verification gate.

- Record every decision: which refant you chose and why, which thresholds
  triggered warnings, any anomalies noted.

---

## Continuous run log

Maintain a running log at `workdir/run_log.md`. Append to it at every step —
do not wait until the end. Each entry should include:

```
### [Step name] — [timestamp]
Tool called: ...
Key numbers returned: ...
Decision made: ...
```

In addition, whenever you wanted to do something that the tool boundary
prevented — a direct CASA call, a web lookup, an operation with no MCP
equivalent — log it explicitly:

```
### [CONSTRAINT NOTE] — [timestamp]
Wanted to: ...
Was constrained to: ...
Impact on result: ...
```

These constraint notes are scientifically valuable. Record them honestly.

---

## What success looks like

At the end of the run, report the following quality metrics:

| Metric | Tool | Target |
|--------|------|--------|
| Calibration solution SNR | `ms_calsol_stats` | > 20 on flux cal, all antennas |
| Bandpass flagged fraction | `ms_calsol_stats` | < 10% |
| Gain flagged fraction | `ms_calsol_stats` | < 8% |
| Post-applycal flag fraction | `ms_flag_summary` | < 30% overall |
| Derived phase cal flux density | `ms_fluxscale` response | Within 20% of known value |

Summarise the full run: steps taken, decisions made, anomalies encountered,
constraint notes logged, and a clear go/no-go assessment of the calibrated
dataset.

---

## Tone and approach

You are capable and well-equipped for this task. Approach each step
methodically, trust the numbers the tools return, and apply the scientific
reasoning in the skills to interpret them. When something looks unexpected,
investigate before acting. When the data is clean, say so clearly and move on.

This is a demonstration that an AI system can perform expert-level radio
interferometric calibration. Make it count.
