"""
Visual layer for Jira Insights: page CSS (Apple-style flat surfaces, soft
shadows, pill controls) and small HTML components — brand mark,
hero header, card titles, and stat tiles.

Everything user-provided (dataset and report names) is HTML-escaped.
"""
from __future__ import annotations

import html
import re
import zlib

import streamlit as st

# Brand accents for chrome (tiles, card dots) — the Apple system palette.
ACCENTS = ("#0a84ff", "#bf5af2", "#64d2ff", "#ff9f0a", "#ff453a", "#ffd60a", "#30d158", "#5e5ce6")

_COMPONENTS = """
  .block-container { padding-top: 3.75rem; padding-bottom: 2.5rem; }  /* clears the fixed Streamlit header */
  .stApp, .stApp button, .stApp input, .stApp textarea { font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", Inter, system-ui, sans-serif; letter-spacing: -.006em; }

  /* ── Brand + hero ── */
  .ji-brand { display: flex; align-items: center; gap: 10px; margin: 0 0 8px; }
  .ji-brand-mark {
    width: 30px; height: 30px; border-radius: 8px; display: grid; place-items: center; font-size: 15px;
    background: linear-gradient(180deg, #4da3ff, #0071e3); box-shadow: 0 1px 2px rgba(0,0,0,.2), inset 0 .5px 0 rgba(255,255,255,.4);
  }
  .ji-brand-name { font-size: 18px; font-weight: 600; letter-spacing: -.02em; color: var(--ji-text) !important; }
  .ji-hero-title { font-size: 28px; font-weight: 700; line-height: 1.12; letter-spacing: -.03em; color: var(--ji-text) !important; }
  .ji-chips { display: flex; gap: 6px; flex-wrap: wrap; margin: 8px 0 16px; }
  .ji-chip { font-size: 12px; font-weight: 500; color: var(--ji-muted) !important; padding: 3px 11px; border-radius: 999px; background: var(--ji-chip); }
  .ji-swatches { display: flex; gap: 5px; margin: -4px 0 6px; }
  .ji-swatches span { width: 16px; height: 16px; border-radius: 50%; box-shadow: inset 0 0 0 .5px rgba(0,0,0,.15); }

  /* ── Cards ── */
  div[class*="st-key-card-"] {
    background: var(--ji-surface) !important; border: .5px solid var(--ji-border) !important; border-radius: 18px;
    padding: 16px 18px 10px; box-shadow: var(--ji-shadow) !important; transition: box-shadow .2s ease;
  }
  div[class*="st-key-card-"]:hover { box-shadow: 0 2px 6px rgba(0,0,0,.08), 0 10px 28px rgba(0,0,0,.08) !important; }
  .ji-card-title {
    font-size: 12px; font-weight: 600; letter-spacing: .04em; text-transform: uppercase; color: var(--ji-muted) !important;
    display: flex; align-items: center; gap: 9px; min-height: 32px;
  }
  .ji-card-title .ji-code {
    font: 500 10px "SF Mono", ui-monospace, Menlo, monospace; letter-spacing: 0; text-transform: none;
    color: var(--ji-muted) !important; background: var(--ji-chip); border-radius: 6px; padding: 1px 6px;
  }
  .ji-card-title .ji-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; background: var(--a); }

  /* ── Stat tiles ── */
  .ji-stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin: 6px 0 10px; }
  .ji-stat {
    border-radius: 18px; padding: 14px 16px 13px; background: var(--ji-surface) !important;
    border: .5px solid var(--ji-border) !important; box-shadow: var(--ji-shadow); transition: box-shadow .2s ease;
  }
  .ji-stat:hover { box-shadow: 0 2px 6px rgba(0,0,0,.08), 0 10px 28px rgba(0,0,0,.08); }
  .ji-stat-head { display: flex; align-items: center; gap: 8px; color: var(--ji-muted) !important; font-size: 12px; font-weight: 500; }
  .ji-stat-icon { width: 26px; height: 26px; border-radius: 8px; display: grid; place-items: center; font-size: 14px;
    background: color-mix(in srgb, var(--a) 18%, transparent); }
  .ji-stat-value, .ji-number-value { font-weight: 700; letter-spacing: -.035em; line-height: 1.05; color: var(--ji-text) !important; }
  .ji-stat-value { font-size: 32px; margin-top: 9px; }
  .ji-stat-sub, .ji-number-sub { font-size: 12px; color: var(--ji-muted) !important; margin-top: 4px; }
  .ji-number { text-align: center; padding: 18px 0 16px; display: flex; flex-direction: column; justify-content: center; }
  .ji-number-value { font-size: 52px; }

  /* ── Navigation, pills and buttons ── */
  button[data-testid="stBaseButton-segmented_control"], button[data-testid="stBaseButton-pills"] {
    background: var(--ji-chip) !important; border-color: transparent !important; border-radius: 999px;
  }
  button[data-testid="stBaseButton-segmented_controlActive"], button[data-testid="stBaseButton-pillsActive"],
  button[data-testid="stBaseButton-primary"], button[data-testid="stBaseButton-primaryFormSubmit"] {
    background: var(--ji-accent) !important; border-color: transparent !important; box-shadow: none; border-radius: 999px;
  }
  button[data-testid="stBaseButton-segmented_controlActive"] *, button[data-testid="stBaseButton-pillsActive"] *,
  button[data-testid="stBaseButton-primary"] *, button[data-testid="stBaseButton-primaryFormSubmit"] * {
    color: #ffffff !important; font-weight: 600;
  }
  button[data-testid="stBaseButton-primary"]:hover, button[data-testid="stBaseButton-primaryFormSubmit"]:hover { filter: brightness(1.1); }
  button[data-testid="stBaseButton-secondary"] { border-radius: 999px; }

  /* ── Containers ── */
  [data-testid="stExpander"] details { border-radius: 14px; border-color: var(--ji-border) !important; background: var(--ji-surface) !important; }
  [data-testid="stDataFrame"] { border-radius: 14px; overflow: hidden; border: .5px solid var(--ji-border) !important; }
  div[data-baseweb="select"] > div, div[data-baseweb="input"], div[data-baseweb="textarea"] { border-radius: 10px !important; }

  /* ── Theme toggle ── */
  .ji-theme-toggle { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--ji-muted); font-weight: 500; }
"""

