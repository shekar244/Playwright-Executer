"""
🧪 New dataset from Zephyr — pull test executions (test runs) for execution reports.

  Drill-down  project → version → cycle(s), turned into a ZQL query just before fetching
  ZQL         any Zephyr Query Language search, e.g. project = "ABC" AND executionStatus = FAIL

Both fetch through the paged ZQL search — one search instead of an executions call
per cycle, which hit Zephyr's rate limits on large versions. The drill-down shows
the query it builds and can hand it to the ZQL editor for tweaking.

This module is identical in the standalone Jira Report Builder and in Amplify QEA;
each app's zephyr.py provides the connection (is_configured, zephyr_client, …).
"""
from __future__ import annotations

import streamlit as st

from .. import zephyr
from ..executions import executions_to_frame
from ..jira_client import JiraError
from ..settings import JiraSettings
from ..store import Store
from ..zql import drilldown_zql
from . import people

_MODES = {"cycle": "Project / version / cycle", "zql": "ZQL query"}


def _progress():
    bar = st.progress(0.0, text="Querying Zephyr…")

    def update(fetched: int, total: int | None) -> None:
        bar.progress(min(fetched / max(total or fetched or 1, 1), 1.0), text=f"Fetched {fetched:,} test runs…")
    return update


def _query(params: dict) -> str:
    """The ZQL for a saved selection (drill-down datasets saved before ZQL get theirs rebuilt)."""
    if params.get("query"):
        return params["query"]
    return drilldown_zql(params.get("project", {}).get("key", ""), params.get("version", {}).get("name", ""),
                         [c.get("name", "") for c in params.get("cycles", [])])


def _fetch(settings: JiraSettings, params: dict) -> list[dict]:
    return zephyr.zephyr_client(settings).zql(_query(params), int(params.get("max", 5000)), _progress())


def name_lookup(settings: JiraSettings):
    """User id → display name lookups through Jira (None when Jira isn't configured)."""
    if not settings.configured:
        return None
    return lambda ids: zephyr.zephyr_client(settings).display_names(ids)


def _save(store: Store, name: str, settings: JiraSettings, params: dict) -> None:
    try:
        records = _fetch(settings, params)
    except JiraError as exc:
        st.error(f"Zephyr{f' ({exc.status})' if exc.status else ''}: {exc}")
        return
    df, multi = executions_to_frame(records)
    if df.empty:
        st.warning("No test executions matched.")
        return
    meta = store.save_dataset(name, df, source="zephyr", multi_cols=multi,
                              extra={"zephyr": {**params, "query": _query(params)}, "jira_url": settings.url})
    store.seed_execution_starters()
    people.resolve_missing(store, df, name_lookup(settings))       # testers / assignees → names
    st.session_state["ds_pending"] = meta["slug"]
    st.rerun()


def refresh(store: Store, meta: dict, settings: JiraSettings) -> None:
    """↻ Refresh for a Zephyr dataset — re-runs its ZQL."""
    _save(store, meta["name"], settings, meta["zephyr"])


def describe(params: dict) -> str:
    if params.get("mode") == "zql":
        return "ZQL"
    cycles = ", ".join(c["name"] for c in params.get("cycles", [])) or "all cycles"
    return f"{params.get('project', {}).get('key', '')} · {params.get('version', {}).get('name', '')} · {cycles}"


def _error(exc: JiraError, what: str = "Zephyr") -> None:
    st.error(f"{what}{f' ({exc.status})' if exc.status else ''}: {exc}")


def _load_versions(settings: JiraSettings, key: str) -> None:
    ss = st.session_state
    try:
        client = zephyr.zephyr_client(settings)
        project = client.project(key)
        versions = [v for v in reversed(client.versions(key)) if not v.get("archived")]
    except JiraError as exc:
        _error(exc, "Jira")
        return
    ss.zs_project_obj = {"id": str(project.get("id", "")), "key": project.get("key", key)}
    ss.zs_versions = [{"id": "-1", "name": "Unscheduled"}] + [{"id": str(v["id"]), "name": v.get("name", v["id"])}
                                                              for v in versions]
    ss.zs_cycles_cache = {}
    ss.pop("zs_version", None)


