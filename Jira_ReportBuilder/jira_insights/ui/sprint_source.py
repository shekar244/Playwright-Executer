"""
🏃 New dataset from Sprint — pull issues by sprint selection.

Two modes:
  Single sprint   select one sprint from a board and pull its issues
  Multi-sprint    select multiple active sprints across teams/projects

Sprint datasets are saved with source="sprint" and carry sprint metadata
(name, dates, goal) so burndown and velocity reports can be built.
Report numbering uses the Sprint-001 scheme.
"""
from __future__ import annotations

import streamlit as st

from ..jira_client import JiraClient, JiraError
from ..settings import JiraSettings, load_jira_settings
from ..sprint_client import SprintClient
from ..sprint_transform import sprint_issues_to_frame
from ..store import Store
from ..transform import DEFAULT_SKIP_FIELDS, _ALWAYS_SPECIAL


def _progress():
    bar = st.progress(0.0, text="Querying Jira…")

    def update(fetched: int, total: int | None) -> None:
        bar.progress(min(fetched / max(total or fetched or 1, 1), 1.0),
                     text=f"Fetched {fetched:,} issues…")
    return update


def _active_skip_fields(store: Store) -> set[str]:
    saved = store.get_setting("skip_fields")
    if isinstance(saved, list):
        return set(saved) | _ALWAYS_SPECIAL
    return DEFAULT_SKIP_FIELDS


def _save_sprint_dataset(store: Store, name: str, issues: list[dict], client: JiraClient,
                         sprint_meta: dict | None, settings: JiraSettings,
                         extra: dict | None = None) -> None:
    try:
        field_names = client.field_names()
    except JiraError:
        field_names = {}
    skip = _active_skip_fields(store)
    df, multi = sprint_issues_to_frame(issues, field_names, sprint_meta, skip_fields=skip)
    if df.empty:
        st.warning("No issues found for this sprint.")
        return
    meta_extra = {"sprint": sprint_meta or {}, "jira_url": settings.url, **(extra or {})}
    meta = store.save_dataset(name, df, source="sprint", multi_cols=multi, extra=meta_extra)
    store.seed_sprint_starters()
    st.session_state["ds_pending"] = meta["slug"]
    st.rerun()


