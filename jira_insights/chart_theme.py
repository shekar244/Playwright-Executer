"""
Shared chart styling: surfaces and ink, reserved status colours, the validated
colour themes, the Plotly template, and small formatting helpers used by both
charts.py and dials.py.
"""
from __future__ import annotations

from dataclasses import dataclass

import plotly.graph_objects as go

from .pivot import PivotResult, ReportSpec

# ── Dark mode surface colours (default) ─────────────────────────────────────
SURFACE   = "#181828"   # card surface (host --surface)
INK       = "#dde3f8"
INK_MUTED = "#8e98bc"
GRID      = "#2a2a42"
AXIS      = "#3a3a58"
HOVER_BG  = "#1f1f34"
OTHER_COLOR = "#6e6e8c"
GAUGE_TRACK = "#262640"


@dataclass(frozen=True)
class ColorScheme:
    surface: str
    ink: str
    ink_muted: str
    grid: str
    axis: str
    hover_bg: str
    other_color: str
    gauge_track: str


DARK_SCHEME = ColorScheme(
    surface="#181828", ink="#dde3f8", ink_muted="#8e98bc",
    grid="#2a2a42", axis="#3a3a58", hover_bg="#1f1f34",
    other_color="#6e6e8c", gauge_track="#262640",
)

LIGHT_SCHEME = ColorScheme(
    surface="#ffffff", ink="#1a1d2e", ink_muted="#6b7294",
    grid="#e4e7f0", axis="#c8cdd8", hover_bg="#f0f1f6",
    other_color="#9ea2b8", gauge_track="#e8eaf0",
)


def get_scheme(dark: bool = True) -> ColorScheme:
    return DARK_SCHEME if dark else LIGHT_SCHEME


# Reserved status colours (good / warning / serious / critical) — never used for ordinary series.
GOOD, WARNING, SERIOUS, CRITICAL = "#0ca30c", "#fab219", "#ec835a", "#d03b3b"
LIGHT_GOOD = "#86c96f"     # the extra band on 4-band dials (red · amber · light green · green)
STATUS_COLORS = {
    "passed": GOOD, "pass": GOOD, "failed": CRITICAL, "fail": CRITICAL, "blocked": SERIOUS,
    "in progress": WARNING, "wip": WARNING, "not run": OTHER_COLOR, "unexecuted": OTHER_COLOR,
    "other": OTHER_COLOR, "yes": GOOD, "no": OTHER_COLOR,
}


@dataclass(frozen=True)
class Theme:
    name: str
    palette: tuple      # categorical slots, fixed order
    sequential: tuple   # magnitude ramp, dark → light so "near zero" recedes into the surface


# Every palette is validated on #181828 (adjacent pairs, dark mode): lightness
# band, chroma floor, CVD ΔE ≥ 8, normal-vision ΔE ≥ 15, every slot ≥ 3:1.
THEMES = {
    # Vivid OKLCH hues at the top of the dark lightness band — CVD ΔE ≥ 10.4.
    "Aurora": Theme("Aurora",
                    ("#3789fd", "#ea630c", "#0baa92", "#c28914",
                     "#f41f94", "#0ca62f", "#904fff", "#f7103b"),
                    # analogous blue → violet → magenta, monotonic lightness
                    ("#13236e", "#3a3493", "#6445b6", "#9255d5", "#bc72dd", "#e48fe6", "#ffb6e9")),
    # The calmer reference palette — CVD ΔE ≥ 8.4.
    "Classic": Theme("Classic",
                     ("#3987e5", "#d95926", "#199e70", "#c98500",
                      "#d55181", "#008300", "#9085e9", "#e66767"),
                     ("#0d366b", "#184f95", "#256abf", "#3987e5", "#6da7ec", "#9ec5f4", "#cde2fb")),
}
DEFAULT_THEME = "Aurora"

PLOTLY_CONFIG = {
    "displaylogo": False,
    "modeBarButtonsToRemove": ["lasso2d", "select2d", "autoScale2d"],
    "toImageButtonOptions": {"format": "png", "scale": 2},
}

DATE_TICKS = {"Day": "%b %d", "Week": "%b %d", "Month": "%b %Y", "Quarter": "%b %Y", "Year": "%Y"}
DATE_HOVER = {"Day": "%d %b %Y", "Week": "Week of %d %b %Y", "Month": "%B %Y",
               "Quarter": "Quarter from %b %Y", "Year": "%Y"}


def make_template(scheme: ColorScheme | None = None) -> go.layout.Template:
    s = scheme or DARK_SCHEME
    axis = dict(gridcolor=s.grid, gridwidth=1, griddash="solid", linecolor=s.axis, linewidth=1,
                zerolinecolor=s.axis, zerolinewidth=1, tickfont=dict(color=s.ink_muted, size=11),
                title=dict(font=dict(color=s.ink_muted, size=11)), automargin=True)
    border = "#4a4a72" if s is DARK_SCHEME else "#c8cdd8"
    return go.layout.Template(layout=dict(
        font=dict(family="Inter, system-ui, -apple-system, Segoe UI, sans-serif", size=12, color=s.ink),
        paper_bgcolor=s.surface, plot_bgcolor=s.surface, colorway=list(THEMES[DEFAULT_THEME].palette),
        margin=dict(l=8, r=16, t=36, b=8),
        legend=dict(orientation="h", x=0, xanchor="left", y=1.02, yanchor="bottom",
                    font=dict(color=s.ink_muted, size=11), title=dict(text="")),
        hoverlabel=dict(bgcolor=s.hover_bg, bordercolor=border, font=dict(color=s.ink, size=12)),
        xaxis=dict(axis, showgrid=False), yaxis=dict(axis, showgrid=True, separatethousands=True),
        bargap=0.38, bargroupgap=0.1, barcornerradius=4,
        uniformtext=dict(minsize=9, mode="hide"),
    ))


def _template() -> go.layout.Template:
    return make_template(DARK_SCHEME)


TEMPLATE = _template()


def value_format(spec: ReportSpec) -> tuple[str, str]:
    """(d3 number format, suffix) for values in labels and tooltips."""
    if spec.normalize and spec.series:
        return ".1f", "%"
    if not spec.value or spec.agg in ("Count", "Distinct count"):
        return ",.0f", ""
    if spec.agg == "Sum":
        return ",.0f", ""
    return ",.1f", ""


def row_label(row, result: PivotResult, spec: ReportSpec) -> str:
    return row.strftime(DATE_TICKS.get(spec.date_grain, "%b %Y")) if result.row_is_date else str(row)
