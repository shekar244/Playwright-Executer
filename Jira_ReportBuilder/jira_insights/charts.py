"""
Plotly figure factory for pivot reports.

One consistent visual language for every chart type: a validated dark-mode
categorical palette in fixed order (switchable colour themes), 4px rounded
data-ends, 2px surface gaps between touching fills, soft gradient washes
under lines and areas, hairline solid grid, a hover tooltip on every mark,
selective labels, and a single y-axis.

Colours follow the entity, not its rank: `color_order` comes from the
unfiltered dataset, so filtering never repaints the surviving series.
"""
from __future__ import annotations

from dataclasses import dataclass

import plotly.graph_objects as go

from .pivot import OTHER, PivotResult, ReportSpec

SURFACE   = "#181828"   # card surface (host --surface)
INK       = "#dde3f8"
INK_MUTED = "#8e98bc"
GRID      = "#2a2a42"
AXIS      = "#3a3a58"
HOVER_BG  = "#1f1f34"
OTHER_COLOR = "#6e6e8c"


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

_BAR_CHARTS = ("Column", "Stacked column", "Bar", "Stacked bar")
_DATE_TICKS = {"Day": "%b %d", "Week": "%b %d", "Month": "%b %Y", "Quarter": "%b %Y", "Year": "%Y"}
_DATE_HOVER = {"Day": "%d %b %Y", "Week": "Week of %d %b %Y", "Month": "%B %Y",
               "Quarter": "Quarter from %b %Y", "Year": "%Y"}


def _template() -> go.layout.Template:
    axis = dict(gridcolor=GRID, gridwidth=1, griddash="solid", linecolor=AXIS, linewidth=1,
                zerolinecolor=AXIS, zerolinewidth=1, tickfont=dict(color=INK_MUTED, size=11),
                title=dict(font=dict(color=INK_MUTED, size=11)), automargin=True)
    return go.layout.Template(layout=dict(
        font=dict(family="Inter, system-ui, -apple-system, Segoe UI, sans-serif", size=12, color=INK),
        paper_bgcolor=SURFACE, plot_bgcolor=SURFACE, colorway=list(THEMES[DEFAULT_THEME].palette),
        margin=dict(l=8, r=16, t=36, b=8),
        legend=dict(orientation="h", x=0, xanchor="left", y=1.02, yanchor="bottom",
                    font=dict(color=INK_MUTED, size=11), title=dict(text="")),
        hoverlabel=dict(bgcolor=HOVER_BG, bordercolor="#4a4a72", font=dict(color=INK, size=12)),
        xaxis=dict(axis, showgrid=False), yaxis=dict(axis, showgrid=True, separatethousands=True),
        bargap=0.38, bargroupgap=0.1, barcornerradius=4,
        uniformtext=dict(minsize=9, mode="hide"),
    ))


TEMPLATE = _template()


# ── Colour helpers ────────────────────────────────────────────────────────────

def color_map(visible: list, color_order: list | tuple = (),
              palette: tuple = THEMES[DEFAULT_THEME].palette) -> dict:
    """Stable slot per category: ranked by the full dataset, then free slots."""
    ranked = [c for c in color_order if c != OTHER][:len(palette)]
    slots = {c: palette[i] for i, c in enumerate(ranked)}
    used = {slots[c] for c in visible if c in slots}
    free = [p for p in palette if p not in used]
    out = {}
    for c in visible:
        if c == OTHER:
            out[c] = OTHER_COLOR
        elif c in slots:
            out[c] = slots[c]
        else:
            out[c] = free.pop(0) if free else OTHER_COLOR
    return out


def _ink_on(hex_color: str) -> str:
    """White or dark text for a label sitting inside a coloured fill."""
    r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in (r, g, b)]
    lum = 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
    return "#0e0e18" if lum > 0.28 else "#ffffff"


def _rgba(hex_color: str, alpha: float) -> str:
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return f"rgba({r},{g},{b},{alpha})"


def _wash(hex_color: str, top: float) -> dict:
    """Vertical gradient fill: transparent at the baseline, a soft tint at the line."""
    return dict(type="vertical", colorscale=[[0, _rgba(hex_color, 0.0)], [1, _rgba(hex_color, top)]])


