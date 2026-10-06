"""
Sprint Reports view — sprint-specific dashboard with burndown, velocity,
completion rate, and carry-over analysis.

Shown as a tab alongside Dashboard, Report Builder, Explorer, Data when the
active dataset is a sprint dataset (source="sprint"). For non-sprint datasets
shows a hint to create one from the sidebar.

Report numbering: Sprint-001, Sprint-002, …
"""
from __future__ import annotations

import streamlit as st
import plotly.graph_objects as go

from ..charts import (DEFAULT_THEME, PLOTLY_CONFIG, SURFACE, TEMPLATE, THEMES,
                      GRID, INK, INK_MUTED, HOVER_BG, GOOD, WARNING, SERIOUS, CRITICAL)
from ..sprint_transform import compute_burndown, compute_velocity
from .common import Dataset, filter_bar
from .style import accent_for, card_title, stat_tiles_html

_SPRINT_PALETTE = ('#7ea8ff', '#5fd4a0', '#f0d080', '#8e98bc', '#f07090', '#b898f5', '#7ecdc0', '#f0a070')


def _theme_palette():
    name = st.session_state.get("ji_theme", DEFAULT_THEME)
    return THEMES.get(name, THEMES[DEFAULT_THEME]).palette


def _sprint_kpis(df, filtered) -> None:
    total = len(filtered)
    done = filtered.loc[filtered.get("Status Category", "").astype(str).str.lower() == "done"] if "Status Category" in filtered.columns else filtered.iloc[:0]
    in_prog = filtered.loc[filtered.get("Status Category", "").astype(str).str.lower() == "in progress"] if "Status Category" in filtered.columns else filtered.iloc[:0]
    to_do = filtered.loc[filtered.get("Status Category", "").astype(str).str.lower() == "to do"] if "Status Category" in filtered.columns else filtered.iloc[:0]

    done_count = len(done)
    completion = f"{round(done_count / total * 100)}%" if total else "—"

    import pandas as pd
    total_pts = pd.to_numeric(filtered.get("Story Points", 0), errors="coerce").sum()
    done_pts = pd.to_numeric(done.get("Story Points", 0), errors="coerce").sum() if len(done) else 0
    defects = len(filtered[filtered.get("Issue Type", "").astype(str).str.contains("(?i)bug|defect", regex=True, na=False)]) if "Issue Type" in filtered.columns else 0

    tiles = [
        ("🏃", "Sprint Items", str(total), f"of {len(df)} in dataset" if len(filtered) < len(df) else "total", "#7ea8ff"),
        ("✅", "Completed", str(done_count), f"{completion} done", "#5fd4a0"),
        ("🔄", "In Progress", str(len(in_prog)), "", "#f0d080"),
        ("📋", "To Do", str(len(to_do)), "", "#8e98bc"),
        ("⭐", "Story Points", f"{total_pts:,.0f}" if total_pts else "—", f"{done_pts:,.0f} completed" if done_pts else "", "#b898f5"),
        ("🐞", "Defects", str(defects), "", "#f07090"),
        ("🎯", "Completion", completion, "of sprint items", "#7ecdc0"),
    ]
    st.markdown(stat_tiles_html(tiles), unsafe_allow_html=True)


def _status_donut(filtered) -> go.Figure:
    if "Status Category" not in filtered.columns:
        return _empty("No Status Category column")
    counts = filtered["Status Category"].fillna("(none)").value_counts()
    colors = {"Done": "#5fd4a0", "In Progress": "#f0d080", "To Do": "#8e98bc"}
    fig = go.Figure(go.Pie(
        labels=counts.index.tolist(), values=counts.values.tolist(),
        hole=0.6, marker=dict(colors=[colors.get(c, "#7ea8ff") for c in counts.index]),
        textinfo="label+percent", textfont=dict(size=11, color=INK),
        hovertemplate="%{label}: %{value} (%{percent})<extra></extra>",
    ))
    fig.update_layout(template=TEMPLATE, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      margin=dict(t=10, b=10, l=10, r=10), showlegend=True,
                      legend=dict(font=dict(size=11, color=INK_MUTED)))
    return fig


