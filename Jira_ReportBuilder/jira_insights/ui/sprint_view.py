"""
Sprint Reports view — a fully editable dashboard with the same features as the
main Dashboard (✎ Edit tiles, 🧩 Reports, ✥ Arrange, ⛶ full-screen, ✎ edit in
Report Builder), plus sprint-specific built-in charts (burndown, velocity).

Reports are saved with the standard R-001 numbering and can be modified, laid
out, and maintained exactly like Dashboard reports.  Sprint datasets also get a
Sprint-001 report code for traceability.

For non-sprint datasets the tab shows a hint to create one from the sidebar.
"""
from __future__ import annotations

import hashlib
import html
import json

import plotly.graph_objects as go
import streamlit as st
from streamlit_sortables import sort_items

from .. import kpi, layout
from ..charts import GRID, INK, INK_MUTED, PLOTLY_CONFIG, TEMPLATE
from ..sprint_transform import compute_burndown, compute_velocity
from ..store import Store
from . import membership, tiles
from .builder import CHART_ICONS
from ..pivot import apply_filters, distinct_values
from .common import DATE_PRESETS, Dataset, download_buttons, prepare_report, show_report
from .style import SORTABLE_CSS, accent_for, card_title, stat_tiles_html

_LAYOUT_KEY = "sprint_layout"
_NEW_ROW = "＋ Drop here to start a new row"
_FOCUS_HEIGHT = 620


# ── Sprint filter bar (Sprint Name first, then date + dimension filters) ─────

def _sprint_filter_bar(ds: Dataset, key: str) -> "pd.DataFrame":
    """Like the global filter_bar but leads with a Sprint Name selector."""
    import pandas as pd

    has_sprint = "Sprint Name" in ds.df.columns
    has_epic = "Epic Name" in ds.df.columns

    # Row 1: Sprint + Epic + Date
    sprint_cols = []
    if has_sprint:
        sprint_cols.append("sprint")
    if has_epic:
        sprint_cols.append("epic")
    sprint_cols += ["date", "range"]

    widths = [1.2] * len(sprint_cols)
    if "date" in sprint_cols:
        # give date + range less space
        pass
    widths.append(2.0)  # extra filters multiselect

    cols = st.columns(len(sprint_cols) + 1)
    filters: dict = {}
    col_idx = 0

    # Sprint Name filter
    if has_sprint:
        sprint_values = distinct_values(ds.df, "Sprint Name", ds.multi_cols)
        chosen_sprints = cols[col_idx].multiselect(
            "🏃 Sprint", sprint_values, key=f"{key}_sprint",
            placeholder="All sprints",
        )
        if chosen_sprints:
            filters["Sprint Name"] = chosen_sprints
        col_idx += 1

    # Epic Name filter
    if has_epic:
        epic_values = distinct_values(ds.df, "Epic Name", ds.multi_cols)
        chosen_epics = cols[col_idx].multiselect(
            "📦 Epic", epic_values, key=f"{key}_epic",
            placeholder="All epics",
        )
        if chosen_epics:
            filters["Epic Name"] = chosen_epics
        col_idx += 1

    # Date field + range (optional, secondary)
    date_col = cols[col_idx].selectbox(
        "Date field", ["", *ds.date_cols], key=f"{key}_date_col",
        format_func=lambda c: c or "Any date",
    )
    col_idx += 1
    if "range" in sprint_cols:
        preset = cols[col_idx].selectbox(
            "Date range", list(DATE_PRESETS), key=f"{key}_preset",
            disabled=not date_col,
        )
        col_idx += 1
    else:
        preset = "All time"

    # Extra dimension filters
    exclude = {"Sprint Name", "Epic Name"} | set(ds.date_cols)
    extra_dims = [c for c in ds.dimension_cols if c not in exclude]
    picked = cols[col_idx].multiselect(
        "Filter by", extra_dims, key=f"{key}_dims",
        placeholder="Add filters…",
    )

    # Date filter
    window = DATE_PRESETS.get(preset) if date_col else None
    if window == "custom":
        rng = st.date_input("Custom range", value=(), key=f"{key}_range")
        if isinstance(rng, (list, tuple)) and len(rng) == 2:
            filters[date_col] = {"from": str(rng[0]), "to": str(rng[1])}
    elif window:
        filters[date_col] = {"last_days": window}

    # Extra dimension filters
    if picked:
        dim_cols = st.columns(min(len(picked), 4))
        for i, col in enumerate(picked):
            chosen = dim_cols[i % 4].multiselect(
                col, distinct_values(ds.df, col, ds.multi_cols),
                key=f"{key}_f_{col}", placeholder="Any value",
            )
            if chosen:
                filters[col] = chosen

    filtered = apply_filters(ds.df, filters, ds.multi_cols)
    if filters:
        st.caption(f"Showing {len(filtered):,} of {len(ds.df):,} sprint items")
    return filtered