def _value_format(spec: ReportSpec) -> tuple[str, str]:
    """(d3 number format, suffix) for values in labels and tooltips."""
    if spec.normalize and spec.series:
        return ".1f", "%"
    if not spec.value or spec.agg in ("Count", "Distinct count"):
        return ",.0f", ""
    return ",.1f", ""


# ── Figure builders ───────────────────────────────────────────────────────────

def _empty(message: str = "No issues match this report") -> go.Figure:
    fig = go.Figure(layout=dict(template=TEMPLATE, height=220))
    fig.add_annotation(text=message, showarrow=False, font=dict(color=INK_MUTED, size=13),
                       xref="paper", yref="paper", x=0.5, y=0.5)
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    return fig


def _series_frames(result: PivotResult, spec: ReportSpec):
    long = result.long
    if not result.series_order:
        yield spec.value_label, long
        return
    col = spec.series
    for name in result.series_order:
        yield name, long[long[col] == name]


def _category_axis(fig: go.Figure, result: PivotResult, spec: ReportSpec, axis: str) -> None:
    update = fig.update_xaxes if axis == "x" else fig.update_yaxes
    if result.row_is_date:
        update(type="date", tickformat=_DATE_TICKS.get(spec.date_grain, "%b %Y"),
               hoverformat=_DATE_HOVER.get(spec.date_grain, "%b %Y"))
    else:
        update(type="category", categoryorder="array", categoryarray=result.row_order)


def _bars(result: PivotResult, spec: ReportSpec, colors: dict, theme: Theme) -> go.Figure:
    horizontal = spec.chart in ("Bar", "Stacked bar")
    stacked = spec.chart.startswith("Stacked") or (spec.normalize and bool(spec.series))
    fmt, suffix = _value_format(spec)
    multi = bool(result.series_order)
    fig = go.Figure(layout=dict(template=TEMPLATE, barmode="stack" if stacked else "group"))
    for name, frame in _series_frames(result, spec):
        cats, vals = frame[spec.rows], frame["Value"]
        if multi or not colors:
            fill = colors.get(name, theme.palette[0])
            ink = _ink_on(fill)
        else:                               # one series, a colour per category
            fill = [colors.get(c, theme.palette[0]) for c in cats]
            ink = [_ink_on(c) for c in fill]
        value_ref = "%{x" if horizontal else "%{y"
        cat_ref = "%{y}" if horizontal else "%{x}"
        fig.add_bar(
            x=vals if horizontal else cats, y=cats if horizontal else vals,
            name=str(name), orientation="h" if horizontal else "v",
            marker=dict(color=fill, line=dict(color=SURFACE, width=2 if stacked or multi else 0)),
            hovertemplate=f"<b>{value_ref}:{fmt}}}{suffix}</b><br>{cat_ref}"
                          + ("<extra>%{fullData.name}</extra>" if multi else "<extra></extra>"),
            text=vals if spec.show_labels else None,
            texttemplate=f"%{{text:{fmt}}}{suffix}" if spec.show_labels else None,
            textposition="inside" if stacked else "outside",
            insidetextfont=dict(color=ink), outsidetextfont=dict(color=INK_MUTED),
            cliponaxis=False,
        )
    _category_axis(fig, result, spec, "y" if horizontal else "x")
    if horizontal:
        fig.update_xaxes(showgrid=True)
        fig.update_yaxes(showgrid=False)
        if not result.row_is_date:
            fig.update_yaxes(autorange="reversed")
        fig.update_layout(legend_traceorder="normal")   # read left→right like the stack
    fig.update_layout(showlegend=multi)
    return fig


