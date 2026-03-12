# 02 — Antenna Configurations

Telescope-specific configuration data for MS simulation.

## Shipped CASA antenna config files

CASA ships antenna configuration files. Locate them at runtime:

```python
import casatools
import os
datapath = os.path.join(casatools.casadata.datapath, "alma", "simmos")
# Files: vla.a.cfg, vla.b.cfg, vla.c.cfg, vla.d.cfg, meerkat.cfg, etc.
```

To use a shipped config with `sm.setconfig`, read the file and pass X/Y/Z
positions directly. The config files have columns: `X Y Z diameter mount name`.

## Loading a config file

```python
import numpy as np

def load_antenna_config(filepath):
    """Parse a CASA simmos antenna config file."""
    x, y, z, diam, names = [], [], [], [], []
    coordsys = "global"  # default
    observatory = "VLA"
    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if line.startswith("# observatory="):
                observatory = line.split("=")[1].strip()
            elif line.startswith("# coordsys="):
                coordsys = line.split("=")[1].strip()
            elif line and not line.startswith("#"):
                parts = line.split()
                x.append(float(parts[0]))
                y.append(float(parts[1]))
                z.append(float(parts[2]))
                diam.append(float(parts[3]))
                names.append(parts[4] if len(parts) > 4 else f"A{len(names):02d}")
    return {
        "x": np.array(x), "y": np.array(y), "z": np.array(z),
        "diam": np.array(diam), "names": names,
        "coordsys": coordsys, "observatory": observatory,
    }
```

## VLA configurations

| Config | Max baseline | N antennas | Typical use |
|--------|-------------|------------|-------------|
| D | 1.03 km | 27 | Low-res surveys, extended emission |
| C | 3.4 km | 27 | Moderate resolution |
| B | 11.1 km | 27 | High resolution continuum |
| A | 36.4 km | 27 | Highest resolution |

Config files: `vla.a.cfg`, `vla.b.cfg`, `vla.c.cfg`, `vla.d.cfg`
Mount: `ALT-AZ`. Dish diameter: 25 m. Feed: `perfect R L`.
Observatory location: VLA (looked up by CASA from `me.observatory('VLA')`).

### VLA band table

| Band | Freq range | Typical center | Default channels |
|------|-----------|----------------|------------------|
| P | 230-470 MHz | 350 MHz | 64 x 1 MHz |
| L | 1-2 GHz | 1.5 GHz | 64 x 1 MHz |
| S | 2-4 GHz | 3.0 GHz | 64 x 2 MHz |
| C | 4-8 GHz | 6.0 GHz | 64 x 2 MHz |
| X | 8-12 GHz | 10.0 GHz | 64 x 2 MHz |
| Ku | 12-18 GHz | 15.0 GHz | 64 x 2 MHz |
| K | 18-26.5 GHz | 22.0 GHz | 64 x 2 MHz |
| Ka | 26.5-40 GHz | 33.0 GHz | 64 x 2 MHz |
| Q | 40-50 GHz | 45.0 GHz | 64 x 2 MHz |

## MeerKAT

Config file: `meerkat.cfg`. N antennas: 64. Dish: 13.5 m. Mount: `ALT-AZ`.
Feed: `perfect X Y`. Observatory: `MeerKAT`.

| Band | Freq range | Typical center |
|------|-----------|----------------|
| UHF | 580-1015 MHz | 800 MHz |
| L | 900-1670 MHz | 1284 MHz |
| S | 1750-3500 MHz | 2625 MHz |

## uGMRT

Config file: `gmrt.cfg` (if available, else provide positions manually).
N antennas: 30. Dish: 45 m. Mount: `ALT-AZ`. Feed: `perfect R L`.
Observatory: `GMRT`.

| Band | Freq range | Typical center |
|------|-----------|----------------|
| Band-2 | 120-250 MHz | 185 MHz |
| Band-3 | 250-500 MHz | 375 MHz |
| Band-4 | 550-850 MHz | 700 MHz |
| Band-5 | 1000-1460 MHz | 1260 MHz |

## Custom / synthetic arrays

If the user describes a custom array, generate positions:

```python
import numpy as np

def make_y_array(n_antennas, max_baseline_m):
    """Generate a Y-shaped array (VLA-like) with given parameters."""
    n_per_arm = n_antennas // 3
    positions = []
    for arm_angle in [0, 120, 240]:
        angle_rad = np.radians(arm_angle)
        for i in range(n_per_arm):
            r = max_baseline_m * (i + 1) / (2 * n_per_arm)
            positions.append([r * np.sin(angle_rad), r * np.cos(angle_rad), 0.0])
    return np.array(positions)
```

For random arrays, use `np.random.uniform` within a circle of radius
`max_baseline / 2`. Always include a few short baselines (compact core).

## Heterogeneous arrays

For mixed dish sizes (e.g., ALMA 12m + 7m, or ngVLA 18m + 6m):
- Pass different diameters in the `dishdiameter` array to `sm.setconfig`
- Noise must be scaled per baseline type: `sigma ∝ 1/(D1 * D2)`
- Primary beam correction requires `gridder='mosaic'` in tclean predictions
- See 04-corruption-noise.md for dish-dependent noise injection
