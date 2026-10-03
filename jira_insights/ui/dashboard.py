"""
Dashboard: headline KPI tiles plus every saved report rendered against the
active dataset, all under one global filter bar. Number reports form a
stat-tile row; charts follow in a two-column grid, each with a table view.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from ..store import Store
from .common import Dataset, filter_bar, render_report
from .style import accent_for, card_title, stat_tiles_html

_POINTS_COLUMNS = ("story points", "story point estimate")   # company- vs team-managed Jira


def _share(part: int, whole: int) -> str:
    return f"{part / whole:.0%} of issues" if whole else "—"


def kpi_tiles(df: pd.DataFrame, total: int) -> list[tuple[str, str, str, str, str]]:
    """(icon, label, value, caption, accent) for the current slice."""
    n = len(df)
    tiles = [("🧾", "Issues", f"{n:,}", f"of {total:,} in dataset" if n < total else "in this dataset", "#7ea8ff")]
    if "Open/Closed" in df.columns:
        opened = int((df["Open/Closed"] == "Open").sum())
        tiles.append(("🔓", "Open", f"{opened:,}", _share(opened, n), "#f0a070"))
    if "Issue Type" in df.columns:
        kind = df["Issue Type"].fillna("").astype(str).str.strip().str.lower()
        stories = int((kind == "story").sum())
        defects = int(kind.str.contains("bug|defect").sum())
        tiles += [("📘", "Stories", f"{stories:,}", _share(stories, n), "#7ecdc0"),
                  ("🐞", "Defects", f"{defects:,}", _share(defects, n), "#f07090")]
        if stories:
            tiles.append(("⚖️", "Defects per story", f"{defects / stories:.2f}", "defects ÷ stories", "#f0d080"))
    points = next((c for c in df.columns if c.lower() in _POINTS_COLUMNS), None)
    if points:
        values = pd.to_numeric(df[points], errors="coerce")
        caption = "total scope"
        if "Open/Closed" in df.columns:
            caption = f"{values[df['Open/Closed'] == 'Closed'].sum():,.0f} delivered"
        tiles.append(("🎯", "Story points", f"{values.sum():,.0f}", caption, "#b898f5"))
    return tiles


def open_in_builder(report_id: str) -> None:
    st.session_state["rb_pending_pick"] = report_id
    st.session_state["view_pending"] = "Report Builder"
    st.rerun()


def _card(report, ds: Dataset, df, table: str | None) -> None:
    accent = accent_for(report.id or report.name)
    with st.container(key=f"card-{report.id}"):
        head, edit = st.columns([8, 1], vertical_alignment="center")
        with head:
            card_title(report.name, accent)
        if edit.button("✎", key=f"edit-{report.id}", help="Edit in Report Builder", type="tertiary"):
            open_in_builder(report.id)
        render_report(report, ds, df=df, key=f"dash-{report.id}", table=table, accent=accent)


def render(store: Store, ds: Dataset) -> None:
    filtered = filter_bar(ds, key="dash")
    st.markdown(stat_tiles_html(kpi_tiles(filtered, len(ds.df))), unsafe_allow_html=True)
    reports = store.list_reports()
    columns = set(ds.df.columns)
    usable = [r for r in reports if not (r.required_columns() - columns)]

    if not reports:
        st.info("No saved reports yet — build one in **🧮 Report Builder** and save it.")
        return

    numbers = [r for r in usable if r.chart == "Number"]
    charts = [r for r in usable if r.chart != "Number"]
    for start in range(0, len(numbers), 4):
        row = numbers[start:start + 4]
        for col, report in zip(st.columns(4), row):
            with col:
                _card(report, ds, filtered, table=None)
    for start in range(0, len(charts), 2):
        for col, report in zip(st.columns(2, gap="medium"), charts[start:start + 2]):
            with col:
                _card(report, ds, filtered, table="expander")

    hidden = len(reports) - len(usable)
    if hidden:
        st.caption(f"{hidden} saved report(s) hidden — this dataset doesn't have the columns they use.")
