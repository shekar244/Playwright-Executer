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
from jira_insights.ui import builder, dashboard, data_source, data_view, explorer  # noqa: E402
from jira_insights.ui.style import hero, inject_css  # noqa: E402

VIEWS = {"Dashboard": "📊", "Report Builder": "🧮", "Explorer": "🎨", "Data": "🗃️"}

st.set_page_config(page_title="Jira Insights", page_icon="📈", layout="wide",
                   initial_sidebar_state="expanded")
inject_css()

store = Store(workspace_dir())
st.session_state["ji_category_colors"] = store.get_setting("category_colors", {}) or {}   # 🎨 field defaults
ds = data_source.render_sidebar(store)

if ds is None:
    hero("Jira Insights", ["Pull issues with JQL", "Build pivot reports", "No code per chart"])
    st.markdown("Create a dataset from the sidebar to get started:\n\n"
                "1. **New dataset from JQL** — pulls issues straight from Jira, or\n"
                "2. **Upload Jira export** — a CSV / Excel export from Jira's issue navigator.\n\n"
                "Then build reports in **🧮 Report Builder** and pin them to the **📊 Dashboard**.")
    st.stop()

ss = st.session_state
if "view_pending" in ss:
    ss.view = ss.pop("view_pending")
ss.setdefault("view", "Dashboard")

head, nav = st.columns([1.2, 2], vertical_alignment="center")
with head:
    meta = ds.meta
    source = meta.get("source")
    origin = {"jql": "⚡ JQL", "zephyr": "🧪 Zephyr"}.get(source, "📄 Upload")
    unit = "test runs" if source == "zephyr" else "issues"
    hero(meta.get("name", ds.slug), [f"🧾 {meta.get('rows', len(ds.df)):,} {unit}", origin,
                                     f"🕒 {meta.get('fetched_at', '')[:16].replace('T', ' ')} UTC"])
view = nav.segmented_control("View", list(VIEWS), key="view", required=True,
                             format_func=lambda v: f"{VIEWS[v]} {v}", label_visibility="collapsed")

if view == "Report Builder":
    builder.render(store, ds)
elif view == "Explorer":
    explorer.render(store, ds)
elif view == "Data":
    data_view.render(ds)
else:
    dashboard.render(store, ds)
