"""
Explorer: PyGWalker's drag-and-drop canvas (Tableau-style) over the active
dataset. Charts are saved per dataset to workspace/specs/<slug>.json with
the save button in the PyGWalker toolbar.

PyGWalker's Streamlit bridge attaches to Streamlit's Tornado server
(Streamlit < 1.57). On newer Streamlit it falls back to a read-only embed.
"""
from __future__ import annotations

import streamlit as st

from ..store import Store
from .common import Dataset


@st.cache_resource(show_spinner="Preparing explorer…", max_entries=4)
def _renderer(_df, slug: str, version: str, spec_path: str):
    """`slug` + `version` key the cache; the frame itself is not hashed."""
    from pygwalker.api.streamlit import StreamlitRenderer
    return StreamlitRenderer(_df, spec=spec_path, spec_io_mode="rw", appearance="dark",
                             kernel_computation=True)


def render(store: Store, ds: Dataset) -> None:
    try:
        import pygwalker
        from pygwalker import GlobalVarManager
        from pygwalker.errors import StreamlitPygwalkerApiError
    except ImportError:
        st.warning("PyGWalker is not installed — run `pip install -r requirements.txt`.")
        return
    GlobalVarManager.set_privacy("offline")      # no telemetry from a Jira workspace

    spec_path = str(store.spec_path(ds.slug))
    st.caption("Drag fields onto the X / Y shelves, colour, size and filters to build any chart. "
               "Use 💾 in the toolbar to keep your charts for this dataset.")
    try:
        renderer = _renderer(ds.df, ds.slug, ds.meta.get("fetched_at", ""), spec_path)
    except (ImportError, StreamlitPygwalkerApiError) as exc:   # Streamlit server it can't attach to
        st.info(f"Interactive saving is unavailable on this Streamlit version ({type(exc).__name__}); "
                "showing a read-only explorer. Install `streamlit<1.57` for full support.")
        html = pygwalker.to_html(ds.df, spec=spec_path, appearance="dark")
        if hasattr(st, "iframe"):                # st.components.v1.html is deprecated in newer Streamlit
            st.iframe(html, height=900)
        else:
            import streamlit.components.v1 as components
            components.html(html, height=900, scrolling=True)
        return
    renderer.explorer(key=f"gw-{ds.slug}")
