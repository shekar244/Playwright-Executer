"""
Pivot engine behind the report builder — pure pandas, no Streamlit.

A ReportSpec fully describes one report (data slice + pivot + chart options)
and round-trips through JSON, so a report built in the UI can be saved and
re-rendered on the dashboard against any dataset that has its columns.

Filters:  {"Issue Type": ["Bug", "Story"]}          categorical — any of
          {"Issue Type": {"contains": "bug|defect"}} text — case-insensitive regex
          {"Created": {"last_days": 90}}            date — relative window
          {"Created": {"from": "2026-01-01", "to": "2026-03-31"}}
"""
from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass, field, fields as dc_fields, replace

import pandas as pd

from .transform import MULTI_SEP

AGGREGATIONS = {
    "Count": "count", "Sum": "sum", "Average": "mean", "Median": "median",
    "Min": "min", "Max": "max", "Distinct count": "nunique",
}
DATE_GRAINS = {"Day": "D", "Week": "W", "Month": "M", "Quarter": "Q", "Year": "Y"}
CHART_TYPES = ("Column", "Stacked column", "Bar", "Stacked bar", "Line", "Area",
               "Donut", "Heatmap", "Treemap", "Gauge", "Meter", "Number")
DIALS = ("Gauge", "Meter")                 # arc gauge · needle meter with colour bands
SINGLE_VALUE = ("Number", *DIALS)          # rows optional: one value, or one dial per category

OTHER      = "Other"
NONE_LABEL = "(none)"
MAX_SERIES = 8    # size of the categorical palette — the tail folds into "Other"
MAX_SLICES = 6    # donut readability limit
MAX_GAUGES = 8    # gauges side by side before it stops reading as a glance

# Semantic orders used when sorting by label (instead of alphabetical).
_KNOWN_ORDERS = [
    ["Blocker", "Highest", "Critical", "High", "Major", "Medium", "Minor", "Low", "Lowest", "Trivial"],
    ["To Do", "In Progress", "Done"],
    ["Open", "Closed"],
    ["Passed", "Failed", "Blocked", "In progress", "Not run", "Other"],
    ["Pass", "Fail", "WIP", "Blocked", "Unexecuted"],
    ["Yes", "No"],
]

_VAL = "__value__"


def clean_bands(cuts) -> list[float]:
    """2 or 3 ascending cut points within 0–100 (falls back to 50 / 80)."""
    try:
        values = sorted(min(100.0, max(0.0, float(c))) for c in (cuts or []))
    except (TypeError, ValueError):
        values = []
    return values[:3] if len(values) >= 2 else [50.0, 80.0]


