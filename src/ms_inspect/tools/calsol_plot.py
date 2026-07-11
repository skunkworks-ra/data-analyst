"""
calsol_plot.py — ms_calsol_plot

Visual QA of a single CASA calibration table. Reads the caltable's OWN columns
directly (CPARAM/FPARAM, FLAG, ANTENNA1, SPECTRAL_WINDOW_ID, TIME) plus CHAN_FREQ
from the SPECTRAL_WINDOW subtable — it does NOT call ms_calsol_stats. The plot is
the raw solutions, not a reduction.

One self-contained Bokeh HTML per caltable. Series are pre-rendered into the page;
a slider switches which is shown via CustomJS (static HTML, no server). Traces are
coloured by correlation (R/L or X/Y). A header carries the caltable name, the
associated MS, and the (non-default) calibration call parameters parsed from the
generated script. An optional per-antenna metadata label can be toggled.

Axis rule: if more than two axes vary, SPW gets its own slider; otherwise SPW
folds onto a single plot. For frequency views (B/Df/Xf) SPW folds onto a
concatenated frequency x-axis, so only an antenna slider is needed.

View routed by VisCal type:
    B            → amp + phase vs frequency (all SPW concatenated) · antenna slider
    Df           → amplitude vs frequency   (all SPW concatenated) · antenna slider
    Xf           → phase vs frequency       (all SPW concatenated) · antenna slider
    G            → amp + phase vs time        · antenna + SPW sliders
    K / Kcross   → delay vs antenna           · SPW slider
"""

from __future__ import annotations

import ast
import contextlib
import math
from pathlib import Path

import numpy as np

from ms_inspect.util.casa_context import open_table
from ms_inspect.util.formatting import field as fmt_field
from ms_inspect.util.formatting import response_envelope

TOOL_NAME = "ms_calsol_plot"

_CORR_COLORS = ["#1f77b4", "#d62728", "#2ca02c", "#9467bd"]

# Salient calibration-call knobs to surface in the header (if present in the call).
_SALIENT_PARAMS = (
    "field",
    "solint",
    "combine",
    "refant",
    "minsnr",
    "minblperant",
    "bandtype",
    "gaintype",
    "poltype",
    "solnorm",
    "parang",
    "smodel",
)


# ---------------------------------------------------------------------------
# VisCal classification
# ---------------------------------------------------------------------------


def _viscal(tb) -> str:
    try:
        vc = str(tb.getkeyword("VisCal")).strip()
    except Exception:
        return ""
    return vc.split()[0] if vc else ""


def _is_delay(vc: str) -> bool:
    return vc.upper().startswith("K") or vc.lower() == "kcross"


def _is_freq_dep(vc: str) -> bool:
    return vc in ("B", "Df", "Xf", "Bf") or vc.startswith("BPOLY")


# ---------------------------------------------------------------------------
# Subtable + provenance readers
# ---------------------------------------------------------------------------


def _ant_names(caltable: str) -> list[str]:
    with open_table(str(Path(caltable) / "ANTENNA")) as tb:
        return [str(x) for x in tb.getcol("NAME")]


def _field_names(caltable: str) -> dict[int, str]:
    with open_table(str(Path(caltable) / "FIELD")) as tb:
        return {i: str(n) for i, n in enumerate(tb.getcol("NAME"))}


def _spw_chan_freqs_ghz(caltable: str) -> dict[int, list[float]]:
    out: dict[int, list[float]] = {}
    with open_table(str(Path(caltable) / "SPECTRAL_WINDOW")) as tb:
        for row in range(tb.nrows()):
            out[row] = (np.asarray(tb.getcell("CHAN_FREQ", row), dtype=float) / 1e9).tolist()
    return out


def _poln_labels(n_poln: int) -> list[str]:
    if n_poln == 2:
        return ["R", "L"]
    return [f"P{i}" for i in range(n_poln)]


