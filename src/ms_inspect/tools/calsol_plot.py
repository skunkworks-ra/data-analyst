"""
calsol_plot.py — ms_calsol_plot

Reads a CASA calibration table via ms_calsol_stats, saves a numpy NPZ of the
extracted arrays, and generates a Bokeh HTML dashboard. Returns paths to both
output files.

Supports G Jones, B Jones, and K Jones tables. Dashboard layout adapts to
table type.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from ms_inspect.tools import calsol_stats
from ms_inspect.util.formatting import field as fmt_field
from ms_inspect.util.formatting import response_envelope

TOOL_NAME = "ms_calsol_plot"


# ---------------------------------------------------------------------------
# Helpers — array extraction
# ---------------------------------------------------------------------------


def _val(data: dict, key: str) -> np.ndarray | list | None:
    """Extract the value from a fmt_field wrapper; return None if UNAVAILABLE."""
    entry = data.get(key)
    if entry is None or entry.get("flag") == "UNAVAILABLE":
        return None
    return entry["value"]


def _arr(data: dict, key: str) -> np.ndarray | None:
    """Extract and convert to float numpy array; None if absent."""
    v = _val(data, key)
    if v is None:
        return None
    return np.array(v, dtype=float)


# ---------------------------------------------------------------------------
# NPZ save
# ---------------------------------------------------------------------------


def _save_npz(npz_path: str, data: dict, table_type: str) -> None:
    arrays: dict[str, np.ndarray] = {}

    for key in ("ant_names", "spw_ids", "field_ids", "field_names"):
        v = _val(data, key)
        if v is not None:
            arrays[key] = np.array(v)

    for key in (
        "flagged_frac",
        "snr_mean",
        "amp_mean",
        "amp_std",
        "phase_mean_deg",
        "phase_rms_deg",
    ):
        a = _arr(data, key)
        if a is not None:
            arrays[key] = a

    if table_type == "B":
        a = _arr(data, "amp_array")
        if a is not None:
            arrays["amp_array"] = a

    if table_type == "K":
        for key in ("delay_ns", "delay_rms_ns"):
            a = _arr(data, key)
            if a is not None:
                arrays[key] = a

    np.savez(npz_path, **arrays)


# ---------------------------------------------------------------------------
# Bokeh dashboard builders
# ---------------------------------------------------------------------------


def _palette(n: int) -> list[str]:
    from bokeh.palettes import Category10, Category20

    if n <= 2:
        return ["#1f77b4", "#ff7f0e"]
    if n <= 10:
        return list(Category10[max(n, 3)])
    return list(Category20[min(n, 20)])


def _build_g_dashboard(data: dict) -> object:
    from bokeh.layouts import column
    from bokeh.models import HoverTool, Legend, LegendItem
    from bokeh.plotting import figure

    ant_names = list(_val(data, "ant_names") or [])
    field_names = list(_val(data, "field_names") or [])
    spw_ids = list(_val(data, "spw_ids") or [])

    amp_mean = _arr(data, "amp_mean")  # [n_ant, n_spw, n_field]
    phase_rms = _arr(data, "phase_rms_deg")  # [n_ant, n_spw, n_field]
    flagged = _arr(data, "flagged_frac")  # [n_ant, n_spw, n_field]

    n_field = len(field_names)
    colors = _palette(n_field)

    # --- amplitude and phase panels (averaged over SPWs) ---
    amp_fig = figure(
        width=900,
        height=280,
        title="Gain amplitude mean (avg over SPWs)",
        x_range=ant_names,
        toolbar_location="right",
    )
    amp_fig.xaxis.major_label_orientation = 0.7

    phase_fig = figure(
        width=900,
        height=280,
        title="Phase RMS deg (avg over SPWs)",
        x_range=ant_names,
        toolbar_location="right",
    )
    phase_fig.xaxis.major_label_orientation = 0.7

    amp_legend_items = []
    phase_legend_items = []

    for fi, (fname, color) in enumerate(zip(field_names, colors, strict=False)):
        if amp_mean is not None:
            y_amp = np.nanmean(amp_mean[:, :, fi], axis=1)
            r = amp_fig.circle(
                x=ant_names,
                y=y_amp.tolist(),
                size=8,
                color=color,
                alpha=0.8,
            )
            amp_fig.add_tools(
                HoverTool(
                    renderers=[r],
                    tooltips=[
                        ("antenna", "@x"),
                        ("amp_mean", "@y{0.4f}"),
                    ],
                )
            )
            amp_legend_items.append(LegendItem(label=fname, renderers=[r]))

        if phase_rms is not None:
            y_ph = np.nanmean(phase_rms[:, :, fi], axis=1)
            r = phase_fig.circle(
                x=ant_names,
                y=y_ph.tolist(),
                size=8,
                color=color,
                alpha=0.8,
            )
            phase_fig.add_tools(
                HoverTool(
                    renderers=[r],
                    tooltips=[
                        ("antenna", "@x"),
                        ("phase_rms_deg", "@y{0.2f}"),
                    ],
                )
            )
            phase_legend_items.append(LegendItem(label=fname, renderers=[r]))

    if amp_legend_items:
        amp_fig.add_layout(Legend(items=amp_legend_items), "right")
    if phase_legend_items:
        phase_fig.add_layout(Legend(items=phase_legend_items), "right")

    # --- flagged fraction heatmap [ant × spw] averaged over fields ---
    heatmap = _flagged_heatmap(
        flagged,
        ant_names,
        [str(s) for s in spw_ids],
        title="Flagged fraction heatmap (avg over fields)",
    )

    return column(amp_fig, phase_fig, heatmap)


def _build_b_dashboard(data: dict) -> object:
    from bokeh.layouts import column
    from bokeh.models import Legend, LegendItem, TabPanel, Tabs
    from bokeh.plotting import figure

    ant_names = list(_val(data, "ant_names") or [])
    spw_ids = list(_val(data, "spw_ids") or [])
    flagged = _arr(data, "flagged_frac")

    amp_array = _arr(data, "amp_array")  # [n_ant, n_spw, n_field, n_chan]

    n_ant = len(ant_names)
    colors = _palette(n_ant)

    tab_panels = []
    if amp_array is not None:
        n_chan = amp_array.shape[3]
        channels = list(range(n_chan))

        for si, spw in enumerate(spw_ids):
            fig = figure(
                width=900,
                height=300,
                title=f"Bandpass amplitude — SPW {spw} (avg over fields)",
                toolbar_location="right",
            )
            legend_items = []
            for ai, (aname, color) in enumerate(zip(ant_names, colors, strict=False)):
                # average over field axis
                y = np.nanmean(amp_array[ai, si, :, :], axis=0)
                r = fig.line(
                    x=channels,
                    y=y.tolist(),
                    line_color=color,
                    line_width=1.2,
                    alpha=0.75,
                )
                legend_items.append(LegendItem(label=aname, renderers=[r]))

            fig.add_layout(Legend(items=legend_items, ncols=3), "right")
            fig.legend.label_text_font_size = "9px"
            fig.xaxis.axis_label = "Channel"
            fig.yaxis.axis_label = "Amplitude"
            tab_panels.append(TabPanel(child=fig, title=f"SPW {spw}"))

    heatmap = _flagged_heatmap(
        flagged,
        ant_names,
        [str(s) for s in spw_ids],
        title="Flagged fraction heatmap (avg over fields)",
    )

    if tab_panels:
        return column(Tabs(tabs=tab_panels), heatmap)
    return column(heatmap)


def _build_k_dashboard(data: dict) -> object:
    from bokeh.layouts import column
    from bokeh.models import HoverTool, Legend, LegendItem
    from bokeh.plotting import figure

    ant_names = list(_val(data, "ant_names") or [])
    spw_ids = list(_val(data, "spw_ids") or [])
    field_names = list(_val(data, "field_names") or [])

    delay_ns = _arr(data, "delay_ns")  # [n_ant, n_spw, n_field, n_corr]
    delay_rms = _arr(data, "delay_rms_ns")  # [n_spw, n_field]
    flagged = _arr(data, "flagged_frac")

    n_field = len(field_names)
    colors = _palette(n_field)

    delay_fig = figure(
        width=900,
        height=300,
        title="Delay per antenna (ns, avg over SPWs and corrs)",
        x_range=ant_names,
        toolbar_location="right",
    )
    delay_fig.xaxis.major_label_orientation = 0.7
    delay_fig.yaxis.axis_label = "Delay (ns)"

    if delay_ns is not None:
        legend_items = []
        for fi, (fname, color) in enumerate(zip(field_names, colors, strict=False)):
            # mean over SPW and corr axes
            y = np.nanmean(delay_ns[:, :, fi, :], axis=(1, 2))
            r = delay_fig.circle(
                x=ant_names,
                y=y.tolist(),
                size=9,
                color=color,
                alpha=0.85,
            )
            delay_fig.add_tools(
                HoverTool(
                    renderers=[r],
                    tooltips=[
                        ("antenna", "@x"),
                        ("delay_ns", "@y{0.3f}"),
                    ],
                )
            )
            legend_items.append(LegendItem(label=fname, renderers=[r]))
        delay_fig.add_layout(Legend(items=legend_items), "right")

    rms_fig = figure(
        width=900,
        height=220,
        title="Delay RMS per SPW (ns, across antennas)",
        toolbar_location="right",
    )
    rms_fig.yaxis.axis_label = "Delay RMS (ns)"

    if delay_rms is not None:
        spw_labels = [str(s) for s in spw_ids]
        for fi, (fname, color) in enumerate(zip(field_names, colors, strict=False)):
            y = delay_rms[:, fi].tolist()
            rms_fig.vbar(
                x=spw_labels,
                top=y,
                width=0.4,
                color=color,
                alpha=0.75,
                legend_label=fname,
            )
        rms_fig.xaxis.axis_label = "SPW"

    heatmap = _flagged_heatmap(
        flagged,
        ant_names,
        [str(s) for s in spw_ids],
        title="Flagged fraction heatmap",
    )

    return column(delay_fig, rms_fig, heatmap)


def _flagged_heatmap(
    flagged: np.ndarray | None,
    ant_names: list[str],
    spw_labels: list[str],
    title: str,
) -> object:
    from bokeh.models import BasicTicker, ColorBar, HoverTool, LinearColorMapper
    from bokeh.plotting import figure
    from bokeh.transform import transform

    n_ant = len(ant_names)

    fig = figure(
        width=900,
        height=max(200, min(40 * n_ant, 500)),
        title=title,
        x_range=spw_labels,
        y_range=list(reversed(ant_names)),
        toolbar_location="right",
    )

    if flagged is not None:
        frac = np.nanmean(flagged, axis=2) if flagged.ndim == 3 else flagged

        xs, ys, vs = [], [], []
        for ai, aname in enumerate(ant_names):
            for si, slabel in enumerate(spw_labels):
                v = frac[ai, si]
                xs.append(slabel)
                ys.append(aname)
                vs.append(float(v) if not math.isnan(v) else 0.0)

        from bokeh.models import ColumnDataSource

        source = ColumnDataSource(dict(x=xs, y=ys, v=vs))
        mapper = LinearColorMapper(
            palette="Viridis256",
            low=0.0,
            high=1.0,
        )
        r = fig.rect(
            x="x",
            y="y",
            width=1,
            height=1,
            source=source,
            fill_color=transform("v", mapper),
            line_color=None,
        )
        fig.add_tools(
            HoverTool(
                renderers=[r],
                tooltips=[
                    ("SPW", "@x"),
                    ("antenna", "@y"),
                    ("flagged_frac", "@v{0.3f}"),
                ],
            )
        )
        cb = ColorBar(color_mapper=mapper, ticker=BasicTicker(), width=10)
        fig.add_layout(cb, "right")

    fig.xaxis.axis_label = "SPW"
    return fig


# ---------------------------------------------------------------------------
# Main run()
# ---------------------------------------------------------------------------


def run(caltable_path: str, output_dir: str) -> dict:
    """
    Inspect a CASA calibration table and produce a Bokeh dashboard + NPZ.

    Calls ms_calsol_stats internally, saves extracted arrays to an NPZ file,
    and writes a self-contained Bokeh HTML dashboard.

    Args:
        caltable_path: Path to the caltable directory.
        output_dir:    Directory to write {name}_stats.npz and {name}_dashboard.html.

    Returns:
        Standard response envelope with npz_path, html_path, and table_type.
    """
    from bokeh.embed import file_html
    from bokeh.resources import CDN

    p = Path(caltable_path).expanduser().resolve()
    out = Path(output_dir).expanduser().resolve()

    if not p.exists() or not p.is_dir():
        from ms_inspect.util.formatting import error_envelope

        return error_envelope(
            TOOL_NAME,
            caltable_path,
            "CALTABLE_NOT_FOUND",
            f"Calibration table not found: {p}",
        )

    out.mkdir(parents=True, exist_ok=True)
    stem = p.name
    npz_path = str(out / f"{stem}_stats.npz")
    html_path = str(out / f"{stem}_dashboard.html")

    # --- get stats ---
    stats = calsol_stats.run(caltable_path)
    if stats.get("status") == "error":
        from ms_inspect.util.formatting import error_envelope

        return error_envelope(
            TOOL_NAME,
            caltable_path,
            stats.get("error_type", "COMPUTATION_ERROR"),
            stats.get("message", "ms_calsol_stats failed"),
        )

    data = stats["data"]
    table_type = data.get("table_type", {}).get("value", "")
    warnings: list[str] = list(stats.get("warnings", []))

    # --- save NPZ ---
    _save_npz(npz_path, data, table_type)

    # --- build dashboard ---
    if table_type == "G":
        layout = _build_g_dashboard(data)
        title = f"Gain solutions — {stem}"
    elif table_type == "B":
        layout = _build_b_dashboard(data)
        title = f"Bandpass solutions — {stem}"
    elif table_type == "K":
        layout = _build_k_dashboard(data)
        title = f"Delay solutions — {stem}"
    else:
        warnings.append(
            f"Table type '{table_type}' has no dashboard template; producing flagged fraction heatmap only."
        )
        flagged = np.array(data.get("flagged_frac", {}).get("value") or [], dtype=float)
        ant_names = list(data.get("ant_names", {}).get("value") or [])
        spw_ids = list(data.get("spw_ids", {}).get("value") or [])
        layout = _flagged_heatmap(
            flagged if flagged.size else None,
            ant_names,
            [str(s) for s in spw_ids],
            title=f"Flagged fraction — {stem}",
        )

    html = file_html(layout, CDN, title)
    with open(html_path, "w") as fh:
        fh.write(html)

    result_data = {
        "npz_path": fmt_field(npz_path),
        "html_path": fmt_field(html_path),
        "table_type": fmt_field(table_type),
        "n_antennas": data.get("n_antennas", fmt_field(0)),
        "n_spw": data.get("n_spw", fmt_field(0)),
        "n_field": data.get("n_field", fmt_field(0)),
    }

    return response_envelope(
        tool_name=TOOL_NAME,
        ms_path=caltable_path,
        data=result_data,
        warnings=warnings,
        casa_calls=stats.get("provenance", {}).get("casa_calls", []),
    )