_CSS_DARK = "<style>" + """
  :root, .stApp {
    --ji-bg: #000000; --ji-surface: #1c1c1e; --ji-surface2: #2c2c2e; --ji-border: rgba(255,255,255,.12);
    --ji-text: #f5f5f7; --ji-muted: #98989d; --ji-chip: rgba(120,120,128,.24); --ji-accent: #0a84ff;
    --ji-shadow: 0 1px 2px rgba(0,0,0,.5);
  }
  .stApp { background: #000000; }
  [data-testid="stHeader"] { background: #000000; }
  [data-testid="stSidebar"] { background: #1c1c1e; border-right: 0.5px solid rgba(255,255,255,.12); }
  /* Keep widgets legible when dark is chosen inside the app while Streamlit's own base theme is light. */
  .stApp, .stApp p, .stApp li, .stApp label, .stApp h1, .stApp h2, .stApp h3, .stApp h4, .stApp h5,
  .stApp [data-testid="stMarkdownContainer"], .stApp [data-testid="stMetricValue"] { color: var(--ji-text); }
  .stApp .stCaption, .stApp small { color: var(--ji-muted); }
  .stApp div[data-baseweb="select"] > div, .stApp div[data-baseweb="input"], .stApp div[data-baseweb="input"] > div,
  .stApp div[data-baseweb="textarea"], .stApp div[data-baseweb="textarea"] > div, .stApp textarea,
  .stApp [data-testid="stFileUploaderDropzone"] { background: var(--ji-surface2) !important; color: var(--ji-text); border-color: var(--ji-border) !important; }
  .stApp input { color: var(--ji-text) !important; }
  .stApp div[data-baseweb="popover"] > div, .stApp [data-baseweb="menu"] { background: var(--ji-surface2) !important; color: var(--ji-text); }
  .stApp [data-baseweb="menu"] li { color: var(--ji-text) !important; }
  .stApp button[data-testid="stBaseButton-secondary"] { background: var(--ji-surface2) !important; border-color: var(--ji-border) !important; }
  .stApp button[data-testid="stBaseButton-secondary"] p { color: var(--ji-text) !important; }
  /* Sidebar collapse/expand — every possible button near the sidebar edge */
  [data-testid*="ollapse"], [data-testid*="idebar"] button,
  [data-testid="collapsedControl"], [data-testid="stSidebarCollapsedControl"],
  [data-testid="stHeader"] button,
  button[kind="header"], button[kind="headerNoPadding"] {
    color: #f5f5f7 !important; background: rgba(255,255,255,0.18) !important;
    border: 1px solid rgba(255,255,255,0.30) !important; border-radius: 8px !important;
  }
  [data-testid*="ollapse"] svg, [data-testid*="idebar"] button svg,
  [data-testid="stHeader"] button svg,
  button[kind="header"] svg, button[kind="headerNoPadding"] svg {
    fill: #f5f5f7 !important; stroke: #f5f5f7 !important; color: #f5f5f7 !important;
  }
""" + _COMPONENTS + "</style>"

