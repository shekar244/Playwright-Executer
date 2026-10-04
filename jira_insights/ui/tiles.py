"""
✎ Edit tiles — the KPI tile editor on the dashboard.

Drag tiles to reorder them, pick one to change its label, icon and colour and
the data behind it (measure, aggregation, filters, an optional "divide by"
metric for ratios, and the caption), add new tiles, delete or reset to the
defaults. A live preview shows the tile against the current filtered data.
"""
from __future__ import annotations

import hashlib
import json

import pandas as pd
import streamlit as st
from streamlit_sortables import sort_items

from ..kpi import CAPTIONS, KpiSpec, Metric, evaluate, is_available
from ..pivot import AGGREGATIONS, distinct_values
from ..store import Store
from .common import Dataset
from .style import ACCENTS, SORTABLE_CSS, card_title, stat_tiles_html

_METRICS = (("m", "metric"), ("d", "divide_by"), ("c", "caption_metric"))


# ── Metric editor (measure · aggregation · filters) ───────────────────────────

def _load_metric(prefix: str, metric: Metric | None, ds: Dataset) -> None:
    ss = st.session_state
    metric = metric or Metric()
    ss[f"{prefix}_value"] = metric.value if metric.value in ds.numeric_cols else ""
    ss[f"{prefix}_agg"] = metric.agg if metric.agg in AGGREGATIONS else "Count"
    ss[f"{prefix}_fcols"] = [c for c in metric.filters if c in ds.df.columns]
    for col, wanted in metric.filters.items():
        if isinstance(wanted, dict) and wanted.get("last_days"):
            ss[f"{prefix}_fd_{col}"] = int(wanted["last_days"])
        elif isinstance(wanted, dict) and wanted.get("contains"):
            ss[f"{prefix}_fc_{col}"] = str(wanted["contains"])
        elif isinstance(wanted, list):
            ss[f"{prefix}_fv_{col}"] = [str(v) for v in wanted]


def _metric_editor(prefix: str, ds: Dataset) -> Metric:
    ss = st.session_state
    c1, c2 = st.columns(2)
    value = c1.selectbox("Measure", ["", *ds.numeric_cols], key=f"{prefix}_value",
                         format_func=lambda c: c or "Issue count")
    agg = c2.selectbox("Aggregation", list(AGGREGATIONS), key=f"{prefix}_agg", disabled=not value)
    cols = st.multiselect("Only issues where…", [c for c in ds.df.columns if c not in ("Key", "Summary")],
                          key=f"{prefix}_fcols", placeholder="All issues — add a filter field")
    filters: dict = {}
    for col in cols:
        if col in ds.date_cols:
            ss.setdefault(f"{prefix}_fd_{col}", 30)
            days = st.number_input(f"{col} · last N days", 1, 3650, step=1, key=f"{prefix}_fd_{col}")
            filters[col] = {"last_days": int(days)}
            continue
        v, t = st.columns([3, 2])
        options = distinct_values(ds.df, col, ds.multi_cols)
        options += [x for x in ss.get(f"{prefix}_fv_{col}", []) if x not in options]
        values = v.multiselect(col, options, key=f"{prefix}_fv_{col}", placeholder="Any value")
        contains = t.text_input("…or contains", key=f"{prefix}_fc_{col}", placeholder="e.g. bug|defect",
                                help="Case-insensitive text match; use | for alternatives. Overrides the values list.")
        if contains.strip():
            filters[col] = {"contains": contains.strip()}
        elif values:
            filters[col] = list(values)
    return Metric(value, agg if value else "Count", filters)


# ── Tile state ────────────────────────────────────────────────────────────────

def _load_tile(tile: KpiSpec, ds: Dataset, marker: tuple) -> None:
    ss = st.session_state
    for stale in [k for k in ss if str(k).startswith(("kpi_m_", "kpi_d_", "kpi_c_"))]:
        del ss[stale]
    ss.kpi_label, ss.kpi_icon = tile.label, tile.icon
    ss.kpi_accent = tile.accent if str(tile.accent).startswith("#") and len(tile.accent) == 7 else ACCENTS[0]
    ss.kpi_ratio = tile.divide_by is not None
    ss.kpi_caption = tile.caption if tile.caption in CAPTIONS else "share"
    ss.kpi_caption_text = tile.caption_text
    for prefix, attr in _METRICS:
        _load_metric(f"kpi_{prefix}", getattr(tile, attr), ds)
    ss.kpi_loaded = marker