# ── Sprint meta chips ────────────────────────────────────────────────────────

def _sprint_header(store: Store, ds: Dataset) -> str:
    """Render the sprint meta strip and return the Sprint-NNN code."""
    meta = ds.meta
    sprint_meta = meta.get("sprint") or {}

    seq = store.get_setting("sprint_report_seq") or 0
    ds_code = store.get_setting(f"sprint_code_{ds.slug}")
    if not ds_code:
        seq += 1
        ds_code = f"Sprint-{seq:03d}"
        store.set_setting("sprint_report_seq", seq)
        store.set_setting(f"sprint_code_{ds.slug}", ds_code)

    chips = []
    if sprint_meta.get("name"):
        chips.append(f"🏃 {sprint_meta['name']}")
    if sprint_meta.get("state"):
        chips.append(f"State: {sprint_meta['state']}")
    if sprint_meta.get("startDate"):
        chips.append(f"Start: {sprint_meta['startDate'][:10]}")
    if sprint_meta.get("endDate"):
        chips.append(f"End: {sprint_meta['endDate'][:10]}")
    if sprint_meta.get("goal"):
        chips.append(f"Goal: {sprint_meta['goal'][:80]}")
    if sprint_meta.get("mode") == "multi":
        names = sprint_meta.get("sprint_names", [])
        chips.append(f"{len(names)} sprints: {', '.join(names[:3])}"
                     + (f" +{len(names)-3}" if len(names) > 3 else ""))
    if chips:
        chip_html = "".join(f'<span class="ji-chip">{html.escape(c)}</span>' for c in chips)
        st.markdown(f'<div class="ji-chips">{chip_html}</div>', unsafe_allow_html=True)

    return ds_code


# ── KPI tile strip (uses the same store-backed tiles as Dashboard) ───────────

def _kpi_strip(store: Store, ds: Dataset, df) -> None:
    kpi_tiles = [t for t in store.list_kpis() if kpi.is_available(t, ds.df)]
    unit = "sprint items" if ds.meta.get("source") == "sprint" else "issues"
    cells = [(t.icon, t.label, *kpi.evaluate(t, df, len(ds.df), ds.multi_cols, unit=unit), t.accent)
             for t in kpi_tiles]
    if cells:
        st.markdown(stat_tiles_html(cells), unsafe_allow_html=True)


# ── Built-in sprint charts (burndown, velocity) ─────────────────────────────

