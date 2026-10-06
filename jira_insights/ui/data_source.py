"""
Sidebar: choose a dataset, pull a new one with JQL, upload a Jira CSV/Excel
export, refresh (re-run the JQL) or delete. Returns the active Dataset.
"""
from __future__ import annotations

from urllib.parse import urlparse

import streamlit as st

from ..charts import DEFAULT_THEME, THEMES
from ..jira_client import JiraClient, JiraError
from ..settings import load_jira_settings
from ..store import Store
from ..transform import DEFAULT_SKIP_FIELDS, SKIP_FIELD_GROUPS, _ALWAYS_SPECIAL, frame_from_export, issues_to_frame
from .. import zephyr
from ..executions import apply_user_names
from . import people, sprint_source, zephyr_source
from .common import Dataset, load_dataset
from .style import brand, swatches

CORE_COLUMNS = ("Issue Type", "Status", "Created")
_JQL_HINT = "project = ABC AND issuetype in (Bug, Story) AND created >= -90d ORDER BY created DESC"


def _describe(meta: dict) -> str:
    when = meta.get("fetched_at", "")[:16].replace("T", " ")
    if meta.get("source") == "zephyr":
        return f"{meta.get('rows', 0):,} test runs · Zephyr · {meta.get('fetched_at', '')[:16].replace('T', ' ')} UTC"
    if meta.get("source") == "sprint":
        sprint = meta.get("sprint", {})
        sprint_label = sprint.get("name", "Sprint")
        if sprint.get("mode") == "multi":
            sprint_label = f"{len(sprint.get('sprint_names', []))} sprints"
        return f"{meta.get('rows', 0):,} issues · 🏃 {sprint_label} · {when} UTC"
    origin = "JQL" if meta.get("source") == "jql" else f"upload · {meta.get('file_name', '')}"
    return f"{meta.get('rows', 0):,} issues · {origin} · {when} UTC"


def _active_skip_fields(store: Store) -> set[str]:
    """The user's skip-field preferences, falling back to the defaults."""
    saved = store.get_setting("skip_fields")
    if isinstance(saved, list):
        return set(saved) | _ALWAYS_SPECIAL
    return DEFAULT_SKIP_FIELDS


def _pull(store: Store, name: str, jql: str, max_issues: int, fields: str) -> None:
    settings = load_jira_settings()
    skip = _active_skip_fields(store)
    try:
        client = JiraClient(settings)
        bar = st.progress(0.0, text="Querying Jira…")

        def progress(fetched: int, total: int | None) -> None:
            bar.progress(min(fetched / max(total or max_issues, 1), 1.0), text=f"Fetched {fetched:,} issues…")

        issues = client.search(jql, fields=fields or "*navigable", max_issues=int(max_issues), on_progress=progress)
        try:
            names = client.field_names()
        except JiraError:
            names = {}
    except JiraError as exc:
        st.error(f"Jira error{f' ({exc.status})' if exc.status else ''}: {exc}")
        return
    if not issues:
        st.warning("The JQL returned no issues.")
        return
    df, multi = issues_to_frame(issues, names, skip_fields=skip)
    meta = store.save_dataset(name, df, source="jql", jql=jql, multi_cols=multi,
                              extra={"max_issues": int(max_issues), "fields": fields, "jira_url": settings.url})
    st.session_state["ds_pending"] = meta["slug"]
    st.rerun()


def _import(store: Store, name: str, upload) -> None:
    try:
        df, multi = frame_from_export(upload.getvalue(), upload.name)
    except Exception as exc:          # malformed CSV / Excel — show, don't crash the app
        st.error(f"Could not read {upload.name}: {exc}")
        return
    if df.empty:
        st.warning("The file has no rows.")
        return
    meta = store.save_dataset(name or upload.name.rsplit(".", 1)[0], df, source="upload",
                              multi_cols=multi, extra={"file_name": upload.name,
                                                       "jira_url": load_jira_settings().url})
    st.session_state["ds_pending"] = meta["slug"]
    st.rerun()


