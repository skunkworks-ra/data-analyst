# 00 — Core: Bands, Intents, Calibrator Roles

## VLA / EVLA band table

| Band | Frequency | Flux standard |
|------|-----------|--------------|
| P | 230–470 MHz | Scaife-Heald 2012 |
| L | 1–2 GHz | Perley-Butler 2017 |
| S | 2–4 GHz | Perley-Butler 2017 |
| C | 4–8 GHz | Perley-Butler 2017 |
| X | 8–12 GHz | Perley-Butler 2017 |
| Ku | 12–18 GHz | Perley-Butler 2017 |
| K | 18–26.5 GHz | Perley-Butler 2017 |
| Ka | 26.5–40 GHz | Perley-Butler 2017 |
| Q | 40–50 GHz | Perley-Butler 2017 |

## MeerKAT band table

| Band | Frequency | Flux standard |
|------|-----------|--------------|
| UHF | 544–1088 MHz | Reynolds 1994 (PKS1934-638) |
| L | 856–1712 MHz | Reynolds 1994 (PKS1934-638) |
| S | 1.75–3.5 GHz | Reynolds 1994 or Stevens 2004 (PKS0408-65) |

## uGMRT band table

| Band | Frequency |
|------|-----------|
| Band 1 | 120–250 MHz |
| Band 2 | 250–500 MHz |
| Band 3 | 550–750 MHz |
| Band 4 | 700–950 MHz |
| Band 5 | 1050–1450 MHz |

## Scan intent vocabulary

| Intent string | Role |
|---------------|------|
| `CALIBRATE_FLUX#ON_SOURCE` | Flux/amplitude calibrator |
| `CALIBRATE_BANDPASS#ON_SOURCE` | Bandpass calibrator (often same field as flux cal) |
| `CALIBRATE_PHASE#ON_SOURCE` | Phase / complex gain calibrator |
| `CALIBRATE_DELAY#ON_SOURCE` | Delay calibrator |
| `CALIBRATE_POLARIZATION#ON_SOURCE` | D-term leakage calibrator |
| `CALIBRATE_POL_ANGLE#ON_SOURCE` | Absolute position angle calibrator |
| `OBSERVE_TARGET#ON_SOURCE` | Science target |
| `SYSTEM_CONFIGURATION` | Slew / setup / dummy — exclude from calibration |
| `UNSPECIFIED` | No intent — treat as unknown; infer from field name |

If `heuristic_intents==true`: intents were inferred from field names. Treat as `INFERRED` quality.

## Calibrator role taxonomy

| Role | Minimum viable | Used for | VLA examples |
|------|---------------|----------|-------------|
| Flux/bandpass cal | 1 required | Absolute flux scale + bandpass shape | 3C286, 3C48, 3C147 |
| Phase cal | 1 per science target | Complex gain (phase + amp) | J-name sources within ~15° of target |
| Polcal (leakage) | Optional | D-term leakage | 3C286, 3C138, phase cal with PA coverage |
| Polcal (angle) | Optional | Absolute EVPA | 3C286 (first choice), 3C138, 3C48 at S-band |

### Resolved calibrators — special handling required

| Source | Config where resolved | Action |
|--------|----------------------|--------|
| CasA | All VLA configs above D | Use `CasA_Epoch2010.0` component model; apply 0.6%/yr secular decline |
| CygA | Above ~50 kλ at L-band | Use `3C405_CygA` component model |
| TauA | B-config and above | Mostly useful at D-config or low frequency |
| VirA | B-config and above at L | Component model required |

If `ms_field_list` issues `CALIBRATOR_RESOLVED_WARNING`: do NOT use point source model. Use the component model named in the warning.

## PKS1934-638 (MeerKAT)

Unpolarised to < 0.2%. Do NOT use for polarisation angle calibration.

## Completeness flag routing

| Flag | Use value? |
|------|-----------|
| `COMPLETE` | Yes, directly |
| `INFERRED` | Yes, with noted confidence |
| `PARTIAL` | Yes, with noted limitation |
| `SUSPECT` | No — block all downstream computations depending on it |
| `UNAVAILABLE` | No — state explicitly; do not substitute a guess |