def _burndown_chart(filtered, sprint_meta) -> go.Figure | None:
    burndown = compute_burndown(filtered, sprint_meta)
    if burndown.empty:
        return None
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=burndown["Date"], y=burndown["Ideal"], mode="lines", name="Ideal",
        line=dict(color=INK_MUTED, width=2, dash="dash"),
    ))
    fig.add_trace(go.Scatter(
        x=burndown["Date"], y=burndown["Remaining"], mode="lines+markers", name="Remaining",
        line=dict(color="#7ea8ff", width=2.5),
        marker=dict(size=5, color="#7ea8ff"),
        fill="tozeroy", fillcolor="rgba(126,168,255,0.08)",
    ))
    fig.update_layout(template=TEMPLATE, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      margin=dict(t=10, b=30, l=10, r=10),
                      xaxis=dict(gridcolor=GRID, tickfont=dict(color=INK_MUTED, size=10)),
                      yaxis=dict(gridcolor=GRID, tickfont=dict(color=INK_MUTED),
                                 title=dict(text="Story Points", font=dict(color=INK_MUTED, size=11))),
                      legend=dict(font=dict(color=INK, size=11)))
    return fig


def _velocity_chart(filtered, meta: dict) -> go.Figure | None:
    """Velocity chart with historical closed sprints + current sprint.

    The history comes from metadata saved at fetch time (velocity_history),
    so it doesn't require extra API calls at render time. The current sprint's
    velocity is computed live from the filtered issues.
    """
    import pandas as pd

    # Historical velocity from stored metadata
    history = meta.get("velocity_history") or []

    # Current sprint velocity from live data
    current = compute_velocity(filtered)

    sprints: list[str] = []
    committed: list[float] = []
    completed: list[float] = []
    is_current: list[bool] = []

    for h in history:
        sprints.append(h.get("name", ""))
        committed.append(h.get("committed", 0))
        completed.append(h.get("completed", 0))
        is_current.append(False)

    if not current.empty:
        for _, row in current.iterrows():
            name = str(row["Sprint"])
            if name not in sprints:
                sprints.append(name)
                committed.append(row["Committed"])
                completed.append(row["Completed"])
                is_current.append(True)

    if not sprints:
        return None

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=sprints, y=committed, name="Committed",
        marker=dict(color=["rgba(126,168,255,0.35)" if not cur else "rgba(126,168,255,0.7)"
                           for cur in is_current], cornerradius=4),
    ))
    fig.add_trace(go.Bar(
        x=sprints, y=completed, name="Completed",
        marker=dict(color=["rgba(95,212,160,0.5)" if not cur else "#5fd4a0"
                           for cur in is_current], cornerradius=4),
    ))

    # Average velocity line
    if len(completed) >= 2:
        avg = sum(completed) / len(completed)
        fig.add_hline(y=avg, line=dict(color=INK_MUTED, width=1.5, dash="dot"),
                      annotation=dict(text=f"Avg: {avg:.0f}", font=dict(color=INK_MUTED, size=10),
                                      showarrow=False, xanchor="left"))

    fig.update_layout(template=TEMPLATE, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      margin=dict(t=10, b=30, l=10, r=10), barmode="group",
                      xaxis=dict(tickfont=dict(color=INK, size=10)),
                      yaxis=dict(gridcolor=GRID, tickfont=dict(color=INK_MUTED),
                                 title=dict(text="Story Points", font=dict(color=INK_MUTED, size=11))),
                      legend=dict(font=dict(color=INK, size=11)))
    return fig


def _builtin_charts(filtered, sprint_meta, meta: dict) -> None:
    """Render the sprint-specific built-in charts that live outside the report system."""
    burndown_fig = _burndown_chart(filtered, sprint_meta)
    velocity_fig = _velocity_chart(filtered, meta)
    if not burndown_fig and not velocity_fig:
        return

    if burndown_fig and velocity_fig:
        c1, c2 = st.columns(2, gap="medium")
        with c1:
            with st.container(key="card-sprint-burndown"):
                card_title("Sprint Burndown", "#7ea8ff", "built-in")
                st.plotly_chart(burndown_fig, key="sprint-burndown", theme=None,
                                config=PLOTLY_CONFIG, use_container_width=True)
        with c2:
            with st.container(key="card-sprint-velocity"):
                card_title("Sprint Velocity", "#5fd4a0", "built-in")
                st.plotly_chart(velocity_fig, key="sprint-velocity", theme=None,
                                config=PLOTLY_CONFIG, use_container_width=True)
    elif burndown_fig:
        with st.container(key="card-sprint-burndown"):
            card_title("Sprint Burndown", "#7ea8ff", "built-in")
            st.plotly_chart(burndown_fig, key="sprint-burndown", theme=None,
                            config=PLOTLY_CONFIG, use_container_width=True)
    elif velocity_fig:
        with st.container(key="card-sprint-velocity"):
            card_title("Sprint Velocity", "#5fd4a0", "built-in")
            st.plotly_chart(velocity_fig, key="sprint-velocity", theme=None,
                            config=PLOTLY_CONFIG, use_container_width=True)


