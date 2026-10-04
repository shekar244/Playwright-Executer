"""
Dashboard: headline KPI tiles (✎ Edit tiles) plus every saved report, all
under one global filter bar. Reports sit in rows that split the width evenly and share one
chart height so cards line up; ✥ Arrange lets users drag reports within and
between rows (saved to the workspace), and ⛶ opens a single report full screen.
"""
from __future__ import annotations

import hashlib
import json

import streamlit as st
from streamlit_sortables import sort_items

from .. import kpi, layout
from ..store import Store
from .builder import CHART_ICONS
from . import tiles
from .common import Dataset, filter_bar, prepare_report, show_report
from .style import SORTABLE_CSS, accent_for, card_title, stat_tiles_html

_LAYOUT_KEY = "dashboard_layout"
_NEW_ROW = "＋ Drop here to start a new row"
_FOCUS_HEIGHT = 620

def _kpi_strip(store: Store, ds: Dataset, df) -> None:
    tiles = [t for t in store.list_kpis() if kpi.is_available(t, ds.df)]
    cells = [(t.icon, t.label, *kpi.evaluate(t, df, len(ds.df), ds.multi_cols), t.accent) for t in tiles]
    if cells:
        st.markdown(stat_tiles_html(cells), unsafe_allow_html=True)


def open_in_builder(report_id: str) -> None:
    st.session_state["rb_pending_pick"] = report_id
    st.session_state["view_pending"] = "Report Builder"
    st.rerun()


def _focus_on(report_id: str | None) -> None:
    if report_id:
        st.session_state["dash_focus"] = report_id
    else:
        st.session_state.pop("dash_focus", None)
    st.rerun()


def _card_header(report, accent: str) -> None:
    head, expand, edit = st.columns([8, 1, 1], vertical_alignment="center")
    with head:
        card_title(report.name, accent)
    if expand.button("⛶", key=f"focus-{report.id}", help="Open this report full screen", type="tertiary"):
        _focus_on(report.id)
    if edit.button("✎", key=f"edit-{report.id}", help="Edit in Report Builder", type="tertiary"):
        open_in_builder(report.id)


def _row(reports: list, ds: Dataset, df) -> None:
    """One dashboard row: equal widths, and every chart at the tallest chart's height."""
    prepared = [prepare_report(r, ds, df) for r in reports]
    height = max((p.height for p in prepared), default=0) or None
    for col, report, prep in zip(st.columns(len(reports), gap="medium"), reports, prepared):
        with col:
            accent = accent_for(report.id or report.name)
            with st.container(key=f"card-{report.id}"):
                _card_header(report, accent)
                show_report(prep, ds, key=f"dash-{report.id}", accent=accent, height=height,
                            table=None if report.chart == "Number" else "expander")


def _focus(report, ds: Dataset, df) -> None:
    """A single report across the whole page: taller chart, full table and downloads."""
    if st.button("← Back to dashboard", key="dash-back", type="tertiary"):
        _focus_on(None)
    accent = accent_for(report.id or report.name)
    with st.container(key="card-focus"):
        head, edit = st.columns([14, 1], vertical_alignment="center")
        with head:
            card_title(report.name, accent)
        if edit.button("✎", key=f"focus-edit-{report.id}", help="Edit in Report Builder", type="tertiary"):
            open_in_builder(report.id)
        show_report(prepare_report(report, ds, df), ds, key=f"focus-{report.id}", table="full",
                    accent=accent, height=_FOCUS_HEIGHT)


def _arrange(store: Store, rows: list[list[str]], reports: list) -> list[list[str]]:
    """Drag-and-drop editor: each row is a group; dropping on the last group starts a new row."""
    labels = layout.unique_labels(reports, CHART_ICONS)
    with st.container(key="card-arrange"):
        head, reset = st.columns([6, 1.3], vertical_alignment="center")
        with head:
            card_title("Arrange dashboard")
        if reset.button("↺ Reset layout", key="dash-reset", width="stretch"):
            store.set_setting(_LAYOUT_KEY, None)
            st.rerun()
        st.caption("Drag reports within a row or between rows. Each row splits its width evenly — "
                   "one report is full width, four make quarter tiles. Changes save automatically.")
        # Keyed by the layout itself: after a save the component remounts with the saved rows
        # (and a fresh empty "new row" group) instead of keeping its stale drag state.
        version = hashlib.sha1(json.dumps(rows).encode()).hexdigest()[:10]
        edited = sort_items(layout.to_containers(rows, labels, _NEW_ROW), multi_containers=True,
                            direction="horizontal", custom_style=SORTABLE_CSS, key=f"dash-arrange-{version}")
    return layout.from_containers(edited, labels)


def render(store: Store, ds: Dataset) -> None:
    filtered = filter_bar(ds, key="dash")
    reports = store.list_reports()
    columns = set(ds.df.columns)
    usable = [r for r in reports if not (r.required_columns() - columns)]
    by_id = {r.id: r for r in usable}

    focused = by_id.get(st.session_state.get("dash_focus", ""))
    if focused:
        _focus(focused, ds, filtered)
        return

    _, edit_col, arrange_col = st.columns([4.2, 1.2, 1.1])
    editing = edit_col.toggle("✎ Edit tiles", key="dash_edit_tiles",
                              help="Add, reorder and edit the KPI tiles and the data behind them")
    arranging = arrange_col.toggle("✥ Arrange", key="dash_arrange",
                                   help="Drag reports to reorder and resize the dashboard")
    _kpi_strip(store, ds, filtered)
    if editing:
        tiles.render(store, ds, filtered)
    if not reports:
        st.info("No saved reports yet — build one in **🧮 Report Builder** and save it.")
        return

    saved = layout.normalize(store.get_setting(_LAYOUT_KEY), reports)
    rows = layout.visible_rows(saved, set(by_id))
    if arranging:
        edited = _arrange(store, rows, usable)
        if edited and edited != rows:
            store.set_setting(_LAYOUT_KEY, layout.merge_hidden(edited, saved, set(by_id)))
            st.rerun()

    for row in rows:
        _row([by_id[i] for i in row], ds, filtered)

    hidden = len(reports) - len(usable)
    if hidden:
        st.caption(f"{hidden} saved report(s) hidden — this dataset doesn't have the columns they use.")