def _type_bar(filtered) -> go.Figure:
    if "Issue Type" not in filtered.columns:
        return _empty("No Issue Type column")
    counts = filtered["Issue Type"].fillna("(none)").value_counts()
    palette = _theme_palette()
    fig = go.Figure(go.Bar(
        y=counts.index.tolist(), x=counts.values.tolist(), orientation="h",
        marker=dict(color=[palette[i % len(palette)] for i in range(len(counts))], cornerradius=4),
        hovertemplate="%{y}: %{x}<extra></extra>",
    ))
    fig.update_layout(template=TEMPLATE, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      margin=dict(t=10, b=30, l=10, r=10), xaxis=dict(gridcolor=GRID, tickfont=dict(color=INK_MUTED)),
                      yaxis=dict(tickfont=dict(color=INK)))
    return fig


def _points_bar(filtered) -> go.Figure:
    import pandas as pd
    if "Assignee" not in filtered.columns or "Story Points" not in filtered.columns:
        return _empty("Need Assignee and Story Points columns")
    df = filtered[["Assignee", "Story Points"]].copy()
    df["Story Points"] = pd.to_numeric(df["Story Points"], errors="coerce")
    sums = df.groupby("Assignee", dropna=False)["Story Points"].sum().sort_values(ascending=False)
    sums.index = sums.index.fillna("(none)")
    fig = go.Figure(go.Bar(
        x=sums.index.tolist(), y=sums.values.tolist(),
        marker=dict(color="#7ea8ff", cornerradius=4),
        hovertemplate="%{x}: %{y:.0f} pts<extra></extra>",
    ))
    fig.update_layout(template=TEMPLATE, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      margin=dict(t=10, b=30, l=10, r=10), yaxis=dict(gridcolor=GRID, tickfont=dict(color=INK_MUTED)),
                      xaxis=dict(tickfont=dict(color=INK, size=10)))
    return fig


def _priority_bar(filtered) -> go.Figure:
    if "Priority" not in filtered.columns:
        return _empty("No Priority column")
    order = ["Blocker", "Highest", "Critical", "High", "Major", "Medium", "Minor", "Low", "Lowest", "Trivial"]
    counts = filtered["Priority"].fillna("(none)").value_counts()
    labels = [p for p in order if p in counts.index] + [p for p in counts.index if p not in order]
    values = [counts[l] for l in labels]
    prio_colors = {"Highest": "#f07090", "High": "#f0a070", "Medium": "#f0d080",
                   "Low": "#7ecdc0", "Lowest": "#8e98bc", "Blocker": "#f07090",
                   "Critical": "#f07090", "Major": "#f0a070", "Minor": "#7ecdc0", "Trivial": "#8e98bc"}
    fig = go.Figure(go.Bar(
        x=labels, y=values,
        marker=dict(color=[prio_colors.get(l, "#7ea8ff") for l in labels], cornerradius=4),
        hovertemplate="%{x}: %{y}<extra></extra>",
    ))
    fig.update_layout(template=TEMPLATE, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      margin=dict(t=10, b=30, l=10, r=10), yaxis=dict(gridcolor=GRID, tickfont=dict(color=INK_MUTED)),
                      xaxis=dict(tickfont=dict(color=INK, size=10)))
    return fig


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


def _velocity_chart(filtered) -> go.Figure | None:
    velocity = compute_velocity(filtered)
    if velocity.empty:
        return None
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=velocity["Sprint"], y=velocity["Committed"], name="Committed",
        marker=dict(color="rgba(126,168,255,0.5)", cornerradius=4),
    ))
    fig.add_trace(go.Bar(
        x=velocity["Sprint"], y=velocity["Completed"], name="Completed",
        marker=dict(color="#5fd4a0", cornerradius=4),
    ))
    fig.update_layout(template=TEMPLATE, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      margin=dict(t=10, b=30, l=10, r=10), barmode="group",
                      xaxis=dict(tickfont=dict(color=INK, size=10)),
                      yaxis=dict(gridcolor=GRID, tickfont=dict(color=INK_MUTED),
                                 title=dict(text="Story Points", font=dict(color=INK_MUTED, size=11))),
                      legend=dict(font=dict(color=INK, size=11)))
    return fig


