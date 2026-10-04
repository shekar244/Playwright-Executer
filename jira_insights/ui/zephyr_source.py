"""
🧪 New dataset from Zephyr — pull test executions (test runs) for execution reports.

  By cycle  project → version → cycle(s): the same flow as Amplify QEA's Test Management
  By ZQL    any Zephyr Query Language search, e.g. project = "ABC" AND executionStatus = FAIL

Uses the Zephyr / Jira connection from Amplify QEA → Config → Zephyr (see zephyr.py).
"""
from __future__ import annotations

import streamlit as st

from ..executions import executions_to_frame
from ..jira_client import JiraError
from ..settings import JiraSettings
from ..store import Store
from ..zephyr import default_project_key, is_configured, zephyr_client
from . import people

_MODES = {"cycle": "Project / version / cycle", "zql": "ZQL query"}


def _progress():
    bar = st.progress(0.0, text="Querying Zephyr…")

    def update(fetched: int, total: int | None) -> None:
        bar.progress(min(fetched / max(total or fetched or 1, 1), 1.0), text=f"Fetched {fetched:,} test runs…")
    return update


def _fetch(settings: JiraSettings, params: dict) -> list[dict]:
    client = zephyr_client(settings)
    progress = _progress()
    if params["mode"] == "zql":
        return client.zql(params["query"], int(params.get("max", 5000)), progress)
    rows: list[dict] = []
    for cycle in params["cycles"]:
        rows += client.cycle_executions(params["project"], params["version"], cycle, progress,
                                        int(params.get("max", 20000)) - len(rows))
    return rows


def name_lookup(settings: JiraSettings):
    """User id → display name lookups through Jira (None when Jira isn't configured)."""
    if not settings.configured:
        return None
    return lambda ids: zephyr_client(settings).display_names(ids)


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
                              extra={"zephyr": params, "jira_url": settings.url})
    store.seed_execution_starters()
    people.resolve_missing(store, df, name_lookup(settings))       # testers / assignees → names
    st.session_state["ds_pending"] = meta["slug"]
    st.rerun()


def refresh(store: Store, meta: dict, settings: JiraSettings) -> None:
    """↻ Refresh for a Zephyr dataset — re-runs the stored cycle selection or ZQL."""
    _save(store, meta["name"], settings, meta["zephyr"])


def describe(params: dict) -> str:
    if params.get("mode") == "zql":
        return "ZQL"
    cycles = ", ".join(c["name"] for c in params.get("cycles", [])) or "all cycles"
    return f"{params.get('project', {}).get('key', '')} · {params.get('version', {}).get('name', '')} · {cycles}"


def _load_versions(settings: JiraSettings, key: str) -> None:
    ss = st.session_state
    try:
        client = zephyr_client(settings)
        project = client.project(key)
        versions = [v for v in reversed(client.versions(key)) if not v.get("archived")]
    except JiraError as exc:
        st.error(f"Jira{f' ({exc.status})' if exc.status else ''}: {exc}")
        return
    ss.zs_project_obj = {"id": str(project.get("id", "")), "key": project.get("key", key)}
    ss.zs_versions = [{"id": "-1", "name": "Unscheduled"}] + [{"id": str(v["id"]), "name": v.get("name", v["id"])}
                                                              for v in versions]
    ss.zs_cycles_cache = {}
    ss.pop("zs_version", None)


def _by_cycle(store: Store, settings: JiraSettings, name: str) -> None:
    ss = st.session_state
    ss.setdefault("zs_project", default_project_key())
    c1, c2 = st.columns([2, 1], vertical_alignment="bottom")
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
            cache[version_id] = zephyr_client(settings).cycles(project["id"], version_id)
        except JiraError as exc:
            st.error(f"Zephyr{f' ({exc.status})' if exc.status else ''}: {exc}")
            return
    cycles = {c["id"]: c for c in cache[version_id]}
    if not cycles:
        st.caption("No test cycles in this version.")
        return
    picked = st.multiselect("Cycles", list(cycles), key=f"zs_cycles_{version_id}",
                            format_func=lambda i: cycles[i]["name"], placeholder="All cycles in this version")
    limit = st.number_input("Max test runs", min_value=50, max_value=100000, value=20000, step=1000, key="zs_max_c")
    if st.button("Fetch test runs", key="zs_fetch_cycle", type="primary", width="stretch"):
        chosen = [cycles[i] for i in picked] or list(cycles.values())
        params = {"mode": "cycle", "project": project, "version": by_id[version_id], "cycles": chosen,
                  "max": int(limit)}
        _save(store, name or f"{project['key']} {by_id[version_id]['name']} test runs", settings, params)


def _by_zql(store: Store, settings: JiraSettings, name: str) -> None:
    example = f'project = "{default_project_key() or "ABC"}" AND executionStatus != UNEXECUTED'
    query = st.text_area("ZQL", key="zs_zql", placeholder=example, height=100,
                         help="Zephyr Query Language — fields such as project, fixVersion, cycleName, "
                              "folderName, executionStatus, executedBy, component, priority.")
    limit = st.number_input("Max test runs", min_value=50, max_value=100000, value=5000, step=500, key="zs_max_z")
    if st.button("Fetch test runs", key="zs_fetch_zql", type="primary", width="stretch", disabled=not query.strip()):
        _save(store, name or "Zephyr test runs", settings, {"mode": "zql", "query": query.strip(), "max": int(limit)})


def render(store: Store, settings: JiraSettings) -> None:
    with st.expander("🧪 New dataset from Zephyr (test runs)"):
        if not is_configured():
            st.caption("Set up Zephyr in Amplify QEA → **Config → Zephyr** "
                       "(Jira URL, access key, secret key, account id).")
            return
        st.caption("Zephyr Squad · uses Config → Zephyr · builds execution reports: pass rate, "
                   "results by cycle, failures by component…")
        mode = st.radio("Select test runs by", list(_MODES), key="zs_mode", horizontal=True, format_func=_MODES.get)
        name = st.text_input("Dataset name", key="zs_name", placeholder="Release 3.2 test runs",
                             help="Re-using a name replaces that dataset.").strip()
        if mode == "zql":
            _by_zql(store, settings, name)
        else:
            _by_cycle(store, settings, name)
