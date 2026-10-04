"""
👥 Tester names — Zephyr often reports testers and assignees as user ids
(Atlassian account ids on Cloud, JIRAUSER keys on Server / DC). Names are looked
up from Jira when test runs are fetched and kept in the workspace settings
("user_names"), where they can be corrected or filled in by hand. Datasets keep
the raw ids; names are applied when a dataset is shown, so edits apply at once.
"""
from __future__ import annotations

from typing import Callable

import pandas as pd
import streamlit as st

from ..executions import user_ids
from ..store import Store

SETTING = "user_names"
Lookup = Callable[[list], dict]


def known_names(store: Store) -> dict:
    names = store.get_setting(SETTING, {}) or {}
    return names if isinstance(names, dict) else {}


def remember(store: Store, found: dict) -> None:
    if found:
        store.set_setting(SETTING, {**known_names(store), **{k: v for k, v in found.items() if v}})


def resolve_missing(store: Store, raw_df: pd.DataFrame, lookup: Lookup | None) -> int:
    """Look up names for ids not named yet; returns how many were found. Never raises."""
    if lookup is None:
        return 0
    names = known_names(store)
    missing = [i for i in user_ids(raw_df) if not names.get(i)]
    if not missing:
        return 0
    try:
        found = lookup(missing) or {}
    except Exception:                       # lookups are best effort — names can be typed in instead
        found = {}
    remember(store, found)
    return len(found)


def render(store: Store, raw_df: pd.DataFrame, lookup: Lookup | None) -> None:
    ids = user_ids(raw_df)
    if not ids:
        return
    names = known_names(store)
    named = sum(1 for i in ids if names.get(i))
    with st.expander(f"👥 Tester names · {named}/{len(ids)} named", expanded=named < len(ids)):
        st.caption("Zephyr sends user ids for Executed By / Assignee. Names come from Jira — "
                   "edit any of them here; every report updates.")
        if st.button("🔍 Look up missing names", key="people-lookup", width="stretch",
                     disabled=lookup is None or named == len(ids)):
            found = resolve_missing(store, raw_df, lookup)
            st.session_state["people_flash"] = (f"Found {found} name(s) in Jira" if found
                                                else "Jira returned no names — type them in below")
            st.rerun()
        if "people_flash" in st.session_state:
            st.caption(st.session_state.pop("people_flash"))
        table = pd.DataFrame({"User id": ids, "Name": [names.get(i, "") for i in ids]})
        edited = st.data_editor(table, key=f"people-editor-{len(ids)}-{named}", hide_index=True,
                                disabled=["User id"], width="stretch",
                                column_config={"Name": st.column_config.TextColumn(
                                    help="Display name used in every report — leave empty to show the id")})
        if st.button("Save names", key="people-save", type="primary", width="stretch"):
            updated = dict(names)
            for uid, name in zip(edited["User id"], edited["Name"]):
                name = str(name or "").strip()
                if name:
                    updated[uid] = name
                else:
                    updated.pop(uid, None)
            store.set_setting(SETTING, updated)
            st.rerun()