@dataclass
class ReportSpec:
    name: str = "Untitled report"
    chart: str = "Column"
    rows: str = ""                  # category / x-axis dimension ("" only for Number)
    series: str = ""                # colour / heatmap-column dimension ("" = none)
    value: str = ""                 # numeric measure column ("" = count issues)
    agg: str = "Count"
    date_grain: str = "Month"
    filters: dict = field(default_factory=dict)
    top_n: int = 0                  # keep the N largest row categories (0 = all)
    fold_other: bool = True         # fold the rest into "Other" instead of dropping it
    sort: str = "Value"             # "Value" | "Label"
    normalize: bool = False         # each row category as 100 %
    cumulative: bool = False        # running total along the rows
    show_labels: bool = False
    color_by_category: bool = True  # single-series bars: one palette colour per category
    # Gauge / Meter: with `gauge_where` the value is the % of the slice matching it (completion,
    # pass rate) on a 0–100 dial; otherwise the plain aggregate on a gauge_min…gauge_max dial.
    gauge_where: dict = field(default_factory=dict)
    gauge_min: float = 0.0
    gauge_max: float = 0.0           # 0 = automatic
    gauge_bands: list = field(default_factory=lambda: [50.0, 80.0])   # cut points, % of the dial:
    #   2 cuts → red · amber · green;  3 cuts → red · amber · light green · green
    higher_is_better: bool = True
    number: int = 0                  # permanent, user-facing report number (R-001…), assigned by the store
    id: str = ""

    @classmethod
    def from_dict(cls, data: dict | None) -> "ReportSpec":
        data = dict(data or {})
        if "gauge_bands" not in data and ("gauge_warn" in data or "gauge_good" in data):
            data["gauge_bands"] = [data.get("gauge_warn", 50.0), data.get("gauge_good", 80.0)]   # older reports
        known = {f.name for f in dc_fields(cls)}
        spec = cls(**{k: v for k, v in data.items() if k in known})
        spec.gauge_bands = clean_bands(spec.gauge_bands)
        return spec

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def code(self) -> str:
        return f"R-{int(self.number):03d}" if self.number else ""

    @property
    def title(self) -> str:
        return f"{self.code} · {self.name}" if self.code else self.name

    def with_id(self) -> "ReportSpec":
        if not self.id:
            self.id = uuid.uuid4().hex[:10]
        return self

    def required_columns(self) -> set[str]:
        gauge = set(self.gauge_where) if self.chart in DIALS else set()
        return {c for c in (self.rows, self.series, self.value) if c} | set(self.filters) | gauge

    @property
    def gauge_percent(self) -> bool:
        return self.chart in DIALS and bool(self.gauge_where)

    @property
    def additive(self) -> bool:
        return not self.value or self.agg in ("Count", "Sum")

    @property
    def value_label(self) -> str:
        if self.gauge_percent:
            return "% of " + (f"{self.agg.lower()} of {self.value}" if self.value else "issues")
        if not self.value:
            return "Issues"
        return f"{self.agg} of {self.value}"


@dataclass
class PivotResult:
    long: pd.DataFrame                  # [rows, (series), "Value"] in display order
    table: pd.DataFrame                 # rows × series (+ Total) for the table view
    row_order: list
    series_order: list
    total: float                        # whole-slice aggregate (Number chart)
    row_is_date: bool = False

    @property
    def empty(self) -> bool:
        return self.long.empty


# ── Filters ───────────────────────────────────────────────────────────────────

def _labels(s: pd.Series) -> pd.Series:
    """String labels for grouping/filtering: 3.0 → '3', NaN → '(none)'."""
    def fmt(v):
        if v is None or (not isinstance(v, str) and pd.isna(v)):
            return NONE_LABEL
        if isinstance(v, float) and v.is_integer():
            return str(int(v))
        return str(v)
    return s.map(fmt).astype(object)


def apply_filters(df: pd.DataFrame, filters: dict | None, multi_cols=(),
                  now: pd.Timestamp | None = None) -> pd.DataFrame:
    mask = pd.Series(True, index=df.index)
    for col, wanted in (filters or {}).items():
        if col not in df.columns or not wanted:
            continue
        s = df[col]
        if isinstance(wanted, dict):
            if wanted.get("contains"):
                mask &= _labels(s).str.contains(str(wanted["contains"]), case=False, regex=True, na=False)
                continue
            if not pd.api.types.is_datetime64_any_dtype(s):
                continue
            if wanted.get("last_days"):
                now = now if now is not None else pd.Timestamp.now()
                mask &= s >= now.normalize() - pd.Timedelta(days=int(wanted["last_days"]))
            if wanted.get("from"):
                mask &= s >= pd.Timestamp(wanted["from"])
            if wanted.get("to"):
                mask &= s < pd.Timestamp(wanted["to"]) + pd.Timedelta(days=1)
            continue
        allowed = {str(w) for w in wanted}
        if col in multi_cols:
            tokens = s.fillna("").astype(str).str.split(MULTI_SEP)
            mask &= tokens.map(lambda t: bool(allowed & ({x for x in t if x} or {NONE_LABEL})))
        else:
            mask &= _labels(s).isin(allowed)
    return df[mask]


