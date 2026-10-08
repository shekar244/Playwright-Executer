"""
Jira Report Builder (Jira Insights) — Streamlit reporting workspace.

Pull Jira issues with JQL (or upload a Jira CSV/Excel export), then build
pivot reports and charts from the UI — no code per chart:

  📊 Dashboard       saved reports under one global filter bar
  🧮 Report Builder  pivot + chart configurator, saved to the dashboard
  🎨 Explorer        PyGWalker drag-and-drop canvas
  🗃️ Data            raw issues with Jira links and downloads

Launch:  ./run.sh   (Windows: run.bat)   or   streamlit run app.py
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st  # noqa: E402

from jira_insights.settings import workspace_dir  # noqa: E402
from jira_insights.store import Store  # noqa: E402
from jira_insights.ui import builder, dashboard, data_source, data_view, explorer, sprint_view  # noqa: E402
from jira_insights.ui.style import hero, inject_css  # noqa: E402

VIEWS = {"Dashboard": "📊", "Sprint Reports": "🏃", "Report Builder": "🧮", "Explorer": "🎨", "Data": "🗃️"}

st.set_page_config(page_title="Jira Insights", page_icon="📈", layout="wide",
                   initial_sidebar_state="expanded")

ss = st.session_state
ss.setdefault("ji_dark_mode", True)
inject_css()

store = Store(workspace_dir())
ss["ji_category_colors"] = store.get_setting("category_colors", {}) or {}   # 🎨 field defaults
ds = data_source.render_sidebar(store)

if ds is None:
    hero("Jira Insights", ["Pull issues with JQL", "Build pivot reports", "No code per chart"])
    st.markdown("Create a dataset from the sidebar to get started:\n\n"
                "1. **New dataset from JQL** — pulls issues straight from Jira, or\n"
                "2. **Upload Jira export** — a CSV / Excel export from Jira's issue navigator.\n\n"
                "Then build reports in **🧮 Report Builder** and pin them to the **📊 Dashboard**.")
    st.stop()

if "view_pending" in ss:
    ss.view = ss.pop("view_pending")
ss.setdefault("view", "Dashboard")

head, nav, theme_col = st.columns([1.2, 2, 0.5], vertical_alignment="center")
with head:
    meta = ds.meta
    source = meta.get("source")
    origin = {"jql": "⚡ JQL", "zephyr": "🧪 Zephyr", "sprint": "🏃 Sprint"}.get(source, "📄 Upload")
    unit = "test runs" if source == "zephyr" else "sprint items" if source == "sprint" else "issues"
    hero(meta.get("name", ds.slug), [f"🧾 {meta.get('rows', len(ds.df)):,} {unit}", origin,
                                     f"🕒 {meta.get('fetched_at', '')[:16].replace('T', ' ')} UTC"])
view = nav.segmented_control("View", list(VIEWS), key="view", required=True,
                             format_func=lambda v: f"{VIEWS[v]} {v}", label_visibility="collapsed")
with theme_col:
    mode_label = "🌙 Dark" if ss.get("ji_dark_mode", True) else "☀️ Light"
    dark = st.toggle(mode_label, value=ss.get("ji_dark_mode", True), key="ji_theme_toggle",
                     help="Toggle dark / light theme")
    if dark != ss.get("ji_dark_mode", True):
        ss["ji_dark_mode"] = dark
        st.rerun()

if view == "Report Builder":
    builder.render(store, ds)
elif view == "Sprint Reports":
    sprint_view.render(store, ds)
elif view == "Explorer":
    explorer.render(store, ds)
elif view == "Data":
    data_view.render(ds)
else:
    dashboard.render(store, ds)
