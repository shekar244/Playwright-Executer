"""
Report Builder: configure a pivot + chart entirely from widgets, preview it
live, then save it to the dashboard.

Every control is bound to an `rb_*` session key, so loading a saved report
just writes its spec into session state before the widgets are drawn.
"""
from __future__ import annotations

import streamlit as st

from ..pivot import (AGGREGATIONS, CHART_TYPES, DATE_GRAINS, DIALS, SINGLE_VALUE, ReportSpec, clean_bands,
                     distinct_values)
from ..store import Store
from .common import Dataset, filter_editor, load_filters, render_report
from .style import accent_for, card_title

NEW = "__new__"
CHART_ICONS = {
    "Column": "📊", "Stacked column": "🧱", "Bar": "📶", "Stacked bar": "▤", "Line": "📈",
    "Area": "⛰️", "Donut": "🍩", "Heatmap": "🟦", "Treemap": "🔲", "Gauge": "⏲️", "Meter": "🧭",
    "Number": "🔢",
}
_BARS         = ("Column", "Stacked column", "Bar", "Stacked bar")
_NORMALIZABLE = ("Stacked column", "Stacked bar", "Area")
_CUMULATIVE   = ("Line", "Area", "Column", "Stacked column")


def _default_spec(ds: Dataset) -> ReportSpec:
    rows = next((c for c in ("Status", "Issue Type", "Result", "Cycle", "Priority") if c in ds.df.columns),
                ds.dimension_cols[0])
    return ReportSpec(name="New report", chart="Column", rows=rows)


def _load_state(spec: ReportSpec, ds: Dataset, marker: tuple) -> None:
    ss = st.session_state
    dims, nums = ds.dimension_cols, ds.numeric_cols
    ss.rb_name   = spec.name
    ss.rb_chart  = spec.chart if spec.chart in CHART_TYPES else "Column"
    whole_slice = spec.rows == "" and spec.chart in SINGLE_VALUE       # one dial / number for everything
    ss.rb_rows   = spec.rows if spec.rows in dims or whole_slice else _default_spec(ds).rows
    ss.rb_series = spec.series if spec.series in dims else ""
    ss.rb_value  = spec.value if spec.value in nums else ""
    ss.rb_agg    = spec.agg if spec.agg in AGGREGATIONS else "Count"
    ss.rb_grain  = spec.date_grain if spec.date_grain in DATE_GRAINS else "Month"
    ss.rb_top, ss.rb_fold, ss.rb_sort = int(spec.top_n), spec.fold_other, spec.sort
    ss.rb_labels, ss.rb_norm, ss.rb_cum = spec.show_labels, spec.normalize, spec.cumulative
    ss.rb_color = spec.color_by_category
    ss.rb_gmin, ss.rb_gmax = float(spec.gauge_min), float(spec.gauge_max)
    bands = clean_bands(spec.gauge_bands)
    ss.rb_gbands = len(bands) + 1
    ss.rb_gcut1, ss.rb_gcut2 = bands[0], bands[1]
    ss.rb_gcut3 = bands[2] if len(bands) == 3 else min(100.0, bands[1] + (100.0 - bands[1]) / 2)
    ss.rb_ghigher = bool(spec.higher_is_better)
    load_filters("rb_g", spec.gauge_where, ds)
    for stale in [k for k in ss if str(k).startswith(("rb_f_", "rb_fd_"))]:
        del ss[stale]
    ss.rb_filter_cols = [c for c in spec.filters if c in ds.df.columns]
    for col in ss.rb_filter_cols:
        wanted = spec.filters[col]
        if isinstance(wanted, dict):
            ss[f"rb_fd_{col}"] = int(wanted.get("last_days") or 30)
        else:
            ss[f"rb_f_{col}"] = [str(v) for v in wanted]
    ss.rb_loaded = marker


def _ensure(key: str, options: list, fallback) -> None:
    if st.session_state.get(key) not in options:
        st.session_state[key] = fallback


def _filters(ds: Dataset) -> dict:
    ss = st.session_state
    st.multiselect("Filter on", [c for c in ds.df.columns if c not in ("Key", "Summary")],
                   key="rb_filter_cols", placeholder="Choose fields…")
    filters: dict = {}
    for col in ss.rb_filter_cols:
        if col in ds.date_cols:
            ss.setdefault(f"rb_fd_{col}", 30)
            st.number_input(f"{col} · last N days", min_value=1, max_value=3650, step=1, key=f"rb_fd_{col}")
            filters[col] = {"last_days": int(ss[f"rb_fd_{col}"])}
            continue
        options = distinct_values(ds.df, col, ds.multi_cols)
        # Keep saved values that this dataset lacks (e.g. "Defect" when only "Bug" exists).
        options += [v for v in ss.get(f"rb_f_{col}", []) if v not in options]
        st.multiselect(col, options, key=f"rb_f_{col}", placeholder="Any value")
        if ss[f"rb_f_{col}"]:
            filters[col] = list(ss[f"rb_f_{col}"])
    return filters