def _tile_from_state(tile_id: str, ds: Dataset) -> KpiSpec:
    ss = st.session_state
    c1, c2, c3 = st.columns([3, 1, 1])
    label = c1.text_input("Label", key="kpi_label")
    icon = c2.text_input("Icon", key="kpi_icon", max_chars=4, help="Any emoji, e.g. 🐞 📘 🎯")
    accent = c3.color_picker("Colour", key="kpi_accent")

    st.markdown("**Value**")
    metric = _metric_editor("kpi_m", ds)
    divide = None
    if st.checkbox("Divide by another metric (ratio, e.g. defects ÷ stories)", key="kpi_ratio"):
        st.markdown("**Divide by**")
        divide = _metric_editor("kpi_d", ds)

    caption = st.radio("Caption", list(CAPTIONS), key="kpi_caption", horizontal=True, format_func=CAPTIONS.get)
    caption_metric = None
    if caption in ("text", "metric"):
        st.text_input("Caption text" if caption == "text" else "Suffix after the second metric",
                      key="kpi_caption_text", placeholder="e.g. delivered")
    if caption == "metric":
        st.markdown("**Second metric**")
        caption_metric = _metric_editor("kpi_c", ds)
    return KpiSpec(label.strip() or "Untitled", icon.strip() or "📊", accent, metric, divide, caption,
                   ss.get("kpi_caption_text", ""), caption_metric, tile_id)


# ── Editor panel ──────────────────────────────────────────────────────────────

def _labels(tiles: list[KpiSpec]) -> dict[str, str]:
    out, used = {}, {}
    for t in tiles:
        base = f"{t.icon} {t.label}"
        used[base] = used.get(base, 0) + 1
        out[t.id] = base if used[base] == 1 else f"{base} · {used[base]}"
    return out


def render(store: Store, ds: Dataset, df: pd.DataFrame) -> None:
    ss = st.session_state
    if "kpi_flash" in ss:
        st.toast(ss.pop("kpi_flash"), icon="✅")
    tiles = store.list_kpis()
    with st.container(key="card-tiles-editor"):
        head, add, reset = st.columns([5, 1.2, 1.3], vertical_alignment="center")
        with head:
            card_title("Edit KPI tiles")
        if add.button("＋ Add tile", key="kpi-add", width="stretch"):
            new = KpiSpec(accent=ACCENTS[len(tiles) % len(ACCENTS)]).with_id()
            store.save_kpis(tiles + [new])
            ss.kpi_pick = new.id
            st.rerun()
        if reset.button("↺ Reset tiles", key="kpi-reset", width="stretch"):
            store.reset_kpis()
            ss.pop("kpi_pick", None)
            ss.pop("kpi_loaded", None)
            st.rerun()
        if not tiles:
            st.info("No tiles — add one, or reset to the defaults.")
            return

        labels = _labels(tiles)
        st.caption("Drag to reorder. Pick a tile below to change its label, icon, colour and the data behind it.")
        version = hashlib.sha1(json.dumps(list(labels.values())).encode()).hexdigest()[:10]
        order = sort_items(list(labels.values()), direction="horizontal", custom_style=SORTABLE_CSS,
                           key=f"kpi-order-{version}")
        if order != list(labels.values()):
            by_label = {lbl: rid for rid, lbl in labels.items()}
            by_id = {t.id: t for t in tiles}
            store.save_kpis([by_id[by_label[lbl]] for lbl in order if lbl in by_label])
            st.rerun()

        by_id = {t.id: t for t in tiles}
        if ss.get("kpi_pick") not in by_id:
            ss.kpi_pick = tiles[0].id
        pick = st.selectbox("Tile to edit", list(by_id), key="kpi_pick", format_func=labels.get)
        marker = (pick, ds.slug, by_id[pick].to_dict().__repr__())
        if ss.get("kpi_loaded") != marker:
            _load_tile(by_id[pick], ds, marker)

        edited = _tile_from_state(pick, ds)
        if is_available(edited, ds.df):
            value, caption = evaluate(edited, df, len(ds.df), ds.multi_cols)
            st.markdown(stat_tiles_html([(edited.icon, edited.label, value, caption or "Preview", edited.accent)]),
                        unsafe_allow_html=True)
        else:
            st.caption("Preview unavailable — this dataset lacks a column the tile uses.")

        c1, c2, _ = st.columns([1, 1, 2])
        if c1.button("💾 Save tile", key="kpi-save", type="primary", width="stretch"):
            store.save_kpis([edited if t.id == pick else t for t in tiles])
            ss.pop("kpi_loaded", None)
            ss.kpi_flash = f"Saved “{edited.label}”"
            st.rerun()
        if c2.button("🗑 Delete tile", key="kpi-delete", width="stretch"):
            store.save_kpis([t for t in tiles if t.id != pick])
            ss.pop("kpi_pick", None)
            st.rerun()
