#!/usr/bin/env python3
"""Generate a minimal synthetic MS for CI smoke tests.

3 antennas, 1 SPW, 1 field, ~2 minutes of data — big enough to be a real MS,
small enough to build and read in a few seconds with no bundled test data.
"""
import os
import shutil
import sys

from casatools import componentlist, ctsys, measures, quanta, simulator, table

msname = sys.argv[1] if len(sys.argv) > 1 else "smoke.ms"

sm = simulator()
tb = table()
cl = componentlist()
me = measures()
qa = quanta()

if os.path.exists(msname):
    shutil.rmtree(msname)

pos = me.observatory("VLA")

# Real shipped VLA-D config, first 3 antennas — a hand-rolled local-coordinate
# array produced an MS with empty SPECTRAL_WINDOW/MAIN tables; the shipped
# global/ITRF config is the path CASA's simulator actually exercises in tests.
cfg_file = os.path.join(ctsys.resolve("alma/simmos"), "vla.d.cfg")
x, y, z, diam, antnames = [], [], [], [], []
with open(cfg_file) as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        x.append(float(parts[0]))
        y.append(float(parts[1]))
        z.append(float(parts[2]))
        diam.append(float(parts[3]))
        antnames.append(parts[4] if len(parts) > 4 else f"A{len(x) - 1:02d}")
        if len(x) == 3:
            break

sm.open(ms=msname)
sm.setconfig(
    telescopename="VLA",
    x=x, y=y, z=z,
    dishdiameter=diam,
    mount=["ALT-AZ"] * len(x),
    antname=antnames,
    coordsystem="global",
    referencelocation=pos,
)
sm.setspwindow(
    spwname="spw0",
    freq="1.4GHz",
    deltafreq="1MHz",
    freqresolution="1MHz",
    nchannels=8,
    stokes="RR RL LR LL",
)
sm.setfeed(mode="perfect R L", pol=[""])
sm.setfield(
    sourcename="smoke_target",
    sourcedirection=me.direction("J2000", "13h31m08.3s", "+30d30m33s"),
)
sm.setlimits(shadowlimit=0.001, elevationlimit="8deg")
sm.setauto(autocorrwt=0.0)
sm.settimes(
    integrationtime="10s",
    usehourangle=True,
    referencetime=me.epoch("UTC", "2026-01-01/00:00:00"),
)
sm.observe("smoke_target", "spw0", starttime="-1min", stoptime="+1min")
sm.close()

clname = os.path.abspath(msname.rstrip("/") + ".cl")
if os.path.exists(clname):
    shutil.rmtree(clname)
cl.done()
cl.addcomponent(
    dir=me.direction("J2000", "13h31m08.3s", "+30d30m33s"),
    flux=1.0,
    fluxunit="Jy",
    freq="1.4GHz",
    shape="point",
)
cl.rename(clname)
cl.close()

# sm.open() always (re)creates the MS from scratch — reopening an already
# -observed MS with it truncates SPECTRAL_WINDOW/MAIN back to empty.
# openfromms() is the call for operating on an existing MS (predict/corrupt).
sm.openfromms(msname)
sm.setvp()
sm.predict(complist=clname)
sm.close()

tb.open(msname, nomodify=False)
tb.putcol("DATA", tb.getcol("MODEL_DATA"))
tb.close()

print(f"smoke MS written: {msname}")