# ── Report rendering (same as Dashboard) ─────────────────────────────────────

def _open_in_builder(report_id: str) -> None:
    st.session_state["rb_pending_pick"] = report_id
    st.session_state["view_pending"] = "Report Builder"
    st.rerun()


def _focus_on(report_id: str | None) -> None:
    if report_id:
        st.session_state["sprint_focus"] = report_id
    else:
        st.session_state.pop("sprint_focus", None)
    st.rerun()


def _card_header(report, accent: str) -> None:
    head, expand, edit = st.columns([8, 1, 1], vertical_alignment="center")
    with head:
        card_title(report.name, accent, report.code)
    if expand.button("⛶", key=f"sf-{report.id}", help="Open this report full screen", type="tertiary"):
        _focus_on(report.id)
    if edit.button("✎", key=f"se-{report.id}", help="Edit in Report Builder", type="tertiary"):
        _open_in_builder(report.id)


def _row(reports: list, ds: Dataset, df) -> None:
    prepared = [prepare_report(r, ds, df) for r in reports]
    height = max((p.height for p in prepared), default=0) or None
    for col, report, prep in zip(st.columns(len(reports), gap="medium"), reports, prepared):
        with col:
            accent = accent_for(report.id or report.name)
            with st.container(key=f"scard-{report.id}"):
                _card_header(report, accent)
                show_report(prep, ds, key=f"sr-{report.id}", accent=accent, height=height,
                            table=None if report.chart == "Number" else "expander")


def _focus(report, ds: Dataset, df) -> None:
    if st.button("← Back to sprint reports", key="sprint-back", type="tertiary"):
        _focus_on(None)
    accent = accent_for(report.id or report.name)
    with st.container(key="scard-focus"):
        head, edit = st.columns([14, 1], vertical_alignment="center")
        with head:
            card_title(report.name, accent, report.code)
        if edit.button("✎", key=f"sfe-{report.id}", help="Edit in Report Builder", type="tertiary"):
            _open_in_builder(report.id)
        show_report(prepare_report(report, ds, df), ds, key=f"sfr-{report.id}", table="full",
                    accent=accent, height=_FOCUS_HEIGHT)


def _arrange(store: Store, ds: Dataset, rows: list[list[str]], reports: list) -> list[list[str]]:
    labels = layout.unique_labels(reports, CHART_ICONS)
    with st.container(key="scard-arrange"):
        head, reset = st.columns([6, 1.3], vertical_alignment="center")
        with head:
            card_title("Arrange sprint dashboard")
        if reset.button("↺ Reset layout", key="sprint-reset", width="stretch"):
            if store.get_dashboard(ds.slug) is not None:
                by_id = {r.id: r for r in reports}
                store.set_dashboard(ds.slug, layout.default_layout([by_id[i] for i in layout.members(rows)]))
            else:
                store.set_setting(_LAYOUT_KEY, None)
            st.rerun()
        st.caption("Drag reports within a row or between rows. Each row splits its width evenly — "
                   "one report is full width, four make quarter tiles. Changes save automatically.")
        version = hashlib.sha1(json.dumps(rows).encode()).hexdigest()[:10]
        edited = sort_items(layout.to_containers(rows, labels, _NEW_ROW), multi_containers=True,
                            direction="horizontal", custom_style=SORTABLE_CSS, key=f"sprint-arrange-{version}")
    return layout.from_containers(edited, labels)