def _by_cycle(store: Store, settings: JiraSettings, name: str) -> None:
    ss = st.session_state
    ss.setdefault("zs_project", zephyr.default_project_key(settings) or "")
    c1, c2 = st.columns([1.4, 1], vertical_alignment="bottom")
    key = c1.text_input("Project key", key="zs_project", placeholder="ABC").strip().upper()
    if c2.button("Load", key="zs_load", width="stretch", disabled=not key):
        _load_versions(settings, key)
    versions = ss.get("zs_versions")
    project = ss.get("zs_project_obj")
    if not versions or not project or project["key"].upper() != key:
        st.caption("Enter a project key and click **Load** to list its versions and cycles.")
        return

    by_id = {v["id"]: v for v in versions}
    version_id = st.selectbox("Version", list(by_id), key="zs_version", format_func=lambda i: by_id[i]["name"])
    cache = ss.setdefault("zs_cycles_cache", {})
    if version_id not in cache:
        try:
            cache[version_id] = zephyr.zephyr_client(settings).cycles(project["id"], version_id)
        except JiraError as exc:
            _error(exc)
            return
    cycles = {c["id"]: c for c in cache[version_id]}
    if not cycles:
        st.caption("No test cycles in this version.")
        return
    picked = st.multiselect("Cycles", list(cycles), key=f"zs_cycles_{version_id}",
                            format_func=lambda i: cycles[i]["name"], placeholder="All cycles in this version")
    query = drilldown_zql(project["key"], by_id[version_id]["name"], [cycles[i]["name"] for i in picked])
    st.caption("Fetched with this ZQL search:")
    st.code(query, language="sql", wrap_lines=True)
    limit = st.number_input("Max test runs", min_value=50, max_value=100000, value=20000, step=1000, key="zs_max_c")
    fetch_col, edit_col = st.columns([2, 1])
    fetch = fetch_col.button("Fetch test runs", key="zs_fetch_cycle", type="primary", width="stretch")
    if edit_col.button("Edit as ZQL", key="zs_to_zql", width="stretch",
                       help="Open this query in the ZQL editor to add conditions (status, tester, priority…)"):
        ss.zs_pending_zql = query
        st.rerun()
    if fetch:
        params = {"mode": "cycle", "query": query, "project": project, "version": by_id[version_id],
                  "cycles": [cycles[i] for i in picked], "max": int(limit)}
        _save(store, name or f"{project['key']} {by_id[version_id]['name']} test runs", settings, params)


def _by_zql(store: Store, settings: JiraSettings, name: str) -> None:
    example = f'project = "{zephyr.default_project_key(settings) or "ABC"}" AND executionStatus != UNEXECUTED'
    query = st.text_area("ZQL", key="zs_zql", placeholder=example, height=110,
                         help="Zephyr Query Language — fields such as project, fixVersion, cycleName, "
                              "folderName, executionStatus, executedBy, component, priority.")
    limit = st.number_input("Max test runs", min_value=50, max_value=100000, value=5000, step=500, key="zs_max_z")
    if st.button("Fetch test runs", key="zs_fetch_zql", type="primary", width="stretch", disabled=not query.strip()):
        _save(store, name or "Zephyr test runs", settings, {"mode": "zql", "query": query.strip(), "max": int(limit)})


def render(store: Store, settings: JiraSettings) -> None:
    ss = st.session_state
    with st.expander("🧪 New dataset from Zephyr (test runs)", expanded="zs_pending_zql" in ss):
        if not zephyr.is_configured(settings):
            st.caption(zephyr.setup_hint())
            return
        if "zs_pending_zql" in ss:                 # "Edit as ZQL" from the drill-down
            ss.zs_mode, ss.zs_zql = "zql", ss.pop("zs_pending_zql")
        st.caption(f"{zephyr.backend_label(settings)} · builds execution reports: pass rate, "
                   "results by cycle, failures by component…")
        mode = st.radio("Select test runs by", list(_MODES), key="zs_mode", horizontal=True, format_func=_MODES.get)
        name = st.text_input("Dataset name", key="zs_name", placeholder="Release 3.2 test runs",
                             help="Re-using a name replaces that dataset.").strip()
        if mode == "zql":
            _by_zql(store, settings, name)
        else:
            _by_cycle(store, settings, name)
