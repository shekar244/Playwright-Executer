"""Data: the raw issue table under the global filter bar, with Jira links and downloads."""
from __future__ import annotations

import streamlit as st

from .common import Dataset, download_buttons, filter_bar, with_issue_links


def render(ds: Dataset) -> None:
    filtered = filter_bar(ds, key="data")
    view, column_config = with_issue_links(filtered, ds.meta.get("jira_url"))
    st.dataframe(view, width="stretch", height=560, hide_index=True, column_config=column_config)
    download_buttons(filtered, ds.meta.get("name", ds.slug), key="data-dl")
