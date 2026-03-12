# 04 — Noise and Calibration Error Models

## Thermal noise

### Simple noise (flat per-visibility sigma)

Fast, good for quick tests. Adds Gaussian noise with constant sigma to all
visibilities regardless of baseline or frequency.

```python
sm.open(ms=msname)
sm.setnoise(mode="simplenoise", simplenoise="0.1Jy")
sm.corrupt()
sm.close()
```

**Choosing a noise level:** For a rough estimate, use the VLA radiometer:
`sigma = SEFD / sqrt(2 * delta_nu * t_int)` where SEFD depends on band.

| Telescope | Band | Approx SEFD (Jy) |
|-----------|------|------------------|
| VLA | L | 420 |
| VLA | S | 370 |
| VLA | C | 310 |
| VLA | X | 250 |
| MeerKAT | L | 420 |
| MeerKAT | UHF | 830 |
| uGMRT | Band-5 | 500 |
| uGMRT | Band-4 | 670 |

### Physically-motivated noise (tsys-atm)

Uses atmospheric model for realistic frequency-dependent noise. Accounts for
dish size, antenna/spillover/correlator efficiency, receiver temperature.

```python
sm.open(ms=msname)
sm.setnoise(
    mode="tsys-atm",
    pwv=5.0,              # precipitable water vapour in mm
    tatmos=250.0,         # atmospheric temperature K
    tground=270.0,        # ground temperature K
    altitude=2124.0,      # observatory altitude in m (VLA = 2124)
    antefficiency=0.8,
    spillefficiency=0.85,
    correfficiency=0.88,
    trx=30.0,             # receiver temperature K
)
sm.corrupt()
sm.close()
```

**Observatory altitudes:**
- VLA: 2124 m
- MeerKAT: 1054 m
- uGMRT: 588 m

**Typical Trx values:**
- VLA L-band: 30 K
- VLA S-band: 25 K
- VLA C/X-band: 20 K
- MeerKAT L-band: 18 K
- uGMRT Band-5: 50 K

### Heterogeneous array noise

For arrays with mixed dish sizes, `sm.setnoise` applies uniform noise per
visibility. Instead, inject noise per-baseline scaled by dish area:

```python
from casatools import ms as mstool
import numpy as np

myms = mstool()
myms.open(msname, nomodify=False)
myms.msselect({"baseline": "***"})

# Reference noise for D_ref x D_ref baseline
sigma_ref = 0.1  # Jy
D_ref = 25.0     # m

myms.iterinit(columns=["ANTENNA1", "ANTENNA2"])
myms.iterorigin()
while True:
    rec = myms.getdata(["data", "antenna1", "antenna2"])
    a1, a2 = rec["antenna1"][0], rec["antenna2"][0]
    D1, D2 = dish_diameters[a1], dish_diameters[a2]
    scale = (D_ref * D_ref) / (D1 * D2)
    noise = sigma_ref * scale * (
        np.random.normal(size=rec["data"].shape)
        + 1j * np.random.normal(size=rec["data"].shape)
    ) / np.sqrt(2)
    rec["data"] += noise
    myms.putdata(rec)
    if not myms.iternext():
        break
myms.close()
```

## Calibration errors (optional)

Only add these when the user requests a "realistic" simulation or explicitly
asks for calibration effects.

### Time-variable gains

Simulates slow antenna-based gain drift (amplitude and phase).

```python
sm.open(ms=msname)
sm.setgain(
    mode="fbm",           # fractional Brownian motion
    table="",             # no cal table output
    amplitude=[0.05],     # 5% amplitude variation
    # phase variation added automatically
)
sm.corrupt()
sm.close()
```

### Bandpass errors

Frequency-dependent gain variations.

```python
sm.setbandpass(
    mode="calculate",
    table="",
    amplitude=[0.05, 0.02],  # [mean, variation] per channel
)
```

### Polarization leakage (D-terms)

Adds instrumental polarization. Only relevant for full-pol simulations.

```python
sm.setleakage(
    mode="constant",
    table="",
    amplitude=[0.02],     # 2% leakage
)
```

### Tropospheric phase

Adds a turbulent phase screen. Mostly relevant at high frequencies (>8 GHz).

```python
sm.settrop(
    mode="screen",
    table="",
    pwv=3.0,              # mm
    deltapwv=0.3,         # mm variation
    beta=1.1,             # Kolmogorov exponent
    windspeed=15.0,       # m/s
)
```

## Corruption order

When combining multiple corruption effects, the order matters:

1. `sm.setnoise(...)` — always first (adds to DATA directly)
2. `sm.setgain(...)` — antenna-based multiplicative
3. `sm.setbandpass(...)` — frequency-dependent multiplicative
4. `sm.setleakage(...)` — polarization mixing
5. `sm.settrop(...)` — phase-only, additive in phase
6. `sm.corrupt()` — applies ALL configured effects in one call

Only call `sm.corrupt()` once after configuring all desired effects.

## Quick presets

Use these when the user asks for a certain "level" of realism:

**Clean (no corruption):** Just stages 1-3, no noise or errors.

**Simple noise:** Stage 4 with `simplenoise`. Good default for testing.

**Realistic:** `tsys-atm` noise + 5% gain drift. No leakage or troposphere.

**Full corruption:** All effects. Leakage at 2%, troposphere at 3mm PWV,
gain drift at 5%, bandpass ripple at 2%. Only for polarization or
calibration-testing simulations.
