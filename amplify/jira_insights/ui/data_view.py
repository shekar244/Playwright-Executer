"""Data: the raw issue table under the global filter bar, with Jira links and downloads."""
from __future__ import annotations

import streamlit as st

from .common import Dataset, download_buttons, filter_bar


def render(ds: Dataset) -> None:
    filtered = filter_bar(ds, key="data")
    view = filtered
    column_config = {}
    base = (ds.meta.get("jira_url") or "").rstrip("/")
    if base and "Key" in view.columns:
        view = filtered.assign(Key=base + "/browse/" + filtered["Key"].astype(str))
        column_config["Key"] = st.column_config.LinkColumn("Key", display_text=r".*/browse/(.+)$")
    st.dataframe(view, width="stretch", height=560, hide_index=True, column_config=column_config)
    download_buttons(filtered, ds.meta.get("name", ds.slug), key="data-dl")
