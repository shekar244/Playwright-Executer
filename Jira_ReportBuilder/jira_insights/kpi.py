"""
KPI tiles — the headline numbers above the dashboard, defined as data so they
can be added, edited, reordered and deleted from the UI.

A tile shows one metric (count / sum / average … of a column over a filtered
slice of issues), optionally divided by a second metric (defects ÷ stories),
plus a caption: the metric's share of the current slice, the dataset size, a
second metric with a suffix ("785 delivered"), static text, or nothing.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field, fields as dc_fields

import pandas as pd

from .pivot import AGGREGATIONS, apply_filters

CAPTIONS = {"share": "Share of issues", "dataset": "Dataset size", "metric": "Second metric",
            "text": "Text", "none": "None"}

# Equivalent column names across Jira flavours (company- vs team-managed projects).
_COLUMN_ALIASES = {"story points": ("story point estimate",), "story point estimate": ("story points",)}


@dataclass
class Metric:
    value: str = ""                 # numeric column; "" = count of issues
    agg: str = "Count"
    filters: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data) -> "Metric | None":
        if not isinstance(data, dict):
            return None
        known = {f.name for f in dc_fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class KpiSpec:
    label: str = "New tile"
    icon: str = "📊"
    accent: str = "#7ea8ff"
    metric: Metric = field(default_factory=Metric)
    divide_by: Metric | None = None          # ratio tile: metric ÷ divide_by
    caption: str = "share"                   # key of CAPTIONS
    caption_text: str = ""                   # text, or the suffix after the caption metric
    caption_metric: Metric | None = None
    id: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "KpiSpec":
        data = dict(data or {})
        known = {f.name for f in dc_fields(cls)}
        spec = cls(**{k: v for k, v in data.items() if k in known and k not in ("metric", "divide_by", "caption_metric")})
        spec.metric = Metric.from_dict(data.get("metric")) or Metric()
        spec.divide_by = Metric.from_dict(data.get("divide_by"))
        spec.caption_metric = Metric.from_dict(data.get("caption_metric"))
        return spec

    def to_dict(self) -> dict:
        return asdict(self)

    def with_id(self) -> "KpiSpec":
        if not self.id:
            self.id = uuid.uuid4().hex[:10]
        return self

    def metrics(self) -> list[Metric]:
        return [m for m in (self.metric, self.divide_by,
                            self.caption_metric if self.caption == "metric" else None) if m]


_DEFECTS = {"Issue Type": {"contains": "bug|defect"}}
_STORIES = {"Issue Type": ["Story"]}

DEFAULT_TILES = [
    KpiSpec("Issues", "🧾", "#7ea8ff", Metric(), caption="dataset"),
    KpiSpec("Open", "🔓", "#f0a070", Metric(filters={"Open/Closed": ["Open"]})),
    KpiSpec("Stories", "📘", "#7ecdc0", Metric(filters=_STORIES)),
    KpiSpec("Defects", "🐞", "#f07090", Metric(filters=_DEFECTS)),
    KpiSpec("Defects per story", "⚖️", "#f0d080", Metric(filters=_DEFECTS),
            divide_by=Metric(filters=_STORIES), caption="text", caption_text="defects ÷ stories"),
    KpiSpec("Story points", "🎯", "#b898f5", Metric("Story Points", "Sum"), caption="metric",
            caption_text="delivered",
            caption_metric=Metric("Story Points", "Sum", {"Open/Closed": ["Closed"]})),
]


def default_tiles() -> list[KpiSpec]:
    return [KpiSpec.from_dict(t.to_dict()).with_id() for t in DEFAULT_TILES]


def resolve_column(df: pd.DataFrame, name: str) -> str | None:
    if not name:
        return None
    if name in df.columns:
        return name
    lower = {c.lower(): c for c in df.columns}
    for alias in (name.lower(), *_COLUMN_ALIASES.get(name.lower(), ())):
        if alias in lower:
            return lower[alias]
    return None


def is_available(spec: KpiSpec, df: pd.DataFrame) -> bool:
    """False when the dataset lacks a column the tile needs (the tile is hidden, not broken)."""
    for m in spec.metrics():
        if m.value and resolve_column(df, m.value) is None:
            return False
        if any(c not in df.columns for c in m.filters):
            return False
    return True


def metric_value(df: pd.DataFrame, m: Metric, multi_cols=(), now: pd.Timestamp | None = None) -> float:
    data = apply_filters(df, m.filters, multi_cols, now=now)
    if not m.value:
        return float(len(data))
    col = resolve_column(df, m.value)
    func = AGGREGATIONS.get(m.agg, "sum")
    series = data[col] if func == "nunique" else pd.to_numeric(data[col], errors="coerce")
    result = series.agg(func) if len(series) else 0
    return float(result) if pd.notna(result) else 0.0


def _fmt(value: float, ratio: bool) -> str:
    if ratio:
        return f"{value:,.2f}"
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.1f}"


def evaluate(spec: KpiSpec, df: pd.DataFrame, total_rows: int, multi_cols=(),
             now: pd.Timestamp | None = None) -> tuple[str, str]:
    """(value text, caption text) for the tile over the current slice `df`."""
    value = metric_value(df, spec.metric, multi_cols, now)
    if spec.divide_by:
        denominator = metric_value(df, spec.divide_by, multi_cols, now)
        text = _fmt(value / denominator, ratio=True) if denominator else "—"
    else:
        text = _fmt(value, ratio=False)

    n = len(df)
    if spec.caption == "share":
        caption = f"{value / n:.0%} of issues" if n and not spec.metric.value else ""
    elif spec.caption == "dataset":
        caption = f"of {total_rows:,} in dataset" if n < total_rows else "in this dataset"
    elif spec.caption == "metric" and spec.caption_metric:
        second = metric_value(df, spec.caption_metric, multi_cols, now)
        caption = f"{_fmt(second, ratio=False)} {spec.caption_text}".strip()
    elif spec.caption == "text":
        caption = spec.caption_text
    else:
        caption = ""
    return text, caption
