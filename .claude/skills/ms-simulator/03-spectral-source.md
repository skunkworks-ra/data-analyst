# 03 — Spectral Setup and Source Models

## Spectral window configuration

```python
sm.setspwindow(
    spwname="spw0",
    freq="1.5GHz",          # center frequency of first channel
    deltafreq="1MHz",       # channel width
    freqresolution="1MHz",  # frequency resolution (usually = deltafreq)
    nchannels=64,           # number of channels
    stokes="RR LL",         # polarization products
)
```

### Multiple spectral windows

Call `sm.setspwindow` once per SPW with a unique `spwname`. Then call
`sm.observe` for each SPW (or once if observing all SPWs simultaneously).

```python
sm.setspwindow(spwname="spw0", freq="1.0GHz", deltafreq="1MHz",
               freqresolution="1MHz", nchannels=64, stokes="RR LL")
sm.setspwindow(spwname="spw1", freq="1.5GHz", deltafreq="1MHz",
               freqresolution="1MHz", nchannels=64, stokes="RR LL")
```

### Polarization products by telescope

| Telescope | Native basis | Stokes string |
|-----------|-------------|---------------|
| VLA | Circular | `"RR LL"` (dual) or `"RR RL LR LL"` (full) |
| MeerKAT | Linear | `"XX YY"` (dual) or `"XX XY YX YY"` (full) |
| uGMRT | Circular | `"RR LL"` (dual) or `"RR RL LR LL"` (full) |

Use full-pol only if user requests polarization calibration or leakage simulation.

## Source models

Three approaches, from simplest to most flexible:

### 1. Component list (point sources, Gaussians)

Best for simple sky models — a few discrete sources.

```python
from casatools import componentlist
cl = componentlist()

# Point source
cl.addcomponent(
    dir="J2000 13h31m08.3s +30d30m33s",
    flux=14.9, fluxunit="Jy",
    freq="1.5GHz",
    shape="point",
    spectrumtype="spectral index",
    index=-0.46,
)

# Gaussian source
cl.addcomponent(
    dir="J2000 12h30m00s +40d00m00s",
    flux=1.0, fluxunit="Jy",
    freq="1.5GHz",
    shape="Gaussian",
    majoraxis="10arcsec",
    minoraxis="5arcsec",
    positionangle="45deg",
    spectrumtype="spectral index",
    index=-0.7,
)

cl.rename("/tmp/sky_model.cl")
cl.close()
```

### 2. Image model (arbitrary sky distribution)

For extended emission, complex morphology, or spectral cubes.

```python
from casatools import image, coordsys, componentlist
ia = image()
cs = coordsys()

# Create a 4D image: [RA, Dec, Stokes, Freq]
cs.setdirection(refcode="J2000",
                refval="13h31m08.3s +30d30m33s",
                incr="-1arcsec 1arcsec")
cs.setstokes("I")  # or "RR LL" etc
cs.setspectral(refcode="LSRK", restfreq="1.5GHz",
               refval="1.5GHz", incr="1MHz")

ia.fromshape("/tmp/sky_model.im", shape=[256, 256, 1, 64],
             csys=cs.torecord(), overwrite=True)
ia.set(0.0)  # zero the image

# Stamp components onto pixels
cl = componentlist()
cl.addcomponent(dir="J2000 13h31m08.3s +30d30m33s",
                flux=1.0, fluxunit="Jy", freq="1.5GHz",
                shape="point")
ia.modify(cl.torecord(), subtract=False)
cl.close()

ia.close()
cs.done()
```

### 3. Direct pixel manipulation

For injecting custom patterns (RFI-like signals, specific UV-plane features).

```python
ia.open("/tmp/sky_model.im")
pixels = ia.getchunk()  # numpy array [RA, Dec, Stokes, Freq]
# ... modify pixels ...
ia.putchunk(pixels)
ia.close()
```

## Predicting visibilities

After creating the sky model, predict into the MS:

### Method A: sm.predict (simple, handles component lists directly)
```python
sm.open(ms=msname)
sm.predict(imagename="", complist="/tmp/sky_model.cl")
sm.close()
```

### Method B: ft task (DFT, more accurate for point sources)
```python
from casatasks import ft
ft(vis=msname, complist="/tmp/sky_model.cl", usescratch=True)
```

### Method C: tclean for prediction (required for mosaic/heterogeneous)
```python
from casatasks import tclean
tclean(vis=msname, startmodel="/tmp/sky_model.im",
       savemodel="modelcolumn", niter=0, calcres=False,
       gridder="mosaic",  # for heterogeneous arrays
       imagename="/tmp/sim_predict")
```

### Copying MODEL_DATA to DATA

After prediction, MODEL_DATA contains the noiseless visibilities. Copy to DATA
before adding noise:

```python
from casatools import table
tb = table()
tb.open(msname, nomodify=False)
tb.putcol("DATA", tb.getcol("MODEL_DATA"))
tb.close()
```

## Common calibrator models

| Source | RA (J2000) | Dec (J2000) | L-band flux (Jy) | Spectral index |
|--------|-----------|-------------|-------------------|----------------|
| 3C286 | 13h31m08.3s | +30d30m33s | ~14.9 | -0.46 |
| 3C48 | 01h37m41.3s | +33d09m35s | ~15.8 | -0.76 |
| 3C138 | 05h21m09.9s | +16d38m22s | ~8.3 | -0.50 |
| 3C147 | 05h42m36.1s | +49d51m07s | ~21.8 | -0.69 |

Fluxes are approximate L-band values (Perley-Butler 2017 scale). For other
bands, scale using `S = S_ref * (nu/nu_ref)^alpha`.