def _dial_controls(ds: Dataset, chart: str) -> dict:
    """Gauge / Meter settings: completion condition, dial range and the red · amber · green bands."""
    ss = st.session_state
    with st.expander(f"{chart} settings", expanded=True):
        where = filter_editor(ds, "rb_g", "Counts as complete / success when…",
                              placeholder="Optional — e.g. Result = Passed or Status = Done shows a %")
        if where:
            st.caption("The dial shows the % of the slice that matches — 0 to 100.")
        c1, c2 = st.columns(2)
        c1.number_input("Dial minimum", step=1.0, key="rb_gmin", disabled=bool(where), format="%g",
                        help="e.g. 1 for a 1–5 rating scale.")
        c2.number_input("Dial maximum (0 = auto)", min_value=0.0, step=1.0, key="rb_gmax", disabled=bool(where),
                        format="%g")
        st.radio("Colour bands", [3, 4], key="rb_gbands", horizontal=True,
                 format_func=lambda n: "Red · Amber · Green" if n == 3 else "Red · Amber · Light green · Green")
        higher = st.radio("Better when the value is", [True, False], key="rb_ghigher", horizontal=True,
                          format_func=lambda b: "Higher" if b else "Lower")
        names = ["Red", "Amber", "Light green", "Green"] if ss.rb_gbands == 4 else ["Red", "Amber", "Green"]
        names = names if higher else names[::-1]
        st.caption("Band edges, as % of the dial (left → right):")
        cols = st.columns(ss.rb_gbands - 1)
        keys = ["rb_gcut1", "rb_gcut2", "rb_gcut3"][:ss.rb_gbands - 1]
        for col, key, name in zip(cols, keys, names):
            col.number_input(f"{name} ends at %", min_value=0.0, max_value=100.0, step=5.0, key=key, format="%g")
        cuts = [float(ss[k]) for k in keys]
        if cuts != sorted(cuts):
            st.warning("Band edges must increase left to right — they'll be sorted.")
        preview = " · ".join(f"{n} {a:g}–{b:g}%" for n, a, b in zip(names, [0.0, *sorted(cuts)], [*sorted(cuts), 100.0]))
        st.caption(preview)
    return dict(gauge_where=where, gauge_min=0.0 if where else float(ss.rb_gmin),
                gauge_max=0.0 if where else float(ss.rb_gmax), gauge_bands=clean_bands(cuts),
                higher_is_better=bool(higher))


def _controls(ds: Dataset) -> ReportSpec:
    ss = st.session_state
    dims, nums, dates = ds.dimension_cols, ds.numeric_cols, ds.date_cols
    _ensure("rb_rows", ["", *dims] if ss.get("rb_chart") in DIALS else dims, dims[0])
    _ensure("rb_series", ["", *dims], "")
    _ensure("rb_value", ["", *nums], "")

    st.text_input("Report name", key="rb_name")
    chart = st.pills("Chart type", CHART_TYPES, key="rb_chart", required=True,
                     format_func=lambda c: f"{CHART_ICONS[c]} {c}")
    if chart in DIALS:
        st.selectbox(f"{chart} per…", ["", *dims], key="rb_rows",
                     format_func=lambda c: c or f"— one {chart.lower()} for the whole slice —",
                     help="Pick a field for one dial per category (up to 8), like pass rate per cycle.")
    else:
        _ensure("rb_rows", dims, dims[0])
        st.selectbox("Rows · X-axis", dims, key="rb_rows", disabled=chart == "Number")
    st.selectbox("Columns" if chart == "Heatmap" else "Series · colour", ["", *dims], key="rb_series",
                 format_func=lambda c: c or "— none —", disabled=chart in ("Donut", "Number", *DIALS),
                 help="Splits each category into coloured series. More than 8 values fold into “Other”.")
    c1, c2 = st.columns(2)
    c1.selectbox("Measure", ["", *nums], key="rb_value", format_func=lambda c: c or "Issue count")
    c2.selectbox("Aggregation", list(AGGREGATIONS), key="rb_agg", disabled=not ss.rb_value)
    uses_date = chart != "Number" and any(c in dates for c in (ss.rb_rows, ss.rb_series) if c)
    st.selectbox("Date grain", list(DATE_GRAINS), key="rb_grain", disabled=not uses_date)

    gauge = _dial_controls(ds, chart) if chart in DIALS else {}

    with st.expander("Filters", expanded=bool(ss.get("rb_filter_cols"))):
        filters = _filters(ds)

    has_series = bool(ss.rb_series) and chart not in ("Donut", "Number", *DIALS)
    with st.expander("Options"):
        st.number_input("Top N categories (0 = all)", min_value=0, max_value=500, step=1, key="rb_top",
                        disabled=chart == "Number")
        st.checkbox("Group the rest as “Other”", key="rb_fold", disabled=not ss.rb_top)
        st.radio("Sort by", ["Value", "Label"], horizontal=True, key="rb_sort")
        st.checkbox("Show value labels", key="rb_labels")
        st.checkbox("Colour each category", key="rb_color",
                    disabled=has_series or chart not in _BARS,
                    help="Single-series bar charts: give every bar its own palette colour (up to 8).")
        st.checkbox("100 % stacked", key="rb_norm", disabled=chart not in _NORMALIZABLE or not has_series)
        st.checkbox("Cumulative (running total)", key="rb_cum", disabled=chart not in _CUMULATIVE)

    return ReportSpec(
        name=ss.rb_name.strip() or "Untitled report", chart=chart,
        rows=ss.rb_rows if chart != "Number" else "",
        series=ss.rb_series if has_series else "",
        value=ss.rb_value, agg=ss.rb_agg if ss.rb_value else "Count", date_grain=ss.rb_grain,
        filters=filters, top_n=int(ss.rb_top), fold_other=ss.rb_fold, sort=ss.rb_sort,
        show_labels=ss.rb_labels, color_by_category=ss.rb_color, **gauge,
        normalize=ss.rb_norm and chart in _NORMALIZABLE and has_series,
        cumulative=ss.rb_cum and chart in _CUMULATIVE,
    )