def _call_provenance(caltable: str) -> dict:
    """
    MS name (from MSName keyword) + the calibration call params parsed from the
    generated script that produced this caltable, if it is alongside the table.
    """
    p = Path(caltable)
    prov: dict = {"caltable": p.name, "ms_name": None, "task": None, "params": {}}
    try:
        with open_table(caltable) as tb:
            prov["ms_name"] = str(tb.getkeyword("MSName"))
    except Exception:
        pass

    for script in sorted(p.parent.glob("*.py")):
        try:
            text = script.read_text()
        except Exception:
            continue
        if p.name not in text:
            continue
        try:
            tree = ast.parse(text)
        except Exception:
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            kw = {k.arg: k.value for k in node.keywords if k.arg}
            ct = kw.get("caltable")
            try:
                ct_val = ast.literal_eval(ct) if ct is not None else None
            except Exception:
                ct_val = None
            if ct_val is None or not str(ct_val).rstrip("/").endswith(p.name):
                continue
            prov["task"] = node.func.id
            params: dict = {}
            for key in _SALIENT_PARAMS:
                if key in kw:
                    with contextlib.suppress(Exception):
                        params[key] = ast.literal_eval(kw[key])
            if "gaintable" in kw:
                with contextlib.suppress(Exception):
                    params["n_priors"] = len(ast.literal_eval(kw["gaintable"]))
            prov["params"] = params
            return prov
    return prov


def _provenance_header(prov: dict, vc: str, stem: str) -> str:
    bits = [f"<b>caltable</b> {prov['caltable']}"]
    if prov.get("ms_name"):
        bits.append(f"<b>MS</b> {prov['ms_name']}")
    if prov.get("task"):
        pstr = ", ".join(f"{k}={v!r}" for k, v in prov["params"].items())
        bits.append(f"<b>{prov['task']}</b>({pstr})")
    return " &nbsp;|&nbsp; ".join(bits)


def _nan_to_none(arr) -> list:
    return [
        None if (v is None or (isinstance(v, float) and math.isnan(v))) else float(v)
        for v in np.asarray(arr, dtype=float)
    ]


def _cds_init(entry: dict) -> dict:
    """ColumnDataSource init: drop 'meta', map None→NaN so first render has gaps not zeros."""
    out = {}
    for k, v in entry.items():
        if k == "meta":
            continue
        if isinstance(v, list):
            out[k] = [float("nan") if x is None else x for x in v]
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------------------
# Caltable read
# ---------------------------------------------------------------------------


