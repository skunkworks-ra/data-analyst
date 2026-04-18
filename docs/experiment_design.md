# Experiment Design — Claude as Radio Interferometrist

## Goal

Demonstrate that Claude can learn the knowledge a human radio interferometrist
holds and apply it to real observational data, producing calibration output of
comparable quality. This document is the scientific protocol for that experiment.

---

## Hypothesis

A large language model given (a) structured inspection tools, (b) a curated
scientific reasoning skill, and (c) the ability to execute and monitor long-running
CASA jobs can produce a calibrated Measurement Set indistinguishable from one
produced by an expert human operator — at measurable, reproducible cost.

---

## Dataset

**3C391 CTM mosaic, 10s integration, SPW 0**
- Source: standard VLA calibration benchmark, well-characterised at C-band
- Path: `/home/pjaganna/Data/measurement_sets/3c391_ctm_mosaic_10s_spw0.ms.tgz`
- Why this dataset: known calibration solution, published NRAO guides exist,
  human-quality baseline is established

---

## Experimental parameters

### Independent variables (what we vary)

| Parameter | Values | Why it matters |
|-----------|--------|----------------|
| Model | `claude-haiku-4-5`, `claude-sonnet-4-6`, `claude-opus-4-7` | Cost-quality trade-off across capability tiers |
| Extended thinking budget | 0 (off), 5k, 16k tokens | Does deeper reasoning improve calibration decisions? |

### Instrumented measurements (what we record per run)

| Measurement | Where captured | Notes |
|-------------|---------------|-------|
| Input tokens per MCP tool call | API `usage.input_tokens` per turn | Includes accumulated context + tool result |
| Output tokens per MCP tool call | API `usage.output_tokens` per turn | Model's reasoning + next action |
| Cache read tokens per turn | API `usage.cache_read_input_tokens` | Measures prompt caching effectiveness |
| Total input tokens (full run) | Sum across all turns | End-to-end cost of one calibration |
| Total output tokens (full run) | Sum across all turns | |
| Wall-clock time per step | `time.time()` around each turn | Practical feasibility |
| Number of turns to completion | Turn counter | Efficiency of the reasoning loop |
| MCP tool calls per turn | Parse tool use blocks | How many tools per reasoning step |

### Output quality metrics (what we evaluate)

| Metric | How measured | Human-quality threshold |
|--------|-------------|------------------------|
| Calibration solution SNR | `ms_calsol_stats` — per-(antenna, SPW) SNR | > 20 for all antennas on flux cal |
| Flagged fraction post-preflag | `ms_flag_summary` | < 10% on calibrators |
| Residual amplitude RMS | `ms_residual_stats` | Consistent with thermal noise |
| Refant selection | `ms_refant` rank vs human choice | Top-3 agreement |
| Script correctness | Review generated CASA scripts | No parameter errors |

---

## Protocol

### Instrument

Run each experiment via the Anthropic API (Python SDK), not Claude Code CLI.
This gives access to `usage` fields per turn for token accounting.

Each run:
1. Provide system prompt (skill content) + initial user message (MS path + task)
2. Run the calibration loop: model calls MCP tools, executes jobs, inspects results
3. Record `usage` on every API response
4. Parse tool use blocks to attribute tokens to specific MCP calls
5. At completion, run quality metrics and record

### Control condition

Human operator runs the standard 3C391 NRAO guide reduction on the same dataset.
Record wall-clock time and final quality metrics as the baseline.

### Execution

- Runs are sequential, not parallel (CASA table locks)
- Each run starts from a fresh extraction of the `.ms.tgz` tarball
- Environment: same pixi environment, same host, same CASA version

---

## MCP server role

The three servers define the interface boundary:

| Server | Port | Role in experiment |
|--------|------|--------------------|
| `ms-inspect` | 8000 | All measurement — read only, never writes |
| `ms-modify` | 8001 | Generates CASA scripts; model executes them as background jobs |
| `ms-create` | 8002 | Not used (starting from pre-imported MS) |

The model must not call CASA directly. All CASA interaction goes through
the MCP tools. This is both the experimental constraint and the production design.

---

## Open questions

1. **CLI connection errors** — 3 issues reported on session start; need to
   diagnose before running the instrumented experiment.
2. **Skill edits** — current skills assume human executes scripts; need to
   update workflow files to reflect model-driven background execution.
3. **Token attribution** — MCP tool results arrive as user turns in the API;
   need to verify token counting correctly separates tool results from
   model reasoning.
4. **Long-running job handling** — for datasets larger than 3C391, background
   job monitoring strategy needs to be defined (polling interval, timeout).
