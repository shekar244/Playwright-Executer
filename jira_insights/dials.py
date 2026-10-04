"""
Dials — Gauge (a filled arc) and Meter (a needle over fixed colour bands).

Both show one dial for the slice or one per category (up to MAX_GAUGES). The
value is a completion % (0–100) when the report has a success condition, or
the plain measure on a gauge_min…gauge_max range. Colour bands are cut points
in % of the dial: red · amber · (light green ·) green, reversed when lower is better.
"""
from __future__ import annotations

import math

import plotly.graph_objects as go

from .chart_theme import (AXIS, CRITICAL, GAUGE_TRACK, GOOD, INK, INK_MUTED, LIGHT_GOOD, SURFACE, TEMPLATE,
                          WARNING, row_label, value_format)
from .pivot import MAX_GAUGES, PivotResult, ReportSpec


def _gauge_values(result: PivotResult, spec: ReportSpec) -> list[tuple[str, float]]:
    if spec.rows and not result.empty:
        rows = list(zip(result.long[spec.rows], result.long["Value"]))[:MAX_GAUGES]
        return [(row_label(r, result, spec), float(v)) for r, v in rows]
    return [("", float(result.total))]



def _nice_max(value: float) -> float:
    if value <= 0:
        return 100.0
    magnitude = 10 ** math.floor(math.log10(value * 1.15))
    return next(m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= value * 1.15)


def band_colors(spec: ReportSpec) -> list[str]:
    """Colour per band, left → right: red · amber · (light green ·) green — reversed when lower is better."""
    colors = [CRITICAL, WARNING, LIGHT_GOOD, GOOD] if len(spec.gauge_bands) == 3 else [CRITICAL, WARNING, GOOD]
    return colors if spec.higher_is_better else colors[::-1]


def dial_range(spec: ReportSpec, values: list[float]) -> tuple[float, float]:
    if spec.gauge_percent:
        return 0.0, 100.0
    low = float(spec.gauge_min or 0.0)
    high = float(spec.gauge_max) if spec.gauge_max and spec.gauge_max > low else \
        low + _nice_max(max(max(values, default=0.0) - low, 0.0))
    return low, high


def _completion(value: float, low: float, high: float) -> float:
    """Where the value sits on the dial, 0–100 %."""
    return min(100.0, max(0.0, (value - low) / (high - low) * 100)) if high > low else 0.0


def gauge_color(value: float, low: float, high: float, spec: ReportSpec) -> str:
    """The colour of the band the value falls in (bands are % of the dial)."""
    pct = _completion(value, low, high)
    band = sum(pct >= cut for cut in spec.gauge_bands)
    return band_colors(spec)[band]


def _dial_grid(n: int) -> tuple[int, int]:
    per_line = n if n <= 4 else math.ceil(n / 2)
    return per_line, math.ceil(n / per_line)


def _dial_format(spec: ReportSpec, values: list[float]) -> tuple[str, str]:
    if spec.gauge_percent:
        return ".1f", "%"                    # 69.7 % must not read as "70 %" in the wrong band
    fmt, _ = value_format(spec)
    small = max((abs(v) for v in values), default=0) < 10
    return (".1f" if small and fmt == ",.0f" and not all(float(v).is_integer() for v in values) else fmt), ""


def _gauges(result: PivotResult, spec: ReportSpec) -> go.Figure:
    values = _gauge_values(result, spec)
    low, high = dial_range(spec, [v for _, v in values])
    fmt, suffix = _dial_format(spec, [v for _, v in values])
    per_line, lines = _dial_grid(len(values))
    number_size = {1: 52, 2: 36, 3: 26}.get(per_line, 20)
    fig = go.Figure(layout=dict(template=TEMPLATE, margin=dict(l=24, r=24, t=30, b=10)))
    for i, (label, value) in enumerate(values):
        col, line = i % per_line, i // per_line
        x0, x1 = col / per_line + 0.03, (col + 1) / per_line - 0.03
        y1 = 1 - line / lines - (0.06 if lines > 1 else 0)
        y0 = 1 - (line + 1) / lines + 0.04
        fig.add_indicator(
            mode="gauge+number", value=value, domain=dict(x=[x0, x1], y=[y0, y1]),
            title=dict(text=label, font=dict(size=13 if per_line <= 2 else 11, color=INK)),
            number=dict(valueformat=fmt, suffix=suffix, font=dict(size=number_size, color=INK)),
            gauge=dict(
                axis=dict(range=[low, high], tickcolor=AXIS, tickwidth=1, tickfont=dict(color=INK_MUTED, size=10),
                          nticks=6),
                bar=dict(color=gauge_color(value, low, high, spec), thickness=0.32),
                bgcolor=GAUGE_TRACK, borderwidth=0,
                steps=[dict(range=[low, high], color=GAUGE_TRACK)],
            ),
        )
    return fig


