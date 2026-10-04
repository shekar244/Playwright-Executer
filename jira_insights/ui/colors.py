"""
🎨 Colours — per report: Auto (theme palette, plus any defaults saved for the
field) or Manual (a colour picker for every category in the chart, or one picker
for one-colour charts). Manual colours can also be saved as the default for the
field (e.g. Issue Type: Bug → red), which every Auto report then uses.

Dials use their colour bands, heatmaps the theme ramp and Number tiles their
accent, so those charts have no picker here.
"""
from __future__ import annotations

import hashlib

import streamlit as st

from ..charts import (DEFAULT_THEME, MANUAL_COLOR_CHARTS, SINGLE_COLOR, apply_overrides, color_field,
                      colorable)
from ..pivot import ReportSpec, category_rank
from ..store import Store
from .common import Dataset, prepare_report

SETTING = "category_colors"          # {field: {value: "#rrggbb"}}
MAX_PICKERS = 16
_NOT_MANUAL = {"Gauge": "Dials use the colour bands in their settings.",
               "Meter": "Dials use the colour bands in their settings.",
               "Heatmap": "Heatmaps use the theme's colour ramp.",
               "Number": "Number tiles use their card accent colour."}
_MODES = {"auto": "Auto — theme palette", "manual": "Manual — pick each colour"}


def load_state(spec: ReportSpec) -> None:
    """Called when the builder loads a report: restore its colour mode and picks."""
    ss = st.session_state
    for stale in [k for k in ss if str(k).startswith("rb_c_")]:
        del ss[stale]
    ss.rb_cmode = spec.color_mode if spec.color_mode in _MODES else "auto"
    ss.rb_loaded_colors = dict(spec.colors or {})


def _key(field: str, value: str) -> str:
    return "rb_c_" + hashlib.md5(f"{field}|{value}".encode("utf-8")).hexdigest()[:12]


def render(store: Store, spec: ReportSpec, ds: Dataset) -> ReportSpec:
    ss = st.session_state
    ss.setdefault("rb_cmode", "auto")
    with st.expander("🎨 Colours", expanded=ss.rb_cmode == "manual"):
        if spec.chart not in MANUAL_COLOR_CHARTS:
            st.caption(_NOT_MANUAL.get(spec.chart, "This chart type has no colour options."))
            spec.color_mode, spec.colors = "auto", {}
            return spec
        mode = st.radio("Colours", list(_MODES), key="rb_cmode", horizontal=True, format_func=_MODES.get,
                        label_visibility="collapsed")
        result = prepare_report(spec, ds).result
        if result is None or result.empty:
            st.caption("Colours appear once the report has data.")
            return spec

        defaults = store.get_setting(SETTING, {}) or {}
        field = color_field(spec, result)
        order = category_rank(ds.df, field, ds.multi_cols) if field else []
        auto = colorable(result, spec, order, ss.get("ji_theme", DEFAULT_THEME))
        auto = apply_overrides(auto, spec, defaults.get(field, {})) if field else auto
        if mode == "auto":
            spec.color_mode, spec.colors = "auto", {}
            if field and defaults.get(field):
                st.caption(f"Theme palette, with your default colours for **{field}**.")
            return spec

        loaded = ss.get("rb_loaded_colors", {})
        picks: dict[str, str] = {}
        cols = st.columns(2)
        for i, value in enumerate(list(auto)[:MAX_PICKERS]):
            key = _key(field, value)
            ss.setdefault(key, loaded.get(str(value), auto[value]))
            label = "Every mark" if value == SINGLE_COLOR else str(value)
            picks[str(value)] = cols[i % 2].color_picker(label, key=key)
        if len(auto) > MAX_PICKERS:
            st.caption(f"The first {MAX_PICKERS} categories are shown; the rest keep their automatic colours.")
        spec.color_mode, spec.colors = "manual", picks

        if field:
            c1, c2 = st.columns(2)
            if c1.button(f"Use for every {field} chart", key="rb-cdefault", width="stretch",
                         help=f"Save these as the default {field} colours — Auto reports use them too"):
                updated = dict(defaults)
                updated[field] = {**updated.get(field, {}), **{k: v for k, v in picks.items() if k != SINGLE_COLOR}}
                store.set_setting(SETTING, updated)
                ss.rb_flash = f"Saved default colours for {field}"
                st.rerun()
            if c2.button(f"Clear {field} defaults", key="rb-cclear", width="stretch",
                         disabled=not defaults.get(field)):
                updated = {k: v for k, v in defaults.items() if k != field}
                store.set_setting(SETTING, updated)
                ss.rb_flash = f"Cleared default colours for {field}"
                st.rerun()
    return spec