_CSS_LIGHT = "<style>" + """
  :root, .stApp {
    --ji-bg: #f5f5f7; --ji-surface: #ffffff; --ji-surface2: #f5f5f7; --ji-border: rgba(0,0,0,.10);
    --ji-text: #1d1d1f; --ji-muted: #6e6e73; --ji-chip: rgba(120,120,128,.12); --ji-accent: #0071e3;
    --ji-shadow: 0 1px 2px rgba(0,0,0,.04), 0 2px 12px rgba(0,0,0,.05);
  }
/* ── Global surface override ── */
  .stApp {
    background: #f5f5f7 !important;
    color: #1d1d1f !important;
  }
  [data-testid="stHeader"] { background: #f5f5f7 !important; }
  .block-container { padding-top: 3.75rem; padding-bottom: 2.5rem; }  /* clears the fixed Streamlit header */

  /* ── Sidebar ── */
  [data-testid="stSidebar"] {
    background: #ffffff !important;
    border-right: 1px solid rgba(0,0,0,.08) !important;
  }
  [data-testid="stSidebar"],
  [data-testid="stSidebar"] *:not(.ji-brand-name) { color: #1d1d1f !important; }
  [data-testid="stSidebar"] label { color: #424245 !important; }
  [data-testid="stSidebar"] .stCaption, [data-testid="stSidebar"] .stCaption * { color: #6e6e73 !important; }
  [data-testid="stSidebar"] div[data-baseweb="select"] > div { background: #ffffff !important; border-color: #d2d2d7 !important; }
  [data-testid="stSidebar"] div[data-baseweb="input"] { background: #ffffff !important; border-color: #d2d2d7 !important; }
  [data-testid="stSidebar"] div[data-baseweb="textarea"] > div { background: #ffffff !important; border-color: #d2d2d7 !important; }
  [data-testid="stSidebar"] hr { border-color: #d2d2d7 !important; }
  [data-testid="stSidebar"] [data-testid="stExpander"] details { background: rgba(255,255,255,.6) !important; border-color: #d2d2d7 !important; }
  [data-testid="stSidebar"] [data-testid="stForm"] { border-color: #d2d2d7 !important; }
  [data-testid="stSidebar"] button[data-testid="stBaseButton-secondary"] { background: #ffffff !important; border-color: #d2d2d7 !important; }
  [data-testid="stSidebar"] button[data-testid="stBaseButton-secondary"]:hover { background: #f0f0f5 !important; }
  [data-testid="stSidebarCollapseButton"] button { color: #424245 !important; }
  [data-testid="stSidebarCollapseButton"] svg { fill: #424245 !important; stroke: #424245 !important; }

  /* ── Global text overrides (Streamlit dark theme bleeds through) ──
     Our own .ji-* components take their colours from the --ji-* variables instead. */
  .stApp p, .stApp span:not([class*="ji-"]),
  .stApp div:not([class*="ji-"]),
  .stApp li, .stApp td, .stApp th,
  .stApp [data-testid="stMarkdownContainer"] p,
  .stApp [data-testid="stMarkdownContainer"] li,
  .stApp [data-testid="stMarkdownContainer"] strong,
  .stApp h1, .stApp h2, .stApp h3, .stApp h4, .stApp h5 { color: #1d1d1f !important; }
  .stApp [data-testid="stMarkdownContainer"] a { color: #0071e3 !important; }
  .stApp label { color: #424245 !important; }
  .stApp .stCaption, .stApp .stCaption p, .stApp .stCaption span { color: #6e6e73 !important; }
  .stApp small { color: #6e6e73 !important; }
  .stApp svg { color: #424245; }

  /* ── Alert banners (info, warning, error, success) ── */
  .stApp [data-testid="stAlert"] { color: #1d1d1f !important; }
  .stApp [data-testid="stAlert"] p,
  .stApp [data-testid="stAlert"] span,
  .stApp [data-testid="stAlert"] a { color: #1d1d1f !important; }
  .stApp [data-testid="stAlert"] a { text-decoration: underline; }
  .stApp [data-testid="stNotification"] { color: #1d1d1f !important; }
  .stApp [data-testid="stNotification"] p,
  .stApp [data-testid="stNotification"] span { color: #1d1d1f !important; }
  .stApp .stAlert { color: #1d1d1f !important; }
  .stApp .stAlert p, .stApp .stAlert span { color: #1d1d1f !important; }

  /* ── Streamlit CSS variable overrides (the dark config.toml injects these) ── */
  .stApp {
    --background-color: #f5f5f7; --secondary-background-color: #e8e8ed;
    --text-color: #1d1d1f; --font: -apple-system, BlinkMacSystemFont, 'SF Pro Text', Inter, system-ui, sans-serif;
    --primary-color: #0071e3;
  }

  /* ── Inputs ── */
  .stApp div[data-baseweb="select"] > div { background: #ffffff !important; border-color: #d2d2d7 !important; color: #1d1d1f !important; }
  .stApp div[data-baseweb="select"] span { color: #1d1d1f !important; }
  .stApp div[data-baseweb="input"],
  .stApp div[data-baseweb="input"] > div { background: #ffffff !important; border-color: #d2d2d7 !important; }
  .stApp div[data-baseweb="textarea"],
  .stApp div[data-baseweb="textarea"] > div { background: #ffffff !important; border-color: #d2d2d7 !important; }
  .stApp [data-testid="stTextInput"] > div { background: transparent !important; }
  .stApp [data-testid="stTextInput"] > div > div { background: #ffffff !important; border-color: #d2d2d7 !important; border-radius: 10px; }
  .stApp [data-testid="stTextArea"] > div > div { background: #ffffff !important; border-color: #d2d2d7 !important; }
  .stApp [data-testid="stNumberInput"] > div > div { background: #ffffff !important; border-color: #d2d2d7 !important; }
  .stApp [data-testid="stSelectbox"] > div > div { background: #ffffff !important; border-color: #d2d2d7 !important; }
  .stApp [data-testid="stMultiSelect"] > div > div { background: #ffffff !important; border-color: #d2d2d7 !important; }
  .stApp input { color: #1d1d1f !important; background: transparent !important; }
  .stApp textarea { color: #1d1d1f !important; background: #ffffff !important; }
  .stApp [data-baseweb="tag"] { background: #e1efff !important; color: #1d1d1f !important; }
  .stApp [data-baseweb="tag"] span { color: #1d1d1f !important; }
  .stApp ::placeholder { color: #aeaeb2 !important; }

  /* ── Dropdown menus (selectbox, multiselect popups) ── */
  .stApp div[data-baseweb="popover"] > div { background: #ffffff !important; border-color: #d2d2d7 !important; box-shadow: 0 4px 16px rgba(0,0,0,.10) !important; }
  .stApp div[data-baseweb="popover"] li { color: #1d1d1f !important; }
  .stApp div[data-baseweb="popover"] li:hover { background: #eef5ff !important; }
  .stApp ul[data-testid="stSelectboxVirtualDropdown"] { background: #ffffff !important; border-color: #d2d2d7 !important; }
  .stApp ul[data-testid="stSelectboxVirtualDropdown"] li { color: #1d1d1f !important; }
  .stApp ul[data-testid="stSelectboxVirtualDropdown"] li:hover,
  .stApp ul[data-testid="stSelectboxVirtualDropdown"] li[aria-selected="true"] { background: #eef5ff !important; }
  .stApp [data-baseweb="menu"] { background: #ffffff !important; }
  .stApp [data-baseweb="menu"] li { color: #1d1d1f !important; }
  .stApp [data-baseweb="menu"] li:hover { background: #eef5ff !important; }

  /* ── Code blocks ── */
  .stApp [data-testid="stCode"],
  .stApp pre, .stApp code {
    background: #e8e8ed !important; color: #1d1d1f !important; border-color: #d2d2d7 !important;
  }
  .stApp .stCodeBlock { background: #e8e8ed !important; }
  .stApp .stCodeBlock pre { background: #e8e8ed !important; color: #1d1d1f !important; }
  .stApp .stCodeBlock code { background: transparent !important; color: #1d1d1f !important; }

  /* ── File uploader ── */
  .stApp [data-testid="stFileUploader"] { background: transparent !important; }
  .stApp [data-testid="stFileUploader"] section { background: #ffffff !important; border-color: #d2d2d7 !important; }
  .stApp [data-testid="stFileUploader"] section > div { color: #424245 !important; }
  .stApp [data-testid="stFileUploader"] button { color: #0071e3 !important; }
  .stApp [data-testid="stFileUploaderDropzone"] { background: #fafafc !important; border-color: #d2d2d7 !important; }
  .stApp [data-testid="stFileUploaderDropzone"] span { color: #424245 !important; }
  .stApp [data-testid="stFileUploaderDropzone"] small { color: #6e6e73 !important; }

  /* ── Form containers ── */
  .stApp [data-testid="stForm"] { border-color: #d2d2d7 !important; background: transparent !important; }

  /* ── Toggle switch ── */
  .stApp [data-testid="stToggle"] label span { color: #1d1d1f !important; }

  /* ── Segmented control & pill buttons — inactive text ── */
  button[data-testid="stBaseButton-segmented_control"] { background: rgba(255,255,255,.85) !important; border-color: rgba(0,0,0,.10) !important; }
  button[data-testid="stBaseButton-segmented_control"] p,
  button[data-testid="stBaseButton-segmented_control"] span { color: #424245 !important; }
  button[data-testid="stBaseButton-pills"] { background: rgba(255,255,255,.85) !important; border-color: rgba(0,0,0,.10) !important; }
  button[data-testid="stBaseButton-pills"] p,
  button[data-testid="stBaseButton-pills"] span { color: #424245 !important; }

  /* ── Active buttons ── */
  button[data-testid="stBaseButton-segmented_controlActive"], button[data-testid="stBaseButton-pillsActive"],
  button[data-testid="stBaseButton-primary"], button[data-testid="stBaseButton-primaryFormSubmit"] {
    background: #0071e3 !important;
    border-color: transparent !important; box-shadow: none;
  }
  button[data-testid="stBaseButton-segmented_controlActive"] *, button[data-testid="stBaseButton-pillsActive"] *,
  button[data-testid="stBaseButton-primary"] *, button[data-testid="stBaseButton-primaryFormSubmit"] * {
    color: #ffffff !important; font-weight: 700;
  }
  button[data-testid="stBaseButton-primary"]:hover, button[data-testid="stBaseButton-primaryFormSubmit"]:hover {
    filter: brightness(1.08);
  }

  /* ── Secondary / tertiary buttons ── */
  button[data-testid="stBaseButton-secondary"] { background: #ffffff !important; border-color: #d2d2d7 !important; }
  button[data-testid="stBaseButton-secondary"] p, button[data-testid="stBaseButton-secondary"] span { color: #424245 !important; }
  button[data-testid="stBaseButton-secondary"]:hover { border-color: rgba(0,113,227,.45) !important; background: #f0f0f5 !important; }
  button[data-testid="stBaseButton-tertiary"] p, button[data-testid="stBaseButton-tertiary"] span { color: #424245 !important; }
  button[data-testid="stBaseButton-tertiary"]:hover { background: #f0f0f5 !important; }

  /* ── Download buttons ── */
  .stApp .stDownloadButton button { background: #ffffff !important; border-color: #d2d2d7 !important; }
  .stApp .stDownloadButton button p, .stApp .stDownloadButton button span { color: #424245 !important; }
  .stApp .stDownloadButton button:hover { border-color: rgba(0,113,227,.45) !important; background: #f0f0f5 !important; }

  /* ── Containers ── */
  [data-testid="stExpander"] details {
    border-radius: 12px; border-color: #d2d2d7 !important; background: rgba(255,255,255,.75) !important;
  }
  [data-testid="stExpander"] summary span { color: #1d1d1f !important; }
  [data-testid="stExpander"] summary svg { color: #424245 !important; }
  [data-testid="stDataFrame"] { border-radius: 12px; overflow: hidden; border: 1px solid #d2d2d7 !important; }
  div[data-baseweb="select"] > div, div[data-baseweb="input"], div[data-baseweb="textarea"] { border-radius: 10px !important; }

  /* ── Popover / tooltip ── */
  .stApp [data-testid="stPopover"] > div { background: #ffffff !important; border-color: #d2d2d7 !important; }
  .stApp [data-testid="stTooltipContent"] { background: #ffffff !important; color: #1d1d1f !important; border: 1px solid #d2d2d7 !important; }

  /* ── Tabs ── */
  .stApp .stTabs [data-baseweb="tab-list"] { border-bottom-color: #d2d2d7 !important; }
  .stApp .stTabs [data-baseweb="tab"] { color: #424245 !important; }
  .stApp .stTabs [aria-selected="true"] { color: #0071e3 !important; border-bottom-color: #0071e3 !important; }

  /* ── Number input spinner ── */
  .stApp [data-testid="stNumberInput"] button { color: #424245 !important; background: #f0f0f5 !important; border-color: #d2d2d7 !important; }
  .stApp [data-testid="stNumberInput"] input { background: #ffffff !important; }

  /* ── Checkbox / radio ── */
  .stApp [data-testid="stCheckbox"] label span { color: #1d1d1f !important; }
  .stApp [data-testid="stRadio"] label span { color: #1d1d1f !important; }

  /* ── Progress bar ── */
  .stApp [data-testid="stProgress"] > div { background: #e5e5ea !important; }

  /* ── Metric widget ── */
  .stApp [data-testid="stMetric"] label { color: #6e6e73 !important; }
  .stApp [data-testid="stMetric"] [data-testid="stMetricValue"] { color: #1d1d1f !important; }

  /* ── Scrollbar ── */
  .stApp ::-webkit-scrollbar-track { background: #ebebf0 !important; }
  .stApp ::-webkit-scrollbar-thumb { background: #c7c7cc !important; }
  .stApp ::-webkit-scrollbar-thumb:hover { background: #aeaeb2 !important; }
""" + _COMPONENTS + "</style>"