def _empty(msg: str = "No data") -> go.Figure:
    fig = go.Figure()
    fig.update_layout(template=TEMPLATE, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      annotations=[dict(text=msg, showarrow=False, font=dict(size=14, color=INK_MUTED))])
    return fig


def render(store, ds: Dataset) -> None:
    meta = ds.meta
    sprint_meta = meta.get("sprint") or {}
    is_sprint = meta.get("source") == "sprint"

    if not is_sprint:
        st.info("This dataset was not pulled from a sprint. Create a sprint dataset from the sidebar "
                "(**🏃 New dataset from Sprint**) to see sprint-specific reports here.")
        st.caption("You can still use the **📊 Dashboard** and **🧮 Report Builder** tabs for "
                   "general-purpose reports on this dataset.")
        return

    # Sprint report code
    seq = store.get_setting("sprint_report_seq") or 0
    ds_code = meta.get("_sprint_code")
    if not ds_code:
        seq += 1
        ds_code = f"Sprint-{seq:03d}"
        store.set_setting("sprint_report_seq", seq)

    head, code_col = st.columns([6, 1.5])
    with head:
        card_title("Sprint Reports", "#7ea8ff", ds_code)
    with code_col:
        st.caption(f"Report: **{ds_code}**")

    # Sprint meta chips
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
        chips.append(f"{len(names)} sprints: {', '.join(names[:3])}")
    if chips:
        from .style import hero as _hero
        import html
        chip_html = "".join(f'<span class="ji-chip">{html.escape(c)}</span>' for c in chips)
        st.markdown(f'<div class="ji-chips">{chip_html}</div>', unsafe_allow_html=True)

    # Global filter bar
    filtered = filter_bar(ds, key="sprint")

    # KPI tiles
    _sprint_kpis(ds.df, filtered)

    # Charts — 2×2 grid + full-width burndown + velocity
    c1, c2 = st.columns(2, gap="medium")
    with c1:
        with st.container(key="card-sprint-status"):
            card_title("Status Distribution", "#5fd4a0")
            st.plotly_chart(_status_donut(filtered), key="sprint-status-donut", theme=None,
                            config=PLOTLY_CONFIG, use_container_width=True)
    with c2:
        with st.container(key="card-sprint-type"):
            card_title("Issues by Type", "#b898f5")
            st.plotly_chart(_type_bar(filtered), key="sprint-type-bar", theme=None,
                            config=PLOTLY_CONFIG, use_container_width=True)

    c3, c4 = st.columns(2, gap="medium")
    with c3:
        with st.container(key="card-sprint-points"):
            card_title("Story Points by Assignee", "#7ea8ff")
            st.plotly_chart(_points_bar(filtered), key="sprint-points-bar", theme=None,
                            config=PLOTLY_CONFIG, use_container_width=True)
    with c4:
        with st.container(key="card-sprint-priority"):
            card_title("Priority Breakdown", "#f0a070")
            st.plotly_chart(_priority_bar(filtered), key="sprint-priority-bar", theme=None,
                            config=PLOTLY_CONFIG, use_container_width=True)

    # Burndown
    burndown_fig = _burndown_chart(filtered, sprint_meta)
    if burndown_fig:
        with st.container(key="card-sprint-burndown"):
            card_title("Sprint Burndown", "#7ea8ff")
            st.plotly_chart(burndown_fig, key="sprint-burndown", theme=None,
                            config=PLOTLY_CONFIG, use_container_width=True)

    # Velocity (meaningful for multi-sprint datasets)
    velocity_fig = _velocity_chart(filtered)
    if velocity_fig:
        with st.container(key="card-sprint-velocity"):
            card_title("Sprint Velocity", "#5fd4a0")
            st.plotly_chart(velocity_fig, key="sprint-velocity", theme=None,
                            config=PLOTLY_CONFIG, use_container_width=True)

    # Issues table
    with st.expander(f"📋 Sprint Issues ({len(filtered):,})", expanded=False):
        display_cols = [c for c in ["Key", "Issue Type", "Summary", "Status", "Status Category",
                                     "Priority", "Assignee", "Story Points", "Sprint Name"]
                        if c in filtered.columns]
        st.dataframe(filtered[display_cols] if display_cols else filtered, use_container_width=True)