def distinct_values(df: pd.DataFrame, col: str, multi_cols=(), limit: int = 1000) -> list[str]:
    s = df[col]
    if col in multi_cols:
        s = s.fillna("").astype(str).str.split(MULTI_SEP).explode().replace("", None)
    return sorted(_labels(s).unique().tolist(), key=_label_key)[:limit]


# ── Ordering helpers ──────────────────────────────────────────────────────────

def _natural(value) -> list:
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", str(value))]


def _label_key(value) -> tuple:
    return (value in (OTHER, NONE_LABEL), _natural(value))


def _known_order(labels: list) -> list | None:
    for known in _KNOWN_ORDERS:
        rank = {v.lower(): i for i, v in enumerate(known)}
        if all(str(l).lower() in rank or l in (OTHER, NONE_LABEL) for l in labels):
            return sorted(labels, key=lambda l: (l in (OTHER, NONE_LABEL), rank.get(str(l).lower(), 0)))
    return None


def _label_order(labels: list) -> list:
    return _known_order(labels) or sorted(labels, key=_label_key)


def category_rank(df: pd.DataFrame, col: str, multi_cols=()) -> list:
    """Categories by frequency in the *unfiltered* dataset — gives stable colours."""
    if not col or col not in df.columns or pd.api.types.is_datetime64_any_dtype(df[col]):
        return []
    s = df[col]
    if col in multi_cols:
        s = s.fillna("").astype(str).str.split(MULTI_SEP).explode().replace("", None)
    return _labels(s).value_counts().index.tolist()


# ── Pivot ─────────────────────────────────────────────────────────────────────

def _prepare_dim(work: pd.DataFrame, col: str, grain: str, multi: bool) -> pd.DataFrame:
    s = work[col]
    if pd.api.types.is_datetime64_any_dtype(s):
        work = work[s.notna()].copy()
        work[col] = work[col].dt.to_period(DATE_GRAINS.get(grain, "M")).dt.start_time
        return work
    if multi:
        work = work.copy()
        work[col] = s.fillna("").astype(str).str.split(MULTI_SEP)
        work = work.explode(col)
        work[col] = work[col].replace("", None)
    work = work.copy()
    work[col] = _labels(work[col])
    return work


def _aggregate(work: pd.DataFrame, dims: list[str], spec: ReportSpec) -> pd.Series:
    grouped = work.groupby(dims, sort=False, dropna=False)
    if not spec.value:
        return grouped.size().astype(float)
    return grouped[_VAL].agg(AGGREGATIONS.get(spec.agg, "count")).astype(float)


def _overall(work: pd.DataFrame, spec: ReportSpec) -> float:
    if not spec.value:
        return float(len(work))
    value = work[_VAL].agg(AGGREGATIONS.get(spec.agg, "count"))
    return float(value) if pd.notna(value) else 0.0


def _fold(work: pd.DataFrame, col: str, spec: ReportSpec, keep: int, fold: bool = True) -> pd.DataFrame:
    ranking = _aggregate(work, [col], spec).sort_values(ascending=False)
    if len(ranking) <= keep + (1 if fold else 0):
        return work
    top = set(ranking.index[:keep])
    work = work.copy()
    if fold:
        work[col] = work[col].where(work[col].isin(top), OTHER)
        return work
    return work[work[col].isin(top)]