def _meters(result: PivotResult, spec: ReportSpec) -> go.Figure:
    """Needle meters: fixed colour bands around a half circle, a needle at the value, the value below."""
    values = _gauge_values(result, spec)
    low, high = dial_range(spec, [v for _, v in values])
    fmt, suffix = _dial_format(spec, [v for _, v in values])
    per_line, lines = _dial_grid(len(values))
    number_size = {1: 34, 2: 26, 3: 20}.get(per_line, 16)
    cuts = [0.0, *spec.gauge_bands, 100.0]
    colors = band_colors(spec)
    ticks = [0, 45, 90, 135, 180]
    tick_text = [format(low + (high - low) * t / 180, fmt) for t in ticks]
    fig = go.Figure(layout=dict(template=TEMPLATE, margin=dict(l=20, r=20, t=26, b=26), showlegend=False))
    for i, (label, value) in enumerate(values):
        col, line = i % per_line, i // per_line
        x0, x1 = col / per_line + 0.015, (col + 1) / per_line - 0.015
        y1 = 1 - line / lines - 0.12 / lines
        y0 = 1 - (line + 1) / lines + 0.10 / lines
        polar = "polar" if i == 0 else f"polar{i + 1}"
        fig.update_layout(**{polar: dict(
            domain=dict(x=[x0, x1], y=[y0, y1]), sector=[0, 180], bgcolor="rgba(0,0,0,0)",
            radialaxis=dict(visible=False, range=[0, 1.3 if label else 1.0]),   # headroom for the label
            angularaxis=dict(rotation=180, direction="clockwise", tickmode="array", tickvals=ticks,
                             ticktext=tick_text, tickfont=dict(color=INK_MUTED, size=10), showline=False,
                             showgrid=False, ticks=""),
        )})
        # Bands: a ring from r=0.55 to 1, each wedge spanning its share of the half circle.
        fig.add_barpolar(
            subplot=polar, r=[0.45] * len(colors), base=[0.55] * len(colors),
            theta=[(a + b) / 2 * 1.8 for a, b in zip(cuts, cuts[1:])],
            width=[(b - a) * 1.8 for a, b in zip(cuts, cuts[1:])],
            marker=dict(color=colors, line=dict(color=SURFACE, width=3)), hoverinfo="skip",
        )
        angle = _completion(value, low, high) * 1.8
        text = format(value, fmt) + suffix
        fig.add_scatterpolar(
            subplot=polar, r=[0, 0.92], theta=[angle, angle], mode="lines",
            line=dict(color=INK, width=4), hovertemplate=f"<b>{text}</b><br>{label}<extra></extra>",
        )
        fig.add_scatterpolar(
            subplot=polar, r=[0], theta=[0], mode="markers+text", marker=dict(size=13, color=INK),
            text=[f"<b>{text}</b>"], textposition="bottom center", cliponaxis=False,
            textfont=dict(size=number_size, color=INK), hoverinfo="skip",
        )
        if label:   # anchored to the dial itself, so it always sits just above the arc
            fig.add_scatterpolar(
                subplot=polar, r=[1.18], theta=[90], mode="text", text=[label], cliponaxis=False,
                textfont=dict(size=13 if per_line <= 2 else 11, color=INK), hoverinfo="skip", name=label)
    return fig