def _single_sprint(store: Store, settings: JiraSettings, name: str) -> None:
    ss = st.session_state
    ss.setdefault("sp_project", "")
    c1, c2 = st.columns([1.4, 1], vertical_alignment="bottom")
    project_key = c1.text_input("Project key", key="sp_project", placeholder="ABC").strip().upper()
    if c2.button("Load boards", key="sp_load_boards", width="stretch", disabled=not project_key):
        try:
            client = JiraClient(settings)
            sc = SprintClient(client)
            boards = sc.boards(project_key)
            ss.sp_boards = boards
            ss.sp_board_project = project_key
            ss.pop("sp_sprints", None)
        except JiraError as exc:
            st.error(f"Jira{f' ({exc.status})' if exc.status else ''}: {exc}")
            return

    boards = ss.get("sp_boards")
    if not boards or ss.get("sp_board_project", "").upper() != project_key:
        st.caption("Enter a project key and click **Load boards** to list its sprints.")
        return

    by_id = {b["id"]: b for b in boards}
    if not by_id:
        st.warning("No boards found for this project.")
        return
    board_id = st.selectbox("Board", list(by_id), key="sp_board",
                            format_func=lambda i: f"{by_id[i].get('name', i)} ({by_id[i].get('type', '')})")

    if st.button("Load sprints", key="sp_load_sprints", width="stretch"):
        try:
            client = JiraClient(settings)
            sc = SprintClient(client)
            sprints = sc.sprints(board_id)
            ss.sp_sprints = sprints
            ss.sp_sprints_board = board_id
        except JiraError as exc:
            st.error(f"Jira{f' ({exc.status})' if exc.status else ''}: {exc}")
            return

    sprints = ss.get("sp_sprints")
    if not sprints or ss.get("sp_sprints_board") != board_id:
        return

    state_icons = {"active": "🟢", "closed": "✅", "future": "📅"}
    sprint_by_id = {s["id"]: s for s in sprints}
    sprint_id = st.selectbox(
        "Sprint", list(sprint_by_id),
        key="sp_sprint",
        format_func=lambda i: f"{state_icons.get(sprint_by_id[i].get('state', ''), '⚪')} "
                              f"{sprint_by_id[i].get('name', i)}"
    )

    selected = sprint_by_id.get(sprint_id, {})
    if selected:
        cols = st.columns(3)
        cols[0].caption(f"State: **{selected.get('state', 'unknown')}**")
        start = (selected.get("startDate") or "")[:10]
        end = (selected.get("endDate") or "")[:10]
        if start:
            cols[1].caption(f"Start: **{start}**")
        if end:
            cols[2].caption(f"End: **{end}**")
        if selected.get("goal"):
            st.caption(f"Goal: {selected['goal']}")

    limit = st.number_input("Max issues", min_value=50, max_value=50000, value=2000, step=500, key="sp_max")
    v1, v2 = st.columns([1.2, 1])
    include_velocity = v1.checkbox("Include velocity history", value=True, key="sp_velocity",
                                   help="Pull the last N closed sprints from this board to show "
                                        "team velocity trend alongside the current sprint.")
    history_count = v2.number_input("Past sprints", min_value=1, max_value=20, value=5, step=1,
                                    key="sp_history_count", disabled=not include_velocity)

    if st.button("Fetch sprint issues", key="sp_fetch", type="primary", width="stretch"):
        try:
            client = JiraClient(settings)
            sc = SprintClient(client)
            issues = sc.sprint_issues_by_id(sprint_id, project_key,
                                            max_issues=int(limit), on_progress=_progress())
            if not issues:
                st.warning("No issues found in this sprint.")
                return

            velocity_history = []
            if include_velocity:
                with st.spinner(f"Fetching velocity from last {int(history_count)} sprints…"):
                    try:
                        velocity_history = sc.velocity_history(
                            board_id, sprint_id, project_key=project_key,
                            history_count=int(history_count),
                        )
                    except JiraError:
                        st.warning("Could not fetch velocity history — continuing without it.")

            ds_name = name or f"{project_key} {selected.get('name', f'Sprint {sprint_id}')}"
            extra = {"velocity_history": velocity_history} if velocity_history else {}
            _save_sprint_dataset(store, ds_name, issues, client, selected, settings, extra=extra)
        except JiraError as exc:
            st.error(f"Jira{f' ({exc.status})' if exc.status else ''}: {exc}")


