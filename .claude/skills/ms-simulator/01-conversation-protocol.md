# 01 — Conversation Protocol

How to gather simulation parameters from the user and resolve ambiguity.

## Required vs optional parameters

**Must be known before generating code** (ask if not stated or inferable):

| Parameter | Example user input | What you need |
|-----------|--------------------|---------------|
| Telescope / array | "VLA", "MeerKAT", "custom 10-element array" | Name or antenna positions |
| Array config | "B-config", "compact" | Config file or description |
| Observing band / frequency | "L-band", "1.4 GHz", "Band 3" | Center frequency |
| Target source(s) | "3C286", "a 1 Jy point source at J2000 12h30m +40d" | Name or coordinates + flux |

**Default if not stated** (do not ask — use the default and mention it):

| Parameter | Default | Rationale |
|-----------|---------|-----------|
| Integration time | 10s | Typical VLA/MeerKAT correlator dump |
| Total duration | 1h | Enough for reasonable UV coverage |
| Number of channels | 64 | Light enough for quick simulation |
| Channel width | 1 MHz | Standard continuum-like channel |
| Polarization | `RR LL` (VLA/uGMRT) or `XX YY` (MeerKAT) | Telescope convention |
| Hour angle range | -0.5h to +0.5h (for 1h) | Symmetric about transit |
| Noise | simplenoise 0.1 Jy | Quick, physically plausible |
| Calibration errors | None | Clean simulation unless requested |
| Output path | `/tmp/sim_<telescope>_<band>.ms` | Temporary, easy to find |

## Elicitation strategy

1. **Parse first.** Extract everything you can from the user's message before
   asking anything. Users often pack multiple parameters into one sentence.

2. **Confirm, don't interrogate.** If you can infer most parameters, present
   your plan as a summary and ask "Does this look right?" rather than asking
   one question at a time.

3. **Band implies frequency.** If the user says "L-band", you know 1-2 GHz.
   Pick the band center (1.5 GHz for VLA L-band). See 02-antenna-configs.md
   for telescope-specific band tables.

4. **Calibrator names imply coordinates.** "3C286" → J2000 13h31m08.3s +30d30m33s,
   known flux density. Use the catalogued position; do not ask for coordinates.

5. **"Simple" or "basic" means minimal.** One field, one SPW, point source,
   no corruption. Get the MS created fast.

6. **"Realistic" means corruption.** Add thermal noise at a physically
   motivated level (tsys-atm mode) plus modest gain errors.

## Parameter summary template

Before generating code, present this to the user:

```
Simulation plan:
  Telescope:      VLA (B-config)
  Frequency:      1.5 GHz (L-band), 64 ch x 1 MHz
  Polarization:   RR LL
  Field(s):       3C286 (J2000 13h31m08.3s +30d30m33s)
  Duration:       1h (HA -0.5h to +0.5h)
  Integration:    10s
  Sky model:      Point source, 14.9 Jy (L-band PB2017)
  Noise:          simplenoise 0.1 Jy
  Corruption:     None
  Output:         /tmp/sim_vla_lband.ms
```

Wait for confirmation before executing. If the user says "just do it" or
similar, skip the confirmation step.

## Multiple fields / scans

If the user describes a typical VLA schedule (flux cal + phase cal + target):

- Create all three fields with `sm.setfield`
- Call `sm.observe` for each scan segment with appropriate HA ranges
- Typical schedule: flux cal 5 min → target 20 min → phase cal 2 min → repeat
- Assign correct intents if the user mentions them (CALIBRATE_FLUX, etc.)

## When to push back

- **Unreasonable parameters**: 10000 channels with 1s integration for 12 hours
  will produce a huge MS. Warn about size and suggest alternatives.
- **Contradictory inputs**: "VLA D-config with 30 km baselines" — explain why
  this is inconsistent and ask which they want.
- **Missing telescope for custom array**: If the user wants a custom array,
  you need either antenna positions or a description (N antennas, max baseline,
  arrangement pattern) to generate positions.