def _read_solutions(caltable: str) -> dict:
    ant_names = _ant_names(caltable)
    field_names = _field_names(caltable)
    chan_freqs = _spw_chan_freqs_ghz(caltable)
    with open_table(caltable) as tb:
        vc = _viscal(tb)
        is_delay = _is_delay(vc)
        col = "FPARAM" if is_delay else "CPARAM"
        param = tb.getcol(col)
        flag = tb.getcol("FLAG")
        ant1 = tb.getcol("ANTENNA1")
        spw = tb.getcol("SPECTRAL_WINDOW_ID")
        fld = tb.getcol("FIELD_ID")
        time = tb.getcol("TIME")
    n_poln = param.shape[0]
    rows = [
        {
            "ant": int(ant1[r]),
            "spw": int(spw[r]),
            "field": int(fld[r]),
            "time": float(time[r]),
            "param": param[:, :, r],
            "flag": flag[:, :, r],
        }
        for r in range(param.shape[2])
    ]
    spw_ids = sorted({r["spw"] for r in rows}, key=lambda s: np.mean(chan_freqs.get(s, [s])))
    return {
        "vc": vc,
        "is_delay": is_delay,
        "is_freq_dep": _is_freq_dep(vc),
        "ant_names": ant_names,
        "spw_ids": spw_ids,
        "field_names": field_names,
        "n_poln": n_poln,
        "poln_labels": _poln_labels(n_poln),
        "chan_freqs": chan_freqs,
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _freq_view(
    sol: dict, want_amp: bool, want_phase: bool, header: str, title: str, color_by_spw: bool = False
) -> object:
    """B/Df/Xf: all SPW concatenated on one frequency axis; antenna slider only.

    color_by_spw: colour the phase trace by SPW (Xf) so SPW breaks are obvious.
    """
    from bokeh.layouts import column
    from bokeh.models import (
        CheckboxGroup,
        ColorBar,
        ColumnDataSource,
        CustomJS,
        Div,
        LinearColorMapper,
        Slider,
    )
    from bokeh.palettes import Turbo256
    from bokeh.plotting import figure
    from bokeh.transform import transform

    ant_names, spw_ids = sol["ant_names"], sol["spw_ids"]
    labels, n_poln = sol["poln_labels"], sol["n_poln"]
    by_key = {}
    for r in sol["rows"]:
        by_key.setdefault((r["ant"], r["spw"]), r)

    store: dict[str, dict] = {}
    for ai in range(len(ant_names)):
        x: list = []
        spw_ax: list = []
        amp_p = {pi: [] for pi in range(n_poln)}
        pha_p = {pi: [] for pi in range(n_poln)}
        flagged_n = total_n = 0
        field = None
        for si, spw in enumerate(spw_ids):
            cf = sol["chan_freqs"].get(spw, [])
            r = by_key.get((ai, spw))
            if r is not None:
                f = r["flag"]
                flagged_n += int(f.sum())
                total_n += f.size
                field = sol["field_names"].get(r["field"], str(r["field"]))
                amp = np.where(f, np.nan, np.abs(r["param"]))
                pha = np.where(f, np.nan, np.angle(r["param"]) * 180.0 / math.pi)
            else:
                amp = np.full((n_poln, len(cf)), np.nan)
                pha = np.full((n_poln, len(cf)), np.nan)
            x.extend(cf)
            spw_ax.extend([spw] * len(cf))
            for pi in range(n_poln):
                amp_p[pi].extend(amp[pi].tolist())
                pha_p[pi].extend(pha[pi].tolist())
            if si < len(spw_ids) - 1:  # NaN break between SPWs
                x.append(cf[-1] if cf else None)
                spw_ax.append(spw)
                for pi in range(n_poln):
                    amp_p[pi].append(float("nan"))
                    pha_p[pi].append(float("nan"))
        entry = {"x": x, "spw": spw_ax}
        for pi in range(n_poln):
            entry[f"amp_p{pi}"] = _nan_to_none(amp_p[pi])
            entry[f"pha_p{pi}"] = _nan_to_none(pha_p[pi])
        ff = (flagged_n / total_n) if total_n else 0.0
        entry["meta"] = (
            f"<b>antenna</b> {ant_names[ai]} &nbsp;|&nbsp; <b>field</b> {field} &nbsp;|&nbsp; <b>flagged</b> {ff * 100:.1f}%"
        )
        store[f"a{ai}"] = entry

    src = ColumnDataSource(data=_cds_init(store["a0"]))
    figs = []
    if want_amp:
        fa = figure(
            width=1000,
            height=280,
            title="Amplitude",
            x_axis_label="Frequency (GHz)",
            y_axis_label="Amp",
            toolbar_location="right",
        )
        for pi in range(n_poln):
            c = _CORR_COLORS[pi % 4]
            fa.line(
                "x", f"amp_p{pi}", source=src, line_color=c, line_width=1.2, legend_label=labels[pi]
            )
            fa.scatter("x", f"amp_p{pi}", source=src, size=3, color=c, legend_label=labels[pi])
        fa.legend.location = "top_right"
        fa.legend.click_policy = "hide"
        figs.append(fa)
    if want_phase:
        fp = figure(
            width=1000,
            height=280,
            title="Phase",
            x_axis_label="Frequency (GHz)",
            y_axis_label="Phase (deg)",
            toolbar_location="right",
        )
        if color_by_spw:
            mapper = LinearColorMapper(palette=Turbo256, low=min(spw_ids), high=max(spw_ids))
            fp.line("x", "pha_p0", source=src, line_color="#cccccc", line_width=1)
            fp.scatter(
                "x",
                "pha_p0",
                source=src,
                size=5,
                line_color=None,
                fill_color=transform("spw", mapper),
            )
            fp.add_layout(ColorBar(color_mapper=mapper, title="SPW", width=10), "right")
        else:
            for pi in range(n_poln):
                c = _CORR_COLORS[pi % 4]
                fp.line(
                    "x",
                    f"pha_p{pi}",
                    source=src,
                    line_color=c,
                    line_width=1.2,
                    legend_label=labels[pi],
                )
                fp.scatter("x", f"pha_p{pi}", source=src, size=3, color=c, legend_label=labels[pi])
            fp.legend.location = "top_right"
            fp.legend.click_policy = "hide"
        figs.append(fp)

    ant_slider = Slider(
        start=0,
        end=len(ant_names) - 1,
        value=0,
        step=1,
        title=f"Antenna: {ant_names[0]}",
        width=1000,
    )
    chk = CheckboxGroup(labels=["metadata"], active=[0])
    meta_div = Div(text=store["a0"]["meta"], width=1000)
    cb = CustomJS(
        args=dict(
            src=src,
            store=store,
            antS=ant_slider,
            ants=ant_names,
            metaDiv=meta_div,
            chk=chk,
            npoln=n_poln,
        ),
        code="""
        const ai=antS.value; antS.title='Antenna: '+ants[ai];
        const d=store['a'+ai]; if(!d){return;}
        const fix=a=>a.map(v=>v===null?NaN:v);
        const nd={x:fix(d.x), spw:d.spw};
        for(let p=0;p<npoln;p++){nd['amp_p'+p]=fix(d['amp_p'+p]); nd['pha_p'+p]=fix(d['pha_p'+p]);}
        src.data=nd; src.change.emit();
        metaDiv.text = chk.active.includes(0)?d.meta:'';
        """,
    )
    ant_slider.js_on_change("value", cb)
    chk.js_on_change("active", cb)
    return column(
        Div(text=f"<h3 style='margin:2px'>{title}</h3>"),
        Div(text=f"<div style='font-size:90%'>{header}</div>", width=1000),
        ant_slider,
        chk,
        meta_div,
        *figs,
    )


_MARKERS = ["circle", "triangle", "square", "diamond"]


def _time_view(sol: dict, header: str, title: str) -> object:
    """G: amp + phase vs time; antenna + SPW sliders. Color = field, marker = correlation.

    Each field gets its own ColumnDataSource (a CDS requires equal-length columns,
    and the cal fields have different time samplings). A slider change swaps the
    data on every field's source.
    """
    from bokeh.layouts import column
    from bokeh.models import CheckboxGroup, ColumnDataSource, CustomJS, Div, Slider
    from bokeh.plotting import figure

    ant_names, spw_ids = sol["ant_names"], sol["spw_ids"]
    labels, n_poln = sol["poln_labels"], sol["n_poln"]
    fields = sorted({r["field"] for r in sol["rows"]})
    fname = {fi: sol["field_names"].get(fi, str(fi)) for fi in fields}

    grouped: dict = {}
    for r in sol["rows"]:
        grouped.setdefault((r["ant"], r["spw"], r["field"]), []).append(r)
    for v in grouped.values():
        v.sort(key=lambda r: r["time"])
    t0 = min((r["time"] for r in sol["rows"]), default=0.0)

    # store[key]["f{fi}"] = {x, amp_p{p}, pha_p{p}}; plus meta.
    store: dict[str, dict] = {}
    for ai in range(len(ant_names)):
        for spw in spw_ids:
            entry: dict = {}
            fcount = []
            for fi in fields:
                rs = grouped.get((ai, spw, fi), [])
                sub = {"x": [(r["time"] - t0) / 3600.0 for r in rs]}
                for pi in range(n_poln):
                    amp, pha = [], []
                    for r in rs:
                        fl = bool(r["flag"][pi, 0])
                        val = r["param"][pi, 0]
                        amp.append(np.nan if fl else float(abs(val)))
                        pha.append(np.nan if fl else float(np.angle(val) * 180.0 / math.pi))
                    sub[f"amp_p{pi}"] = _nan_to_none(amp)
                    sub[f"pha_p{pi}"] = _nan_to_none(pha)
                entry[f"f{fi}"] = sub
                if rs:
                    fcount.append(f"{fname[fi]} n={len(rs)}")
            entry["meta"] = (
                f"<b>antenna</b> {ant_names[ai]} &nbsp;|&nbsp; <b>SPW</b> {spw} "
                f"&nbsp;|&nbsp; " + ", ".join(fcount)
            )
            store[f"a{ai}_s{spw}"] = entry

    spw0 = spw_ids[0]
    init = store[f"a0_s{spw0}"]
    src_by_field = {fi: ColumnDataSource(data=_cds_init(init[f"f{fi}"])) for fi in fields}
    src_list = [
        src_by_field[fi] for fi in fields
    ]  # ordered for CustomJS (dict-of-models won't serialize)

    fa = figure(
        width=1000,
        height=300,
        title="Amplitude",
        x_axis_label="Time (h)",
        y_axis_label="Amp",
        toolbar_location="right",
    )
    fp = figure(
        width=1000,
        height=300,
        title="Phase",
        x_axis_label="Time (h)",
        y_axis_label="Phase (deg)",
        toolbar_location="right",
    )
    for k, fi in enumerate(fields):
        mk = _MARKERS[k % 4]  # marker = field
        src = src_by_field[fi]
        for pi in range(n_poln):
            color = _CORR_COLORS[pi % 4]  # color = correlation (R/L)
            lbl = f"{fname[fi]} {labels[pi]}"
            fa.scatter(
                "x", f"amp_p{pi}", source=src, size=6, color=color, marker=mk, legend_label=lbl
            )
            fp.scatter(
                "x", f"pha_p{pi}", source=src, size=6, color=color, marker=mk, legend_label=lbl
            )
    fa.legend.click_policy = "hide"
    fp.legend.click_policy = "hide"
    fa.legend.label_text_font_size = "8px"
    fp.legend.label_text_font_size = "8px"

    ant_slider = Slider(
        start=0,
        end=len(ant_names) - 1,
        value=0,
        step=1,
        title=f"Antenna: {ant_names[0]}",
        width=1000,
    )
    spw_slider = Slider(
        start=0, end=len(spw_ids) - 1, value=0, step=1, title=f"SPW: {spw0}", width=1000
    )
    chk = CheckboxGroup(labels=["metadata"], active=[0])
    meta_div = Div(text=init["meta"], width=1000)
    cb = CustomJS(
        args=dict(
            srcs=src_list,
            store=store,
            antS=ant_slider,
            spwS=spw_slider,
            ants=ant_names,
            spws=spw_ids,
            fields=fields,
            metaDiv=meta_div,
            chk=chk,
            npoln=n_poln,
        ),
        code="""
        const ai=antS.value, si=spwS.value, spw=spws[si];
        antS.title='Antenna: '+ants[ai]; spwS.title='SPW: '+spw;
        const d=store['a'+ai+'_s'+spw]; if(!d){return;}
        const fix=a=>a.map(v=>v===null?NaN:v);
        for(let k=0;k<fields.length;k++){
          const sub=d['f'+fields[k]]; const s=srcs[k]; if(!sub||!s){continue;}
          const nd={x:sub.x};
          for(let p=0;p<npoln;p++){nd['amp_p'+p]=fix(sub['amp_p'+p]); nd['pha_p'+p]=fix(sub['pha_p'+p]);}
          s.data=nd; s.change.emit();
        }
        metaDiv.text=chk.active.includes(0)?d.meta:'';
        """,
    )
    ant_slider.js_on_change("value", cb)
    spw_slider.js_on_change("value", cb)
    chk.js_on_change("active", cb)
    return column(
        Div(text=f"<h3 style='margin:2px'>{title}</h3>"),
        Div(text=f"<div style='font-size:90%'>{header}</div>", width=1000),
        ant_slider,
        spw_slider,
        chk,
        meta_div,
        fa,
        fp,
    )


def _kcross_view(sol: dict, header: str, title: str) -> object:
    """Kcross: cross-hand delay vs antenna, all SPWs overlaid coloured by SPW.

    The cross-hand delay lives in a single polarization; the other pol is
    identically zero and is not plotted.
    """
    from bokeh.layouts import column
    from bokeh.models import ColorBar, ColumnDataSource, Div, LinearColorMapper
    from bokeh.palettes import Turbo256
    from bokeh.plotting import figure
    from bokeh.transform import transform

    ant_names, spw_ids = sol["ant_names"], sol["spw_ids"]
    n_poln = sol["n_poln"]
    by_key = {}
    for r in sol["rows"]:
        by_key.setdefault((r["ant"], r["spw"]), r)

    # Which polns carry signal (drop the uniformly-zero one).
    active = []
    for pi in range(n_poln):
        vals = [abs(r["param"][pi, 0]) for r in sol["rows"] if not bool(r["flag"][pi, 0])]
        if vals and np.nanmax(vals) > 1e-9:
            active.append(pi)
    if not active:
        active = [0]

    xs, ys, spws = [], [], []
    for spw in spw_ids:
        for ai, aname in enumerate(ant_names):
            r = by_key.get((ai, spw))
            if r is None:
                continue
            for pi in active:
                if bool(r["flag"][pi, 0]):
                    continue
                xs.append(aname)
                ys.append(float(r["param"][pi, 0]))
                spws.append(spw)
    src = ColumnDataSource(dict(x=xs, y=ys, spw=spws))

    mapper = LinearColorMapper(palette=Turbo256, low=min(spw_ids), high=max(spw_ids))
    fig = figure(
        width=1000,
        height=400,
        title="Cross-hand delay vs antenna (colour = SPW)",
        x_range=ant_names,
        x_axis_label="Antenna",
        y_axis_label="Delay (ns)",
        toolbar_location="right",
    )
    fig.xaxis.major_label_orientation = 0.8
    fig.scatter("x", "y", source=src, size=9, line_color=None, fill_color=transform("spw", mapper))
    fig.add_layout(ColorBar(color_mapper=mapper, title="SPW", width=10), "right")

    pol_note = (
        f"pol {sol['poln_labels'][active[0]]}"
        if len(active) == 1
        else "pols " + ",".join(sol["poln_labels"][p] for p in active)
    )
    return column(
        Div(text=f"<h3 style='margin:2px'>{title}</h3>"),
        Div(
            text=f"<div style='font-size:90%'>{header} &nbsp;|&nbsp; {pol_note} (other pol ≡ 0, omitted)</div>",
            width=1000,
        ),
        fig,
    )


def _delay_view(sol: dict, header: str, title: str) -> object:
    """K/Kcross: delay vs antenna; SPW slider."""
    from bokeh.layouts import column
    from bokeh.models import CheckboxGroup, ColumnDataSource, CustomJS, Div, Slider
    from bokeh.plotting import figure

    ant_names, spw_ids = sol["ant_names"], sol["spw_ids"]
    labels, n_poln = sol["poln_labels"], sol["n_poln"]
    by_key = {}
    for r in sol["rows"]:
        by_key.setdefault((r["ant"], r["spw"]), r)
    store: dict[str, dict] = {}
    for spw in spw_ids:
        entry = {"x": list(ant_names)}
        for pi in range(n_poln):
            ys = []
            for ai in range(len(ant_names)):
                r = by_key.get((ai, spw))
                ys.append(
                    np.nan if (r is None or bool(r["flag"][pi, 0])) else float(r["param"][pi, 0])
                )
            entry[f"d_p{pi}"] = _nan_to_none(ys)
        entry["meta"] = f"<b>SPW</b> {spw}"
        store[f"s{spw}"] = entry

    spw0 = spw_ids[0]
    src = ColumnDataSource(data=_cds_init(store[f"s{spw0}"]))
    fig = figure(
        width=1000,
        height=380,
        title="Delay vs antenna",
        x_range=ant_names,
        x_axis_label="Antenna",
        y_axis_label="Delay (ns)",
        toolbar_location="right",
    )
    fig.xaxis.major_label_orientation = 0.8
    for pi in range(n_poln):
        fig.scatter(
            "x", f"d_p{pi}", source=src, size=9, color=_CORR_COLORS[pi % 4], legend_label=labels[pi]
        )
    fig.legend.click_policy = "hide"
    spw_slider = Slider(
        start=0, end=len(spw_ids) - 1, value=0, step=1, title=f"SPW: {spw0}", width=1000
    )
    chk = CheckboxGroup(labels=["metadata"], active=[0])
    meta_div = Div(text=store[f"s{spw0}"]["meta"], width=1000)
    cb = CustomJS(
        args=dict(
            src=src,
            store=store,
            spwS=spw_slider,
            spws=spw_ids,
            metaDiv=meta_div,
            chk=chk,
            npoln=n_poln,
        ),
        code="""
        const si=spwS.value, spw=spws[si]; spwS.title='SPW: '+spw;
        const d=store['s'+spw]; if(!d){return;}
        const fix=a=>a.map(v=>v===null?NaN:v); const nd={x:d.x};
        for(let p=0;p<npoln;p++){nd['d_p'+p]=fix(d['d_p'+p]);}
        src.data=nd; src.change.emit(); metaDiv.text=chk.active.includes(0)?d.meta:'';
        """,
    )
    spw_slider.js_on_change("value", cb)
    chk.js_on_change("active", cb)
    return column(
        Div(text=f"<h3 style='margin:2px'>{title}</h3>"),
        Div(text=f"<div style='font-size:90%'>{header}</div>", width=1000),
        spw_slider,
        chk,
        meta_div,
        fig,
    )


# ---------------------------------------------------------------------------
# Main run()
# ---------------------------------------------------------------------------


def build_layout(caltable_path: str) -> dict:
    """Read a caltable and build its Bokeh layout, routed by VisCal. No file written.

    Returns {layout, title, vc, view, prov} — reused by run() and the combined render.
    """
    sol = _read_solutions(caltable_path)
    vc = sol["vc"]
    stem = Path(caltable_path).name
    prov = _call_provenance(caltable_path)
    header = _provenance_header(prov, vc, stem)

    if sol["is_delay"] and vc.lower() == "kcross":
        layout = _kcross_view(sol, header, f"Kcross delay — {stem}")
        kind = "kcross_vs_antenna"
    elif sol["is_delay"]:
        layout = _delay_view(sol, header, f"{vc} delay — {stem}")
        kind = "delay_vs_antenna"
    elif vc == "Df":
        layout = _freq_view(sol, True, False, header, f"Df leakage — {stem}")
        kind = "amp_vs_freq"
    elif vc == "Xf":
        layout = _freq_view(sol, False, True, header, f"Xf angle — {stem}", color_by_spw=True)
        kind = "phase_vs_freq"
    elif sol["is_freq_dep"]:
        layout = _freq_view(sol, True, True, header, f"Bandpass — {stem}")
        kind = "amp_phase_vs_freq"
    else:
        layout = _time_view(sol, header, f"Gain — {stem}")
        kind = "amp_phase_vs_time"
    return {
        "layout": layout,
        "title": f"{vc} — {stem}",
        "vc": vc,
        "view": kind,
        "prov": prov,
        "sol": sol,
    }


def run(caltable_path: str, output_dir: str) -> dict:
    """
    Render a single caltable to a self-contained Bokeh HTML, read directly from
    the caltable columns (no ms_calsol_stats). View routed by VisCal type.
    """
    from bokeh.embed import file_html
    from bokeh.resources import CDN

    p = Path(caltable_path).expanduser().resolve()
    out = Path(output_dir).expanduser().resolve()
    if not p.exists() or not p.is_dir():
        from ms_inspect.util.formatting import error_envelope

        return error_envelope(
            TOOL_NAME, caltable_path, "CALTABLE_NOT_FOUND", f"Calibration table not found: {p}"
        )
    out.mkdir(parents=True, exist_ok=True)

    built = build_layout(caltable_path)
    layout, vc, kind, prov, sol = (
        built["layout"],
        built["vc"],
        built["view"],
        built["prov"],
        built["sol"],
    )
    stem = p.name

    html_path = str(out / f"{stem}_plot.html")
    with open(html_path, "w") as fh:
        fh.write(file_html(layout, CDN, f"{vc} — {stem}"))

    return response_envelope(
        tool_name=TOOL_NAME,
        ms_path=caltable_path,
        data={
            "html_path": fmt_field(html_path),
            "viscal": fmt_field(vc),
            "view": fmt_field(kind),
            "ms_name": fmt_field(prov.get("ms_name")),
            "call_params": fmt_field(prov.get("params", {})),
            "n_antennas": fmt_field(len(sol["ant_names"])),
            "n_spw": fmt_field(len(sol["spw_ids"])),
        },
        warnings=[],
        casa_calls=[
            f"tb.open({stem}) → CPARAM/FPARAM, FLAG, ANTENNA1, SPECTRAL_WINDOW_ID, FIELD_ID, TIME",
            "tb.open(ANTENNA/FIELD/SPECTRAL_WINDOW) → NAME, CHAN_FREQ",
            "getkeyword(MSName)",
        ],
    )
