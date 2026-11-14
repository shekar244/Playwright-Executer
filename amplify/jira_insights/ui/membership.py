"""
🧩 Reports on this dashboard — choose which saved reports (by R-number) a dataset's
dashboard shows, or copy them from another dataset, instead of recreating reports.

A dataset starts "automatic" (every saved report whose columns it has). The first
change gives it its own dashboard, so Jira and Zephyr datasets — or two releases —
can show different report sets. ↺ Automatic returns to the shared behaviour.
"""
from __future__ import annotations

import streamlit as st

from .. import layout
from ..pivot import ReportSpec
from ..store import Store
from .common import Dataset
from .style import card_title

_KIND_NAMES = {"zephyr": "Zephyr test runs", "jira": "Jira issues", "any": "any dataset"}


def label(report: ReportSpec) -> str:
    return f"{layout.KIND_BADGES[layout.report_kind(report)]} {report.title}"


def _fits(report: ReportSpec, columns: set[str]) -> bool:
    return not (report.required_columns() - columns)


def board_for(store: Store, slug: str, reports: list[ReportSpec], columns: set[str],
              default_rows) -> list[list[str]]:
    """Rows a dataset shows: its own dashboard, or every report that fits it (automatic)."""
    fitting = {r.id for r in reports if _fits(r, columns)}
    saved = store.get_dashboard(slug)
    rows = layout.clean(saved, reports) if saved is not None else layout.normalize(default_rows, reports)
    return layout.visible_rows(rows, fitting)


def render(store: Store, ds: Dataset, reports: list[ReportSpec], rows: list[list[str]], default_rows) -> None:
    ss = st.session_state
    columns = set(ds.df.columns)
    fitting = {r.id: r for r in reports if _fits(r, columns)}
    current = layout.members(rows)
    custom = store.get_dashboard(ds.slug) is not None

    with st.container(key="card-report-manager"):
        head, reset = st.columns([5, 1.6], vertical_alignment="center")
        with head:
            card_title("Reports on this dashboard")
        if reset.button("↺ Automatic", key="rm-auto", width="stretch", disabled=not custom,
                        help="Show every saved report that fits this dataset again"):
            store.clear_dashboard(ds.slug)
            st.rerun()
        st.caption(("**Custom for this dataset** — pick the reports it shows." if custom else
                    "**Automatic** — showing every saved report that fits this dataset. "
                    "Change the list to give this dataset its own dashboard.")
                   + "  🧾 Jira · 🧪 Zephyr · ◻️ either")

        picked = st.multiselect("Reports", list(fitting), default=[i for i in current if i in fitting],
                                format_func=lambda i: label(fitting[i]), label_visibility="collapsed",
                                key=f"rm-pick-{ds.slug}-{'-'.join(current)[:200]}",
                                placeholder="Pick reports for this dashboard")
        if picked != current:
            store.set_dashboard(ds.slug, layout.with_members(rows, picked, reports))
            st.rerun()

        others = {d["slug"]: d for d in store.list_datasets() if d["slug"] != ds.slug}
        if others:
            c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
            source = c1.selectbox("Copy reports from another dataset", ["", *others], key="rm-copy-from",
                                  format_func=lambda s: others[s]["name"] if s else "— choose a dataset —")
            if c2.button("Add its reports", key="rm-copy", width="stretch", disabled=not source):
                theirs = layout.members(board_for(store, source, reports, store.dataset_columns(source), default_rows))
                usable = [i for i in theirs if i in fitting and i not in current]
                skipped = [i for i in theirs if i not in fitting]
                store.set_dashboard(ds.slug, layout.with_members(rows, current + usable, reports))
                ss["rm_flash"] = (f"Added {len(usable)} report(s) from {others[source]['name']}"
                                  + (f" · {len(skipped)} skipped — this dataset lacks their columns" if skipped else ""))
                st.rerun()
        if "rm_flash" in ss:
            st.caption("✅ " + ss.pop("rm_flash"))

        unavailable = [r for r in reports if r.id not in fitting]
        if unavailable:
            with st.expander(f"Not available for this dataset ({len(unavailable)})"):
                for r in unavailable:
                    missing = ", ".join(sorted(r.required_columns() - columns))
                    st.caption(f"{label(r)} — for {_KIND_NAMES[layout.report_kind(r)]}; needs {missing}")