# Styles injected into the drag-and-drop component (it renders in its own iframe).
SORTABLE_CSS = """
body { background: transparent; }
.sortable-component { background: transparent; border: none; padding: 0; margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", Inter, system-ui, sans-serif; }
.sortable-container { background: #1c1c1e; border: 1px dashed rgba(255,255,255,.22);
  border-radius: 14px; margin: 0 0 8px; padding: 2px 4px 4px; counter-reset: none; }
.sortable-container:last-child { border-color: rgba(10,132,255,.55); background: rgba(10,132,255,.08); }
.sortable-container-header { background: transparent; color: #98989d; font-size: 11px; font-weight: 600;
  letter-spacing: .05em; text-transform: uppercase; padding: 6px 8px 2px; }
.sortable-container-body { background: transparent; min-height: 46px; }
.sortable-item, .sortable-item:hover {
  background: #2c2c2e; color: #f5f5f7; border: .5px solid rgba(255,255,255,.14); border-radius: 10px;
  font-size: 13px; font-weight: 500; cursor: grab; box-shadow: 0 1px 3px rgba(0,0,0,.4);
}
"""

SORTABLE_CSS_LIGHT = """
body { background: transparent; }
.sortable-component { background: transparent; border: none; padding: 0; margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", Inter, system-ui, sans-serif; }
.sortable-container { background: #ebebf0; border: 1px dashed rgba(0,0,0,.20);
  border-radius: 14px; margin: 0 0 8px; padding: 2px 4px 4px; counter-reset: none; }
.sortable-container:last-child { border-color: rgba(0,113,227,.5); background: rgba(0,113,227,.06); }
.sortable-container-header { background: transparent; color: #6e6e73; font-size: 11px; font-weight: 600;
  letter-spacing: .05em; text-transform: uppercase; padding: 6px 8px 2px; }
.sortable-container-body { background: transparent; min-height: 46px; }
.sortable-item, .sortable-item:hover {
  background: #ffffff; color: #1d1d1f; border: .5px solid rgba(0,0,0,.12); border-radius: 10px;
  font-size: 13px; font-weight: 500; cursor: grab; box-shadow: 0 1px 3px rgba(0,0,0,.10);
}
"""