def _lines(result: PivotResult, spec: ReportSpec, colors: dict, theme: Theme) -> go.Figure:
    area = spec.chart == "Area"
    fmt, suffix = _value_format(spec)
    multi = bool(result.series_order)
    fig = go.Figure(layout=dict(template=TEMPLATE, hovermode="x unified"))
    for name, frame in _series_frames(result, spec):
        color = colors.get(name, theme.palette[0])
        n = len(frame)
        # Selective labels: only the end value of each line.
        labels = [""] * (n - 1) + [format(frame["Value"].iloc[-1], fmt) + suffix] if spec.show_labels and n else None
        # Areas stack with a gradient wash per band; a lone line gets a soft glow beneath it.
        fill = "tonexty" if area else ("tozeroy" if not multi else None)
        fig.add_scatter(
            x=frame[spec.rows], y=frame["Value"], name=str(name),
            mode=("lines+markers" if n <= 40 else "lines") + ("+text" if labels else ""),
            line=dict(color=color, width=2, shape="linear"),
            marker=dict(size=8, color=color, line=dict(color=SURFACE, width=2)),
            stackgroup="one" if area else None, fill=fill,
            fillgradient=_wash(color, 0.42 if area else 0.28) if fill else None,
            text=labels, textposition="middle right", textfont=dict(color=INK_MUTED),
            hovertemplate=f"<b>%{{y:{fmt}}}{suffix}</b>" + ("  %{fullData.name}" if multi else "") + "<extra></extra>",
            cliponaxis=False,
        )
    _category_axis(fig, result, spec, "x")
    fig.update_xaxes(showspikes=True, spikemode="across", spikethickness=1, spikecolor=AXIS,
                     spikedash="solid", spikesnap="data")
    fig.update_layout(showlegend=multi)
    return fig


def _donut(result: PivotResult, spec: ReportSpec, colors: dict, theme: Theme) -> go.Figure:
    fmt, _ = _value_format(spec)
    labels = [str(r) for r in result.long[spec.rows]]
    fig = go.Figure(layout=dict(template=TEMPLATE))
    fig.add_pie(
        labels=labels, values=result.long["Value"], hole=0.66, sort=False, direction="clockwise",
        marker=dict(colors=[colors.get(r, theme.palette[0]) for r in result.long[spec.rows]],
                    line=dict(color=SURFACE, width=3)),
        textinfo="percent" if spec.show_labels else "none",
        textfont=dict(color=INK), insidetextorientation="horizontal",
        hovertemplate=f"<b>%{{value:{fmt}}}</b> · %{{percent}}<br>%{{label}}<extra></extra>",
    )
    total = result.long["Value"].sum()
    fig.add_annotation(
        text=f"<b style='font-size:28px'>{format(total, fmt)}</b>"
             f"<br><span style='font-size:11px;color:{INK_MUTED}'>{spec.value_label}</span>",
        showarrow=False, x=0.5, y=0.5, xref="paper", yref="paper", font=dict(color=INK))
    fig.update_layout(legend=dict(orientation="v", x=1.02, xanchor="left", y=0.5, yanchor="middle"))
    return fig


def _row_label(row, result: PivotResult, spec: ReportSpec) -> str:
    return row.strftime(_DATE_TICKS.get(spec.date_grain, "%b %Y")) if result.row_is_date else str(row)


def _heatmap(result: PivotResult, spec: ReportSpec, theme: Theme) -> go.Figure:
    fmt, suffix = _value_format(spec)
    table = result.table.drop(columns=["Total"], errors="ignore")
    rows = [_row_label(r, result, spec) for r in table.index]
    cols = [str(c) for c in table.columns]
    ramp = theme.sequential
    scale = [[i / (len(ramp) - 1), c] for i, c in enumerate(ramp)]
    fig = go.Figure(layout=dict(template=TEMPLATE))
    fig.add_heatmap(
        z=table.values, x=cols, y=rows, colorscale=scale, xgap=3, ygap=3, hoverongaps=False,
        hovertemplate=f"<b>%{{z:{fmt}}}{suffix}</b><br>%{{y}} · %{{x}}<extra></extra>",
        texttemplate=f"%{{z:{fmt}}}" if spec.show_labels else None,
        colorbar=dict(thickness=10, outlinewidth=0, tickfont=dict(color=INK_MUTED, size=10)),
    )
    fig.update_xaxes(type="category", showgrid=False, side="top")
    fig.update_yaxes(type="category", showgrid=False, autorange="reversed")
    return fig