# ── Main render ──────────────────────────────────────────────────────────────

def render(store: Store, ds: Dataset) -> None:
    meta = ds.meta
    is_sprint = meta.get("source") == "sprint"

    if not is_sprint:
        st.info("This dataset was not pulled from a sprint. Create a sprint dataset from the sidebar "
                "(**🏃 New dataset from Sprint**) to see sprint-specific reports here.")
        st.caption("You can still use the **📊 Dashboard** and **🧮 Report Builder** tabs for "
                   "general-purpose reports on this dataset.")
        return

    sprint_meta = meta.get("sprint") or {}
    ds_code = _sprint_header(store, ds)

    # Sprint filter bar (Sprint Name first, then epic, then date)
    filtered = _sprint_filter_bar(ds, key="sprint")

    # Reports: same system as Dashboard
    reports = store.list_reports()
    columns = set(ds.df.columns)
    usable = [r for r in reports if not (r.required_columns() - columns)]
    by_id = {r.id: r for r in usable}

    # Focused report (⛶ full screen)
    focused = by_id.get(st.session_state.get("sprint_focus", ""))
    if focused:
        _focus(focused, ds, filtered)
        return

    # Toolbar — same toggles as Dashboard
    _, edit_col, reports_col, arrange_col = st.columns([3.2, 1.2, 1.15, 1.1])
    editing = edit_col.toggle("✎ Edit tiles", key="sprint_edit_tiles",
                              help="Add, reorder and edit the KPI tiles and the data behind them")
    managing = reports_col.toggle("🧩 Reports", key="sprint_manage",
                                  help="Choose which saved reports this sprint's dashboard shows, "
                                       "or copy them from another dataset")
    arranging = arrange_col.toggle("✥ Arrange", key="sprint_arrange",
                                   help="Drag reports to reorder and resize this sprint dashboard")

    # KPI tiles (editable)
    _kpi_strip(store, ds, filtered)
    if editing:
        tiles.render(store, ds, filtered)

    # Built-in sprint charts (burndown + velocity) — always shown, not part of report system
    _builtin_charts(filtered, sprint_meta, meta)

    if not reports:
        st.info("No saved reports yet — build one in **🧮 Report Builder** and save it.")
        return

    # Report layout
    default_rows = store.get_setting(_LAYOUT_KEY)
    rows = membership.board_for(store, ds.slug, reports, columns, default_rows)
    if managing:
        membership.render(store, ds, reports, rows, default_rows)
    if arranging:
        edited = _arrange(store, ds, rows, usable)
        if edited and edited != rows:
            store.set_dashboard(ds.slug, edited)
            st.rerun()

    # Render report rows
    for row in rows:
        _row([by_id[i] for i in row], ds, filtered)

    if not rows:
        st.info("No reports on this dashboard — turn on **🧩 Reports** to add some.")
    shown = len(layout.members(rows))
    if shown < len(reports):
        st.caption(f"Showing {shown} of {len(reports)} saved reports — use 🧩 Reports to add others "
                   "that fit this dataset.")

    # Issues table at the bottom
    with st.expander(f"📋 Sprint Issues ({len(filtered):,})", expanded=False):
        display_cols = [c for c in ["Key", "Issue Type", "Summary", "Status", "Status Category",
                                     "Priority", "Assignee", "Story Points", "Sprint Name"]
                        if c in filtered.columns]
        st.dataframe(filtered[display_cols] if display_cols else filtered, use_container_width=True)
        download_buttons(filtered[display_cols] if display_cols else filtered, f"sprint-{ds_code}", "sprint-dl")