def _saved(report_id: str, message: str) -> None:
    ss = st.session_state
    ss.rb_pending_pick = report_id          # reopen exactly what was saved
    ss.rb_flash = message
    st.rerun()


def _actions(store: Store, spec: ReportSpec, pick: str, ds: Dataset) -> None:
    editing = pick != NEW
    c1, c2, c3 = st.columns([2, 1, 1])
    if c1.button("Save", icon=":material/save:", type="primary", width="stretch"):
        spec.id = pick if editing else ""
        saved = store.save_report(spec)
        if not editing:                              # new reports join this dataset's dashboard
            store.add_to_dashboard(ds.slug, saved.id)
        _saved(saved.id, f"Saved {saved.code} “{saved.name}” to the dashboard")
    if c2.button("", icon=":material/content_copy:", width="stretch", disabled=not editing,
                 help="Copy — save as a new report", key="rb-copy"):
        spec.id = ""
        spec.name = f"{spec.name} (copy)"
        saved = store.save_report(spec)
        store.add_to_dashboard(ds.slug, saved.id)
        _saved(saved.id, f"Copied as {saved.code} “{saved.name}”")
    if c3.button("", icon=":material/delete:", width="stretch", disabled=not editing,
                 help="Delete this report", key="rb-delete"):
        store.delete_report(pick)
        st.session_state.rb_pending_pick = NEW
        st.session_state.rb_flash = "Report deleted"
        st.rerun()


def render(store: Store, ds: Dataset) -> None:
    ss = st.session_state
    if "rb_flash" in ss:
        st.toast(ss.pop("rb_flash"), icon="✅")
    reports = {r.id: r for r in store.list_reports()}
    # Streamlit drops widget values while another view is shown, so the builder keeps its own
    # copies: rb_current (which report) and rb_draft (its unsaved edits).
    if "rb_pending_pick" in ss:                      # explicit open: ✎ on a card, after save / delete
        ss.rb_pick = ss.pop("rb_pending_pick")
        ss.pop("rb_loaded", None)
        ss.pop("rb_draft", None)
    elif "rb_pick" not in ss and ss.get("rb_current") in reports:
        ss.rb_pick = ss.rb_current
    _ensure("rb_pick", [NEW, *reports], NEW)

    left, right = st.columns([1.2, 2.4], gap="large")
    with left:
        pick = st.selectbox("Report", [NEW, *reports], key="rb_pick",
                            format_func=lambda i: "➕ New report" if i == NEW else reports[i].title)
        marker = (pick, ds.slug)
        if ss.get("rb_loaded") != marker or "rb_chart" not in ss:
            draft = ss.get("rb_draft") if ss.get("rb_loaded") == marker else None
            source = ReportSpec.from_dict(draft) if draft else \
                (reports[pick] if pick != NEW else _default_spec(ds))
            _load_state(source, ds, marker)
        spec = _controls(ds)
        if pick != NEW:
            spec.number = reports[pick].number
        ss.rb_current, ss.rb_draft = pick, spec.to_dict()
        _actions(store, spec, pick, ds)
    with right:
        accent = accent_for(pick if pick != NEW else spec.name)
        with st.container(key="card-builder-preview"):
            card_title(spec.name, accent, spec.code or "new")
            render_report(spec, ds, key="rb-preview", table="full", accent=accent)