def inject_css() -> None:
    dark = st.session_state.get("ji_dark_mode", False)
    st.markdown(_CSS_DARK if dark else _CSS_LIGHT, unsafe_allow_html=True)


def get_sortable_css() -> str:
    dark = st.session_state.get("ji_dark_mode", False)
    return SORTABLE_CSS if dark else SORTABLE_CSS_LIGHT


def accent_for(key: str) -> str:
    """Stable accent per report so each card keeps its colour across reruns."""
    return ACCENTS[zlib.crc32(key.encode("utf-8")) % len(ACCENTS)]


def brand() -> None:
    st.markdown('<div class="ji-brand"><div class="ji-brand-mark">📈</div>'
                '<div class="ji-brand-name">Jira Insights</div></div>', unsafe_allow_html=True)


def swatches(colors) -> None:
    dots = "".join(f'<span style="background:{c}"></span>' for c in colors)
    st.markdown(f'<div class="ji-swatches">{dots}</div>', unsafe_allow_html=True)


def hero(title: str, chips: list[str]) -> None:
    chip_html = "".join(f'<span class="ji-chip">{html.escape(c)}</span>' for c in chips)
    st.markdown(f'<div class="ji-hero-title">{html.escape(title)}</div><div class="ji-chips">{chip_html}</div>',
                unsafe_allow_html=True)