def render_sidebar(store: Store) -> Dataset | None:
    ss = st.session_state
    settings = load_jira_settings()
    datasets = store.list_datasets()
    by_slug = {d["slug"]: d for d in datasets}
    if "ds_pending" in ss:
        ss["ds_slug"] = ss.pop("ds_pending")

    if ss.get("ji_theme") not in THEMES:
        ss["ji_theme"] = store.get_setting("theme", DEFAULT_THEME)
        if ss["ji_theme"] not in THEMES:
            ss["ji_theme"] = DEFAULT_THEME

    current = None
    with st.sidebar:
        brand()
        if settings.configured:
            st.caption(f"🟢 Jira · {urlparse(settings.url).netloc}")
        else:
            st.caption("⚪ Jira not configured — set it in **Config → Zephyr** "
                       "(or JIRA_URL / JIRA_API_TOKEN), or upload an export below.")

        if datasets:
            if ss.get("ds_slug") not in by_slug:
                ss["ds_slug"] = datasets[0]["slug"]
            slug = st.selectbox("Dataset", list(by_slug), key="ds_slug",
                                format_func=lambda s: by_slug[s]["name"])
            meta = by_slug[slug]
            st.caption(_describe(meta))
            if meta.get("jql"):
                st.code(meta["jql"], language="sql", wrap_lines=True)
            runs = meta.get("zephyr") or {}
            if runs.get("mode") == "zql":
                st.code(runs.get("query", ""), language="sql", wrap_lines=True)
            elif runs:
                st.caption(f"🧪 {zephyr_source.describe(runs)}")
            sprint_info = meta.get("sprint") or {}
            if sprint_info:
                if sprint_info.get("mode") == "multi":
                    names = sprint_info.get("sprint_names", [])
                    st.caption(f"🏃 {len(names)} sprint(s): {', '.join(names[:5])}"
                               + (f" +{len(names)-5} more" if len(names) > 5 else ""))
                elif sprint_info.get("name"):
                    start = (sprint_info.get("startDate") or "")[:10]
                    end = (sprint_info.get("endDate") or "")[:10]
                    dates = f" · {start} → {end}" if start and end else ""
                    st.caption(f"🏃 {sprint_info['name']}{dates}")
            refreshable = (meta.get("source") == "jql" and settings.configured) or \
                          (meta.get("source") == "zephyr" and zephyr.is_configured()) or \
                          (meta.get("source") == "sprint" and settings.configured)
            c1, c2 = st.columns(2)
            if c1.button("↻ Refresh", width="stretch", disabled=not refreshable,
                         help="Re-run the JQL and replace this dataset"):
                if meta.get("source") == "zephyr":
                    zephyr_source.refresh(store, meta, settings)
                elif meta.get("source") == "sprint":
                    sprint_source.refresh(store, meta, settings)
                else:
                    _pull(store, meta["name"], meta["jql"], meta.get("max_issues", 2000),
                          meta.get("fields", "*navigable"))
            if c2.button("🗑 Delete", width="stretch", help="Delete this dataset and its explorer charts"):
                store.delete_dataset(slug)
                ss.pop("ds_slug", None)
                st.rerun()
            df, meta = load_dataset(str(store.root), slug, meta.get("fetched_at", ""))
            if meta.get("source") == "zephyr":       # ids → names, editable under 👥 Tester names
                people.render(store, df, zephyr_source.name_lookup(settings))
                df = apply_user_names(df, people.known_names(store))
            current = Dataset(slug, meta, df)
            missing = [] if meta.get("source") == "zephyr" else [c for c in CORE_COLUMNS if c not in df.columns]
            if missing:
                st.warning(f"Missing typical Jira columns: {', '.join(missing)}. "
                           "Reports that use them are hidden.", icon="⚠️")

        with st.expander("➕ New dataset from JQL", expanded=not datasets):
            with st.form("jql_form", border=False):
                name = st.text_input("Name", placeholder="Release 3.2 defects",
                                     help="Re-using a name replaces that dataset.")
                jql = st.text_area("JQL", placeholder=_JQL_HINT, height=120)
                max_issues = st.number_input("Max issues", min_value=50, max_value=50000, value=2000, step=500)
                fields = st.text_input("Fields", value="*navigable",
                                       help="Comma-separated field ids, or *navigable / *all")
                if st.form_submit_button("Fetch issues", type="primary", width="stretch",
                                         disabled=not settings.configured):
                    if name.strip() and jql.strip():
                        _pull(store, name.strip(), jql.strip(), max_issues, fields.strip())
                    else:
                        st.error("Name and JQL are required.")

        zephyr_source.render(store, settings)

        sprint_source.render(store, settings)

        with st.expander("⬆️ Upload Jira export (CSV / Excel)", expanded=not datasets and not settings.configured):
            with st.form("upload_form", border=False, clear_on_submit=True):
                up_name = st.text_input("Name", placeholder="Defects export")
                upload = st.file_uploader("Jira export", type=["csv", "xlsx", "xls"], label_visibility="collapsed")
                if st.form_submit_button("Import", width="stretch"):
                    if upload is None:
                        st.error("Choose a file first.")
                    else:
                        _import(store, up_name.strip(), upload)

        _skip_fields_config(store)

        st.selectbox("🎨 Colour theme", list(THEMES), key="ji_theme",
                     on_change=lambda: store.set_setting("theme", ss["ji_theme"]),
                     help="Chart palette — every theme is checked for colour-blind safety and contrast.")
        swatches(THEMES[ss["ji_theme"]].palette)
    return current


def _skip_fields_config(store: Store) -> None:
    """UI to view and modify which Jira fields are excluded from datasets."""
    with st.expander("🔧 Excluded fields", expanded=False):
        st.caption("Fields skipped when building datasets. Uncheck a group to include those fields. "
                   "Issue links and sub-tasks are always extracted into structured columns.")

        saved = store.get_setting("skip_fields")
        current_skip = set(saved) if isinstance(saved, list) else \
            {f for group in SKIP_FIELD_GROUPS.values() for f in group}

        changed = False
        new_skip: set[str] = set()

        for group_name, fields in SKIP_FIELD_GROUPS.items():
            all_skipped = fields <= current_skip
            label = f"{group_name}  ({', '.join(sorted(fields))})"
            skip_this = st.checkbox(f"Skip {group_name}", value=all_skipped,
                                    key=f"skip_{group_name}",
                                    help=f"Fields: {', '.join(sorted(fields))}")
            if skip_this:
                new_skip |= fields
            if skip_this != all_skipped:
                changed = True

        if changed:
            store.set_setting("skip_fields", sorted(new_skip))
            st.caption("✅ Saved — re-fetch or refresh a dataset to apply.")
        else:
            included = {f for group in SKIP_FIELD_GROUPS.values() for f in group} - current_skip
            if included:
                st.caption(f"Currently included: {', '.join(sorted(included))}")