def _treemap(result: PivotResult, spec: ReportSpec, colors: dict, theme: Theme) -> go.Figure:
    fmt, _ = _value_format(spec)
    long = result.long
    row_totals = long.groupby(spec.rows, sort=False)["Value"].sum()
    ids, labels, parents, values, fills = [], [], [], [], []
    many = len(row_totals) > len(theme.palette)
    for row, total in row_totals.items():
        color = theme.palette[0] if many else colors.get(row, theme.palette[0])
        ids.append(f"r::{row}"); labels.append(_row_label(row, result, spec))
        parents.append(""); values.append(total); fills.append(color)
        if result.series_order:
            for _, rec in long[long[spec.rows] == row].iterrows():
                ids.append(f"r::{row}::{rec[spec.series]}"); labels.append(str(rec[spec.series]))
                parents.append(f"r::{row}"); values.append(rec["Value"]); fills.append(_rgba(color, 0.78))
    fig = go.Figure(layout=dict(template=TEMPLATE, margin=dict(l=4, r=4, t=8, b=4)))
    fig.add_treemap(
        ids=ids, labels=labels, parents=parents, values=values, branchvalues="total",
        marker=dict(colors=fills, line=dict(color=SURFACE, width=2), cornerradius=6),
        textinfo="label+value" if spec.show_labels else "label",
        texttemplate=f"%{{label}}<br>%{{value:{fmt}}}" if spec.show_labels else "%{label}",
        hovertemplate=f"<b>%{{value:{fmt}}}</b><br>%{{label}}<extra></extra>",
        tiling=dict(pad=2), pathbar=dict(visible=False),
    )
    return fig


def _column_gap(result: PivotResult, spec: ReportSpec) -> float:
    """bargap that keeps vertical bars at ~24px or thinner in a typical ~560px card."""
    if spec.chart not in ("Column", "Stacked column"):
        return 0.38
    per_group = 1 if spec.chart == "Stacked column" or spec.normalize else max(len(result.series_order), 1)
    band = 560 / max(len(result.row_order), 1)
    return min(0.8, max(0.38, 1 - 24 * per_group / band))


def natural_height(result: PivotResult, spec: ReportSpec) -> int:
    """Pixel height a chart needs on its own (bars and heatmaps grow with their categories)."""
    if result.empty:
        return 220
    if spec.chart in ("Bar", "Stacked bar") and not result.row_is_date:
        return min(900, max(320, 30 * len(result.row_order) + 90))
    if spec.chart == "Heatmap":
        return min(900, max(320, 26 * len(result.row_order) + 110))
    return 360


def build_figure(result: PivotResult, spec: ReportSpec, color_order: list | tuple = (),
                 theme: str = DEFAULT_THEME, height: int | None = None) -> go.Figure:
    """Chart for every type except Number, which the UI renders as a stat tile.

    `height` overrides the natural height — the dashboard passes the tallest
    chart in a row so cards line up; the focus view passes a taller size."""
    if spec.chart == "Number":
        raise ValueError("Number reports render as stat tiles, not figures")
    fig = _empty() if result.empty else _chart(result, spec, color_order, THEMES.get(theme, THEMES[DEFAULT_THEME]))
    # Set explicitly — Streamlit repaints a paper colour that only comes from the template.
    fig.update_layout(paper_bgcolor=SURFACE, plot_bgcolor=SURFACE,
                      height=max(height or 0, natural_height(result, spec)))
    return fig


def _chart(result: PivotResult, spec: ReportSpec, color_order, theme: Theme) -> go.Figure:
    if result.series_order:
        colors = color_map(result.series_order, color_order, theme.palette)
    elif spec.chart in ("Donut", "Treemap") or (
            spec.color_by_category and spec.chart in _BAR_CHARTS and not result.row_is_date
            and len(result.row_order) <= len(theme.palette)):
        colors = color_map(result.row_order, color_order, theme.palette)
    else:
        colors = {}                      # one series → one colour for every mark (slot 1)

    if spec.chart in _BAR_CHARTS:
        fig = _bars(result, spec, colors, theme)
    elif spec.chart in ("Line", "Area"):
        fig = _lines(result, spec, colors, theme)
    elif spec.chart == "Donut":
        fig = _donut(result, spec, colors, theme)
    elif spec.chart == "Heatmap":
        fig = _heatmap(result, spec, theme)
    else:
        fig = _treemap(result, spec, colors, theme)

    if spec.chart in ("Column", "Stacked column"):
        fig.update_layout(bargap=_column_gap(result, spec))
    return fig