def card_title(text: str, accent: str = ACCENTS[0], code: str = "") -> None:
    badge = f'<span class="ji-code">{html.escape(code)}</span>' if code else ""
    st.markdown(f'<div class="ji-card-title" style="--a:{safe_color(accent)}"><span class="ji-dot"></span>'
                f'{badge}{html.escape(text)}</div>', unsafe_allow_html=True)


_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def safe_color(value: str, fallback: str = ACCENTS[0]) -> str:
    """Only #rrggbb reaches a style attribute — tile colours are user-editable."""
    return value if isinstance(value, str) and _HEX.match(value) else fallback


def stat_tiles_html(tiles: list[tuple[str, str, str, str, str]]) -> str:
    """tiles: (icon, label, value, caption, accent)."""
    cells = "".join(
        f'<div class="ji-stat" style="--a:{safe_color(accent)}"><div class="ji-stat-head">'
        f'<span class="ji-stat-icon">{html.escape(icon)}</span>{html.escape(label)}</div>'
        f'<div class="ji-stat-value">{html.escape(value)}</div>'
        f'<div class="ji-stat-sub">{html.escape(caption)}</div></div>'
        for icon, label, value, caption, accent in tiles)
    return f'<div class="ji-stats">{cells}</div>'


def number_html(value: str, caption: str, accent: str, min_height: int = 0) -> str:
    """Big gradient number; `min_height` stretches it to line up with charts in the same row."""
    size = f";min-height:{int(min_height)}px" if min_height else ""
    return (f'<div class="ji-number" style="--a:{accent}{size}"><div class="ji-number-value">{html.escape(value)}</div>'
            f'<div class="ji-number-sub">{html.escape(caption)}</div></div>')
