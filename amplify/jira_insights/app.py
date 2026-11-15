"""
Jira Insights — Streamlit reporting workspace for Amplify QEA.

Pull Jira issues with JQL (or upload a Jira CSV/Excel export), then build
pivot reports and charts from the UI — no code per chart:

  📊 Dashboard       saved reports under one global filter bar
  🧮 Report Builder  pivot + chart configurator, saved to the dashboard
  🎨 Explorer        PyGWalker drag-and-drop canvas
  🗃️ Data            raw issues with Jira links and downloads

Started from the Jira Insights tab, or standalone from the tool root:
  streamlit run amplify/jira_insights/app.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]   # repo root, so `amplify` is importable
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st  # noqa: E402

from amplify.jira_insights.settings import workspace_dir  # noqa: E402
from amplify.jira_insights.store import Store  # noqa: E402
from amplify.jira_insights.ui import builder, dashboard, data_source, data_view, explorer, sprint_view  # noqa: E402
from amplify.jira_insights.ui.style import hero, inject_css  # noqa: E402

VIEWS = {"Dashboard": "📊", "Sprint Reports": "🏃", "Report Builder": "🧮", "Explorer": "🎨", "Data": "🗃️"}

st.set_page_config(page_title="Jira Insights", page_icon="📈", layout="wide",
                   initial_sidebar_state="expanded")

ss = st.session_state
ss.setdefault("ji_dark_mode", st.query_params.get("theme", "light") == "dark")
inject_css()

# ── Authentication ────────────────────────────────────────────────────────────
# If accessed via Flask iframe, user_id comes from query params (already authed).
# If accessed standalone, show a login form using the same Amplify user database.
_user_id = st.query_params.get("user_id")
_is_embedded = st.query_params.get("embed") == "true"

if not _user_id and not _is_embedded:
    _logged_in = "ji_auth_user_id" in ss
    # Toggle sidebar/header visibility based on login state
    st.markdown(f"""<style>
    section[data-testid="stSidebar"] {{ display:{'block' if _logged_in else 'none'} !important; }}
    [data-testid="stHeader"] {{ display:{'block' if _logged_in else 'none'} !important; }}
    .stForm div[data-testid="InputInstructions"] {{ display:none !important; }}
    </style>""", unsafe_allow_html=True)

    if not _logged_in:
        import urllib.request, urllib.error, json as _json, ssl as _ssl

        def _qea_api(endpoint, payload):
            _qea_url = os.environ.get("QEA_API_URL", "").rstrip("/")
            if not _qea_url:
                return None, "QEA_API_URL not configured"
            _ctx = _ssl._create_unverified_context()
            _req = urllib.request.Request(
                f"{_qea_url}{endpoint}",
                data=_json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            try:
                with urllib.request.urlopen(_req, timeout=10, context=_ctx) as _resp:
                    return _json.loads(_resp.read()), None
            except urllib.error.HTTPError as _he:
                _body = _he.read().decode()
                try:
                    return None, _json.loads(_body).get("error", _body)
                except Exception:
                    return None, _body
            except Exception as _ex:
                return None, str(_ex)

        _sp1, _login_col, _sp2 = st.columns([1, 1, 1])
        with _login_col:
            st.markdown("""
            <div style="text-align:center;margin:12vh 0 24px;">
                <div style="width:48px;height:48px;border-radius:13px;margin:0 auto 14px;
                     background:linear-gradient(135deg,#0071e3,#0a7f99);color:#fff;
                     display:flex;align-items:center;justify-content:center;font-size:22px;
                     box-shadow:0 2px 8px rgba(0,113,227,.3);">⚡</div>
                <div style="font-size:24px;font-weight:800;letter-spacing:-0.03em;">Amplify <span style="color:#6e6e73;font-weight:400;">QEA</span></div>
                <div style="color:#6e6e73;font-size:13px;margin-top:6px;">Jira Insights</div>
            </div>
            """, unsafe_allow_html=True)

            _login_tab, _register_tab = st.tabs(["Sign In", "Create Account"])

            with _login_tab:
                with st.form("login_form"):
                    l_user = st.text_input("Username", key="l_user")
                    l_pass = st.text_input("Password", type="password", key="l_pass")
                    l_submit = st.form_submit_button("Sign In", use_container_width=True, type="primary")
                if l_submit and l_user and l_pass:
                    _qea_url = os.environ.get("QEA_API_URL", "").rstrip("/")
                    if _qea_url:
                        _data, _err = _qea_api("/api/auth/login", {"username": l_user, "password": l_pass})
                        if _data and _data.get("ok"):
                            ss["ji_auth_user_id"] = _data["user"]["id"]
                            ss["ji_auth_username"] = _data["user"]["username"]
                            st.rerun()
                        else:
                            st.error(_err or "Invalid credentials")
                    else:
                        from amplify.models.user import User
                        user = User.verify_credentials(l_user, l_pass)
                        if user:
                            ss["ji_auth_user_id"] = user.id
                            ss["ji_auth_username"] = user.username
                            st.rerun()
                        else:
                            st.error("Invalid credentials")

            with _register_tab:
                with st.form("register_form"):
                    r_user = st.text_input("Username (min 3 chars)", key="r_user")
                    r_email = st.text_input("Email (optional)", key="r_email")
                    r_pass = st.text_input("Password (min 6 chars)", type="password", key="r_pass")
                    r_submit = st.form_submit_button("Create Account", use_container_width=True, type="primary")
                if r_submit and r_user and r_pass:
                    _qea_url = os.environ.get("QEA_API_URL", "").rstrip("/")
                    if _qea_url:
                        _payload = {"username": r_user, "password": r_pass}
                        if r_email:
                            _payload["email"] = r_email
                        _data, _err = _qea_api("/api/auth/register", _payload)
                        if _data and _data.get("ok"):
                            ss["ji_auth_user_id"] = _data["user"]["id"]
                            ss["ji_auth_username"] = _data["user"]["username"]
                            st.rerun()
                        else:
                            st.error(_err or "Registration failed")
                    else:
                        from amplify.models.user import User
                        try:
                            user = User.create(r_user, r_pass, r_email or None)
                            ss["ji_auth_user_id"] = user.id
                            ss["ji_auth_username"] = user.username
                            from amplify.storage import initialize_user_workspace
                            initialize_user_workspace(user.id)
                            st.rerun()
                        except ValueError as _ve:
                            st.error(str(_ve))

        st.stop()
    _user_id = ss["ji_auth_user_id"]

store = Store(workspace_dir(_user_id))
ss["ji_category_colors"] = store.get_setting("category_colors", {}) or {}   # 🎨 field defaults

# Show logged-in user + logout in sidebar (standalone mode only)
if not _is_embedded and "ji_auth_username" in ss:
    with st.sidebar:
        cols = st.columns([3, 1])
        cols[0].caption(f"👤 **{ss['ji_auth_username']}**")
        if cols[1].button("Logout", key="ji_logout", type="secondary"):
            del ss["ji_auth_user_id"]
            del ss["ji_auth_username"]
            st.rerun()

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
    mode_label = "🌙 Dark" if ss.get("ji_dark_mode", False) else "☀️ Light"
    dark = st.toggle(mode_label, value=ss.get("ji_dark_mode", False), key="ji_theme_toggle",
                     help="Toggle dark / light theme")
    if dark != ss.get("ji_dark_mode", False):
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
