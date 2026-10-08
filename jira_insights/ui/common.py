"""
Shared Streamlit pieces: the active-dataset handle, the global filter bar,
and the report renderer used by both the builder and the dashboard.
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import streamlit as st

from ..charts import DEFAULT_THEME, PLOTLY_CONFIG, build_figure, effective_overrides, natural_height
from ..pivot import (SINGLE_VALUE, PivotResult, ReportSpec, apply_filters, build_pivot, category_rank,
                     distinct_values)
from ..store import Store, slugify
from .style import number_html

DATE_PRESETS = {"All time": None, "Last 7 days": 7, "Last 30 days": 30, "Last 90 days": 90,
                "Last 180 days": 180, "Last 365 days": 365, "Custom range": "custom"}

# One row per issue — useless as a pivot dimension.
ID_COLUMNS = {"Key", "Summary"}

_DATE_LABELS = {"Day": "%Y-%m-%d", "Week": "Wk of %Y-%m-%d", "Month": "%b %Y", "Quarter": "%Y-%m", "Year": "%Y"}


@dataclass
class Dataset:
    slug: str
    meta: dict
    df: pd.DataFrame

    @property
    def multi_cols(self) -> set[str]:
        return set(self.meta.get("multi_value_cols", []))

    @property
    def date_cols(self) -> list[str]:
        return [c for c in self.df.columns if pd.api.types.is_datetime64_any_dtype(self.df[c])]

    @property
    def numeric_cols(self) -> list[str]:
        return [c for c in self.df.columns
                if pd.api.types.is_numeric_dtype(self.df[c]) and not pd.api.types.is_bool_dtype(self.df[c])]

    @property
    def dimension_cols(self) -> list[str]:
        return [c for c in self.df.columns if c not in ID_COLUMNS]


@st.cache_data(show_spinner=False, max_entries=8)
def load_dataset(root: str, slug: str, version: str) -> tuple[pd.DataFrame, dict]:
    """`version` (fetched_at) is part of the cache key so a refresh invalidates it."""
    return Store(Path(root)).load_dataset(slug)


# ── Downloads ─────────────────────────────────────────────────────────────────

def download_buttons(df: pd.DataFrame, name: str, key: str, index: bool = False) -> None:
    excel = io.BytesIO()
    df.to_excel(excel, index=index, engine="openpyxl")
    c1, c2, _ = st.columns([1, 1, 3])
    c1.download_button("⬇ CSV", df.to_csv(index=index).encode("utf-8"), f"{slugify(name)}.csv",
                       "text/csv", key=f"{key}-csv", on_click="ignore", width="stretch")
    c2.download_button("⬇ Excel", excel.getvalue(), f"{slugify(name)}.xlsx",
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       key=f"{key}-xlsx", on_click="ignore", width="stretch")


# ── Global filter bar ─────────────────────────────────────────────────────────

def filter_bar(ds: Dataset, key: str) -> pd.DataFrame:
    """One row of filters above everything it scopes; returns the filtered frame."""
    c1, c2, c3 = st.columns([1, 1, 2.4])
    date_col = c1.selectbox("Date field", ["", *ds.date_cols], key=f"{key}_date_col",
                            format_func=lambda c: c or "Any date")
    preset = c2.selectbox("Date range", list(DATE_PRESETS), key=f"{key}_preset", disabled=not date_col)
    picked = c3.multiselect("Filter by", [c for c in ds.dimension_cols if c not in ds.date_cols],
                            key=f"{key}_dims", placeholder="Add filters…")

    filters: dict = {}
    window = DATE_PRESETS[preset] if date_col else None
    if window == "custom":
        rng = st.date_input("Custom range", value=(), key=f"{key}_range")
        if isinstance(rng, (list, tuple)) and len(rng) == 2:
            filters[date_col] = {"from": str(rng[0]), "to": str(rng[1])}
    elif window:
        filters[date_col] = {"last_days": window}

    if picked:
        cols = st.columns(min(len(picked), 4))
        for i, col in enumerate(picked):
            chosen = cols[i % 4].multiselect(col, distinct_values(ds.df, col, ds.multi_cols),
                                             key=f"{key}_f_{col}", placeholder="Any value")
            if chosen:
                filters[col] = chosen

    filtered = apply_filters(ds.df, filters, ds.multi_cols)
    if filters:
        st.caption(f"Showing {len(filtered):,} of {len(ds.df):,} issues")
    return filtered


# ── Report rendering ──────────────────────────────────────────────────────────

def _color_dimension(spec: ReportSpec) -> str:
    if spec.series and spec.series != spec.rows and spec.chart not in ("Donut", "Number"):
        return spec.series
    return spec.rows


def _display_table(result, spec: ReportSpec) -> pd.DataFrame:
    table = result.table.copy()
    if result.row_is_date:
        table.index = [ts.strftime(_DATE_LABELS.get(spec.date_grain, "%b %Y")) for ts in table.index]
    table.index.name = spec.rows or None
    table.columns = [str(c) for c in table.columns]
    if not spec.value or spec.agg in ("Count", "Distinct count"):
        decimals = 0
    elif spec.normalize and spec.series:
        decimals = 1
    else:
        all_int = all(float(v).is_integer() for v in table.values.flat if pd.notna(v))
        decimals = 0 if all_int else 1
    return table.round(decimals)


@dataclass
class PreparedReport:
    """A report's pivot, computed once so the dashboard can size a row before drawing it."""
    spec: ReportSpec
    result: PivotResult | None = None
    note: str = ""                       # why it can't be drawn, if result is None

    @property
    def height(self) -> int:
        if self.result is None or self.spec.chart == "Number":
            return 0
        return natural_height(self.result, self.spec)