def build_pivot(df: pd.DataFrame, spec: ReportSpec, multi_cols=(),
                now: pd.Timestamp | None = None) -> PivotResult:
    data = apply_filters(df, spec.filters, multi_cols, now=now)
    work = pd.DataFrame(index=data.index)
    if spec.value:
        raw = data[spec.value]
        work[_VAL] = raw if spec.agg == "Distinct count" else pd.to_numeric(raw, errors="coerce")
    total = _overall(work, spec)

    if spec.gauge_percent:
        return _gauge_percent(df, spec, multi_cols, now)
    rows = spec.rows if spec.chart != "Number" else ""
    series = spec.series if rows and spec.chart not in ("Donut", *SINGLE_VALUE) else ""
    series = "" if series == rows else series
    dims = [d for d in (rows, series) if d]
    if not dims:
        return PivotResult(pd.DataFrame(), pd.DataFrame({spec.value_label: [total]}), [], [], total)

    for d in dims:
        work[d] = data[d]
    for d in dims:
        work = _prepare_dim(work, d, spec.date_grain, d in multi_cols)
    row_is_date = pd.api.types.is_datetime64_any_dtype(work[rows])

    if not row_is_date and spec.top_n:
        work = _fold(work, rows, spec, spec.top_n, spec.fold_other)
    if spec.chart == "Donut":
        work = _fold(work, rows, spec, MAX_SLICES - 1)
    if series:
        work = _fold(work, series, spec, MAX_SERIES - 1)
    if work.empty:
        return PivotResult(pd.DataFrame(), pd.DataFrame(), [], [], total, row_is_date)

    agg = _aggregate(work, dims, spec)
    wide = agg.unstack(series) if series else agg.to_frame(spec.value_label)

    if row_is_date:
        wide = wide.sort_index()
        if spec.additive:     # show empty periods as zero, not as a gap
            freq = DATE_GRAINS.get(spec.date_grain, "M")
            full = pd.period_range(wide.index.min(), wide.index.max(), freq=freq).start_time
            wide = wide.reindex(full)
            wide.index.name = rows
    elif spec.sort == "Label":
        wide = wide.loc[_label_order(list(wide.index))]
    else:
        totals = wide.sum(axis=1)
        order = sorted(wide.index, key=lambda r: (r == OTHER, -totals[r]))
        wide = wide.loc[order]

    if series:
        col_totals = wide.sum(axis=0)   # semantic order (To Do → Done) beats size for stacking
        wide = wide[_known_order(list(wide.columns))
                    or sorted(wide.columns, key=lambda c: (c == OTHER, -col_totals[c]))]
    if spec.additive or spec.cumulative:
        wide = wide.fillna(0)
    if spec.cumulative:
        wide = wide.cumsum()
    if spec.normalize and series:
        wide = wide.div(wide.sum(axis=1).replace(0, float("nan")), axis=0) * 100

    row_order = list(wide.index)
    series_order = list(wide.columns) if series else []
    if series:
        long = wide.rename_axis(index=rows, columns=None).reset_index().melt(
            id_vars=rows, var_name=series, value_name="Value").dropna(subset=["Value"])
    else:
        long = wide.rename(columns={spec.value_label: "Value"}).reset_index()

    table = wide.copy()
    if series and spec.additive and not (spec.normalize or spec.cumulative):
        table["Total"] = table.sum(axis=1)
    return PivotResult(long, table, row_order, series_order, total, row_is_date)


def _gauge_percent(df: pd.DataFrame, spec: ReportSpec, multi_cols, now) -> PivotResult:
    plain = replace(spec, gauge_where={})
    base = build_pivot(df, plain, multi_cols, now)
    hits = build_pivot(df, replace(plain, filters={**spec.filters, **spec.gauge_where}), multi_cols, now)
    total = (hits.total / base.total * 100) if base.total else 0.0
    if base.empty:
        return PivotResult(base.long, base.table, [], [], total, base.row_is_date)
    label = spec.value_label
    hit_by_row = dict(zip(hits.long[spec.rows], hits.long["Value"])) if not hits.empty else {}
    long = base.long.copy()
    long["Value"] = [(hit_by_row.get(r, 0.0) / v * 100) if v else 0.0 for r, v in zip(long[spec.rows], long["Value"])]
    table = long.set_index(spec.rows).rename(columns={"Value": label})
    return PivotResult(long, table, base.row_order, [], total, base.row_is_date)

