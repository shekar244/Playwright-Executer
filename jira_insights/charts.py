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

import re

import plotly.graph_objects as go

from .chart_theme import (  # noqa: F401 — re-exported for callers and tests
    AXIS, CRITICAL, DATE_HOVER, DATE_TICKS, DEFAULT_THEME, GAUGE_TRACK, GOOD, GRID, HOVER_BG, INK, INK_MUTED,
    LIGHT_GOOD, OTHER_COLOR, PLOTLY_CONFIG, SERIOUS, STATUS_COLORS, SURFACE, TEMPLATE, THEMES, WARNING, Theme,
    row_label, value_format)
from .dials import _gauges, _gauge_values, _meters, band_colors, dial_range, gauge_color  # noqa: F401
from .pivot import DIALS, OTHER, PivotResult, ReportSpec

_BAR_CHARTS = ("Column", "Stacked column", "Bar", "Stacked bar")
MANUAL_COLOR_CHARTS = (*_BAR_CHARTS, "Line", "Area", "Donut", "Treemap")
SINGLE_COLOR = "*"                       # key for "every mark" on one-colour charts
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


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


def status_color_map(visible: list) -> dict | None:
    """Execution results/statuses get the status palette; anything else returns None."""
    keys = [str(v).lower() for v in visible if v != OTHER]
    if keys and all(k in STATUS_COLORS for k in keys) and not set(keys) <= {"yes", "no"}:
        return {v: STATUS_COLORS.get(str(v).lower(), OTHER_COLOR) for v in visible}
    return None


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
        update(type="date", tickformat=DATE_TICKS.get(spec.date_grain, "%b %Y"),
               hoverformat=DATE_HOVER.get(spec.date_grain, "%b %Y"))
    else:
        update(type="category", categoryorder="array", categoryarray=result.row_order)


def _bars(result: PivotResult, spec: ReportSpec, colors: dict, theme: Theme) -> go.Figure:
    horizontal = spec.chart in ("Bar", "Stacked bar")
    stacked = spec.chart.startswith("Stacked") or (spec.normalize and bool(spec.series))
    fmt, suffix = value_format(spec)
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
    fmt, suffix = value_format(spec)
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
    fmt, _ = value_format(spec)
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


def _heatmap(result: PivotResult, spec: ReportSpec, theme: Theme) -> go.Figure:
    fmt, suffix = value_format(spec)
    table = result.table.drop(columns=["Total"], errors="ignore")
    rows = [row_label(r, result, spec) for r in table.index]
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
    fmt, _ = value_format(spec)
    long = result.long
    row_totals = long.groupby(spec.rows, sort=False)["Value"].sum()
    ids, labels, parents, values, fills = [], [], [], [], []
    many = len(row_totals) > len(theme.palette)
    for row, total in row_totals.items():
        color = theme.palette[0] if many else colors.get(row, theme.palette[0])
        ids.append(f"r::{row}"); labels.append(row_label(row, result, spec))
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
    if spec.chart in DIALS:
        n = len(_gauge_values(result, spec))
        if spec.chart == "Meter":
            return {1: 340, 2: 300}.get(n, 250) if n <= 4 else 470
        return 300 if n <= 4 else 560
    if result.empty:
        return 220
    if spec.chart in ("Bar", "Stacked bar") and not result.row_is_date:
        return min(900, max(320, 30 * len(result.row_order) + 90))
    if spec.chart == "Heatmap":
        return min(900, max(320, 26 * len(result.row_order) + 110))
    return 360


# ── Colour choice: automatic, field defaults, manual ──────────────────────────

def resolve_colors(result: PivotResult, spec: ReportSpec, color_order=(), theme: Theme | None = None) -> dict:
    """The automatic colours: status colours for results, otherwise the theme palette per category
    ({} = one colour for every mark)."""
    theme = theme or THEMES[DEFAULT_THEME]
    statuses = status_color_map(result.series_order or ([] if result.row_is_date else result.row_order))
    if statuses and (result.series_order or spec.chart in ("Donut", "Treemap") or
                     (spec.color_by_category and spec.chart in _BAR_CHARTS)):
        return statuses
    if result.series_order:
        return color_map(result.series_order, color_order, theme.palette)
    if spec.chart in ("Donut", "Treemap") or (
            spec.color_by_category and spec.chart in _BAR_CHARTS and not result.row_is_date
            and len(result.row_order) <= len(theme.palette)):
        return color_map(result.row_order, color_order, theme.palette)
    return {}


def color_field(spec: ReportSpec, result: PivotResult) -> str:
    """The field whose values carry the colours ("" when the chart is a single colour)."""
    if result.series_order:
        return spec.series
    colored = spec.chart in ("Donut", "Treemap") or (spec.color_by_category and spec.chart in _BAR_CHARTS)
    return spec.rows if colored and not result.row_is_date else ""


def colorable(result: PivotResult, spec: ReportSpec, color_order=(), theme: str = DEFAULT_THEME) -> dict:
    """What the 🎨 Colours panel offers: category → automatic colour, or {"*": colour} for one-colour charts."""
    if spec.chart not in MANUAL_COLOR_CHARTS or result.empty:
        return {}
    t = THEMES.get(theme, THEMES[DEFAULT_THEME])
    return resolve_colors(result, spec, color_order, t) or {SINGLE_COLOR: t.palette[0]}


def effective_overrides(spec: ReportSpec, result: PivotResult, field_defaults: dict | None) -> dict:
    """Colours that replace the automatic ones: the field's saved defaults, then the report's manual picks."""
    field = color_field(spec, result)
    chosen = dict((field_defaults or {}).get(field, {})) if field else {}
    if spec.color_mode == "manual":
        chosen.update(spec.colors or {})
    return {str(k): v for k, v in chosen.items() if isinstance(v, str) and _HEX.match(v)}


def apply_overrides(colors: dict, spec: ReportSpec, overrides: dict | None) -> dict:
    if not overrides:
        return colors
    if not colors:                                   # one-colour chart: "*" paints every mark
        return {spec.value_label: overrides[SINGLE_COLOR]} if SINGLE_COLOR in overrides else colors
    return {k: overrides.get(str(k), v) for k, v in colors.items()}


def build_figure(result: PivotResult, spec: ReportSpec, color_order: list | tuple = (),
                 theme: str = DEFAULT_THEME, height: int | None = None,
                 overrides: dict | None = None) -> go.Figure:
    """Chart for every type except Number, which the UI renders as a stat tile.

    `height` overrides the natural height — the dashboard passes the tallest
    chart in a row so cards line up; the focus view passes a taller size.
    `overrides` (category → #rrggbb) replaces automatic colours — see effective_overrides."""
    if spec.chart == "Number":
        raise ValueError("Number reports render as stat tiles, not figures")
    if spec.chart == "Gauge":
        fig = _gauges(result, spec)
    elif spec.chart == "Meter":
        fig = _meters(result, spec)
    else:
        fig = _empty() if result.empty else _chart(result, spec, color_order,
                                                    THEMES.get(theme, THEMES[DEFAULT_THEME]), overrides)
    # Set explicitly — Streamlit repaints a paper colour that only comes from the template.
    fig.update_layout(paper_bgcolor=SURFACE, plot_bgcolor=SURFACE,
                      height=max(height or 0, natural_height(result, spec)))
    return fig


def _chart(result: PivotResult, spec: ReportSpec, color_order, theme: Theme, overrides: dict | None = None) -> go.Figure:
    colors = apply_overrides(resolve_colors(result, spec, color_order, theme), spec, overrides)

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