def prepare_report(spec: ReportSpec, ds: Dataset, df: pd.DataFrame | None = None) -> PreparedReport:
    missing = sorted(spec.required_columns() - set(ds.df.columns))
    if missing:
        return PreparedReport(spec, note=f"This dataset has no {', '.join(missing)} column — pick another field.")
    if spec.chart not in SINGLE_VALUE and not spec.rows:
        return PreparedReport(spec, note="Choose a field for Rows · X-axis.")
    return PreparedReport(spec, build_pivot(ds.df if df is None else df, spec, ds.multi_cols))


def show_report(prepared: PreparedReport, ds: Dataset, *, key: str, table: str | None = None,
                accent: str = "#7ea8ff", height: int | None = None) -> None:
    """table: None | "expander" | "full" — every chart keeps a table-view twin."""
    spec, result = prepared.spec, prepared.result
    if result is None:
        st.info(prepared.note)
        return
    if spec.chart == "Number":
        if not spec.value or spec.agg in ("Count", "Distinct count"):
            decimals = 0
        elif float(result.total).is_integer():
            decimals = 0
        else:
            decimals = 1
        st.markdown(number_html(f"{result.total:,.{decimals}f}", spec.value_label, accent,
                                min_height=height + 56 if height else 0), unsafe_allow_html=True)
        return
    order = category_rank(ds.df, _color_dimension(spec), ds.multi_cols)
    overrides = effective_overrides(spec, result, st.session_state.get("ji_category_colors"))
    dark = st.session_state.get("ji_dark_mode", True)
    fig = build_figure(result, spec, order, theme=st.session_state.get("ji_theme", DEFAULT_THEME), height=height,
                       overrides=overrides, dark=dark)
    config = {**PLOTLY_CONFIG,
              "toImageButtonOptions": {**PLOTLY_CONFIG["toImageButtonOptions"], "filename": slugify(spec.title)}}
    st.plotly_chart(fig, key=key, theme=None, config=config)

    if table is None or result.empty:
        return
    view = _display_table(result, spec)
    if table == "expander":
        with st.expander("Table view"):
            st.dataframe(view, width="stretch")
    else:
        st.dataframe(view, width="stretch")
        download_buttons(view, spec.title, key=f"{key}-dl", index=True)


def render_report(spec: ReportSpec, ds: Dataset, *, key: str, df: pd.DataFrame | None = None,
                  table: str | None = None, accent: str = "#7ea8ff", height: int | None = None) -> None:
    show_report(prepare_report(spec, ds, df), ds, key=key, table=table, accent=accent, height=height)


# ── Generic filter editor (values · contains · last N days) ───────────────────

def load_filters(prefix: str, filters: dict, ds: Dataset) -> None:
    """Write a filters dict into the widget state used by filter_editor(prefix)."""
    ss = st.session_state
    for stale in [k for k in ss if str(k).startswith((f"{prefix}_fv_", f"{prefix}_fc_", f"{prefix}_fd_"))]:
        del ss[stale]
    ss[f"{prefix}_cols"] = [c for c in (filters or {}) if c in ds.df.columns]
    for col, wanted in (filters or {}).items():
        if isinstance(wanted, dict) and wanted.get("last_days"):
            ss[f"{prefix}_fd_{col}"] = int(wanted["last_days"])
        elif isinstance(wanted, dict) and wanted.get("contains"):
            ss[f"{prefix}_fc_{col}"] = str(wanted["contains"])
        elif isinstance(wanted, list):
            ss[f"{prefix}_fv_{col}"] = [str(v) for v in wanted]


def filter_editor(ds: Dataset, prefix: str, label: str, placeholder: str = "Choose fields…") -> dict:
    ss = st.session_state
    cols = st.multiselect(label, [c for c in ds.df.columns if c not in ID_COLUMNS],
                          key=f"{prefix}_cols", placeholder=placeholder)
    filters: dict = {}
    for col in cols:
        if col in ds.date_cols:
            ss.setdefault(f"{prefix}_fd_{col}", 30)
            filters[col] = {"last_days": int(st.number_input(f"{col} · last N days", 1, 3650, step=1,
                                                             key=f"{prefix}_fd_{col}"))}
            continue
        v, t = st.columns([3, 2])
        options = distinct_values(ds.df, col, ds.multi_cols)
        options += [x for x in ss.get(f"{prefix}_fv_{col}", []) if x not in options]
        values = v.multiselect(col, options, key=f"{prefix}_fv_{col}", placeholder="Any value")
        contains = t.text_input("…or contains", key=f"{prefix}_fc_{col}", placeholder="e.g. pass",
                                help="Case-insensitive text match; | for alternatives. Overrides the values list.")
        if contains.strip():
            filters[col] = {"contains": contains.strip()}
        elif values:
            filters[col] = list(values)
    return filters