def _multi_sprint(store: Store, settings: JiraSettings, name: str) -> None:
    ss = st.session_state
    ss.setdefault("sp_multi_projects", "")
    projects_input = st.text_input(
        "Project keys (comma-separated)", key="sp_multi_projects",
        placeholder="ABC, DEF, GHI",
        help="Enter project keys to scan for active sprints across teams.",
    )
    project_keys = [k.strip().upper() for k in projects_input.split(",") if k.strip()]

    if st.button("Find active sprints", key="sp_find_active", width="stretch", disabled=not project_keys):
        try:
            client = JiraClient(settings)
            sc = SprintClient(client)
            active = sc.active_sprints_across_boards(project_keys)
            ss.sp_active_sprints = active
        except JiraError as exc:
            st.error(f"Jira{f' ({exc.status})' if exc.status else ''}: {exc}")
            return

    active = ss.get("sp_active_sprints")
    if not active:
        st.caption("Enter project keys and click **Find active sprints** to discover sprints across teams.")
        return

    st.caption(f"Found **{len(active)}** active sprint(s) across {len(project_keys)} project(s)")
    sprint_by_id = {s["id"]: s for s in active}
    selected_ids = st.multiselect(
        "Select sprints", list(sprint_by_id),
        key="sp_multi_selected",
        format_func=lambda i: f"{sprint_by_id[i].get('_project', '')} · "
                              f"{sprint_by_id[i].get('name', i)} "
                              f"({sprint_by_id[i].get('_board', '')})",
        placeholder="Choose one or more sprints…",
    )

    if not selected_ids:
        return

    for sid in selected_ids:
        sp = sprint_by_id[sid]
        start = (sp.get("startDate") or "")[:10]
        end = (sp.get("endDate") or "")[:10]
        st.caption(f"• **{sp.get('name')}** ({sp.get('_project', '')}) — {start} → {end}")

    limit = st.number_input("Max issues", min_value=100, max_value=100000, value=5000, step=1000, key="sp_multi_max")
    if st.button("Fetch all sprint issues", key="sp_multi_fetch", type="primary", width="stretch"):
        try:
            client = JiraClient(settings)
            sc = SprintClient(client)
            issues = sc.multi_sprint_issues(selected_ids, max_issues=int(limit), on_progress=_progress())
            if not issues:
                st.warning("No issues found across selected sprints.")
                return
            sprint_names = [sprint_by_id[i].get("name", str(i)) for i in selected_ids]
            ds_name = name or f"Multi-sprint: {', '.join(sprint_names[:3])}"
            if len(sprint_names) > 3:
                ds_name += f" +{len(sprint_names) - 3} more"
            combined_meta = {
                "name": ds_name,
                "sprint_ids": selected_ids,
                "sprint_names": sprint_names,
                "mode": "multi",
            }
            _save_sprint_dataset(store, ds_name, issues, client, None, settings,
                                 extra={"sprint": combined_meta})
        except JiraError as exc:
            st.error(f"Jira{f' ({exc.status})' if exc.status else ''}: {exc}")


def refresh(store: Store, meta: dict, settings: JiraSettings) -> None:
    """Re-fetch a sprint dataset using its saved metadata."""
    sprint = meta.get("sprint", {})
    try:
        client = JiraClient(settings)
        sc = SprintClient(client)
        if sprint.get("mode") == "multi":
            sprint_ids = sprint.get("sprint_ids", [])
            issues = sc.multi_sprint_issues(sprint_ids, max_issues=10000, on_progress=_progress())
        elif sprint.get("id"):
            issues = sc.sprint_issues_by_id(sprint["id"], max_issues=5000, on_progress=_progress())
        else:
            st.warning("Cannot refresh — sprint metadata missing.")
            return
        if not issues:
            st.warning("No issues found on refresh.")
            return
        _save_sprint_dataset(store, meta["name"], issues, client, sprint if sprint.get("id") else None,
                             settings, extra={"sprint": sprint})
    except JiraError as exc:
        st.error(f"Jira{f' ({exc.status})' if exc.status else ''}: {exc}")


def render(store: Store, settings: JiraSettings) -> None:
    ss = st.session_state
    with st.expander("🏃 New dataset from Sprint", expanded=False):
        if not settings.configured:
            st.caption("⚪ Jira not configured — set it in **Config → Zephyr** "
                       "(or JIRA_URL / JIRA_API_TOKEN).")
            return
        st.caption("Pull Jira issues for a sprint — builds sprint reports: burndown, velocity, "
                   "status breakdown, and carry-over analysis.")
        mode = st.radio("Mode", ["single", "multi"], key="sp_mode", horizontal=True,
                        format_func={"single": "Single sprint", "multi": "Multi-sprint (cross-team)"}.get)
        ds_name = st.text_input("Dataset name", key="sp_name", placeholder="Sprint 42 report",
                                help="Re-using a name replaces that dataset.").strip()
        if mode == "multi":
            _multi_sprint(store, settings, ds_name)
        else:
            _single_sprint(store, settings, ds_name)
