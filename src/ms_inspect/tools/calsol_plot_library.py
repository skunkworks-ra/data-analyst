"""
calsol_plot_library.py — ms_plot_caltable_library

Render an explicit list of CASA calibration tables into ONE combined Bokeh HTML,
a tab per caltable. Each layout is built via calsol_plot.build_layout (direct
table read, no ms_calsol_stats). A table that fails becomes an error tab rather
than aborting the batch.
"""

from __future__ import annotations

from pathlib import Path

from ms_inspect.tools import calsol_plot
from ms_inspect.util.formatting import field as fmt_field
from ms_inspect.util.formatting import response_envelope

TOOL_NAME = "ms_plot_caltable_library"


def run(
    caltable_paths: list[str], output_dir: str, combined_name: str = "caltables_overview.html"
) -> dict:
    """
    Plot a list of caltables into a single combined HTML (one tab each).

    Args:
        caltable_paths: Ordered list of caltable directory paths.
        output_dir:     Directory to write the combined HTML.
        combined_name:  Filename for the combined HTML.

    Returns:
        Standard envelope: data["html_path"] and per-table tab/status list.
    """
    from bokeh.embed import file_html
    from bokeh.models import Div, TabPanel, Tabs
    from bokeh.resources import CDN

    out = Path(output_dir).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)

    panels: list = []
    entries: list[dict] = []
    warnings: list[str] = []

    for raw in caltable_paths:
        p = Path(raw).expanduser().resolve()
        name = p.name
        if not p.exists() or not p.is_dir():
            warnings.append(f"{name}: not found")
            entries.append({"caltable": str(p), "status": "error", "error": "not found"})
            panels.append(TabPanel(child=Div(text=f"<b>{name}</b>: not found"), title=f"⚠ {name}"))
            continue
        try:
            built = calsol_plot.build_layout(str(p))
            panels.append(TabPanel(child=built["layout"], title=name))
            entries.append(
                {"caltable": str(p), "status": "ok", "viscal": built["vc"], "view": built["view"]}
            )
        except Exception as exc:  # partial success — one bad table is an error tab
            warnings.append(f"{name}: {exc}")
            entries.append({"caltable": str(p), "status": "error", "error": str(exc)})
            panels.append(TabPanel(child=Div(text=f"<b>{name}</b>: {exc}"), title=f"⚠ {name}"))

    html_path = str(out / combined_name)
    with open(html_path, "w") as fh:
        fh.write(file_html(Tabs(tabs=panels), CDN, "Caltable overview"))

    n_ok = sum(1 for e in entries if e["status"] == "ok")
    return response_envelope(
        tool_name=TOOL_NAME,
        ms_path=output_dir,
        data={
            "html_path": fmt_field(html_path),
            "n_ok": fmt_field(n_ok),
            "n_error": fmt_field(len(entries) - n_ok),
            "tables": fmt_field(entries),
        },
        warnings=warnings,
        casa_calls=[],
    )
