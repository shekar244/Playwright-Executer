"""
Visual layer for Jira Insights: page CSS (aurora backdrop, glowing cards,
gradient navigation and buttons) and small HTML components — brand mark,
hero header, card titles, and colourful stat tiles.

Everything user-provided (dataset and report names) is HTML-escaped.
"""
from __future__ import annotations

import html
import re
import zlib

import streamlit as st

# Brand accents for chrome (tiles, card dots) — the Amplify QEA palette.
ACCENTS = ("#7ea8ff", "#b898f5", "#7ecdc0", "#f0a070", "#f07090", "#f0d080", "#5fd4a0", "#6fc3ff")

_CSS_DARK = """
<style>
  .stApp {
    background:
      radial-gradient(1100px 520px at 0% -8%, rgba(126,168,255,.16), transparent 60%),
      radial-gradient(900px 480px at 100% -6%, rgba(184,152,245,.14), transparent 62%),
      radial-gradient(900px 600px at 50% 115%, rgba(126,205,192,.07), transparent 60%),
      #0b0b16;
    background-attachment: fixed;
  }
  [data-testid="stHeader"] { background: transparent; }
  .block-container { padding-top: 1.1rem; padding-bottom: 2.5rem; }

  [data-testid="stSidebar"] {
    background: linear-gradient(180deg, #15152a 0%, #0f0f1e 100%);
    border-right: 1px solid rgba(126,168,255,.12);
  }

  /* ── Brand + hero ── */
  .ji-brand { display: flex; align-items: center; gap: 10px; margin: 0 0 8px; }
  .ji-brand-mark {
    width: 34px; height: 34px; border-radius: 10px; display: grid; place-items: center; font-size: 17px;
    background: linear-gradient(135deg, #6ea8ff, #a87aff 55%, #f07090);
    box-shadow: 0 0 22px rgba(126,168,255,.35), 0 2px 6px rgba(0,0,0,.4);
  }
  .ji-brand-name, .ji-hero-title {
    font-weight: 800; letter-spacing: -.02em; -webkit-background-clip: text; background-clip: text; color: transparent;
  }
  .ji-brand-name { font-size: 19px; background-image: linear-gradient(90deg, #ffffff, #9fc0ff 50%, #c7a8ff); }
  .ji-hero-title {
    font-size: 27px; line-height: 1.15;
    background-image: linear-gradient(90deg, #ffffff 0%, #9fc0ff 38%, #c7a8ff 68%, #ff9cc2 100%);
  }
  .ji-chips { display: flex; gap: 6px; flex-wrap: wrap; margin: 7px 0 14px; }
  .ji-chip {
    font-size: 11px; font-weight: 600; color: #c0c9ec; padding: 3px 10px; border-radius: 999px;
    background: rgba(126,168,255,.10); border: 1px solid rgba(126,168,255,.20);
  }
  .ji-swatches { display: flex; gap: 5px; margin: -4px 0 6px; }
  .ji-swatches span { width: 16px; height: 16px; border-radius: 5px; box-shadow: 0 0 8px rgba(0,0,0,.35); }

  /* ── Cards ── */
  div[class*="st-key-card-"] {
    background: linear-gradient(90deg, #7ea8ff, #b898f5 50%, #f07090) top / 100% 2px no-repeat, #181828;
    border: 1px solid rgba(126,168,255,.14); border-radius: 16px; padding: 16px 18px 10px;
    box-shadow: 0 12px 30px -14px rgba(0,0,0,.7), inset 0 1px 0 rgba(255,255,255,.04);
    transition: border-color .18s ease, box-shadow .18s ease;
  }
  div[class*="st-key-card-"]:hover {
    border-color: rgba(126,168,255,.34);
    box-shadow: 0 18px 40px -16px rgba(0,0,0,.8), 0 0 30px -8px rgba(126,168,255,.28);
  }
  .ji-card-title {
    font-size: 12px; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; color: #c5cdea;
    display: flex; align-items: center; gap: 9px; min-height: 32px;
  }
  .ji-card-title .ji-code {
    font: 600 10px 'JetBrains Mono', ui-monospace, monospace; letter-spacing: .02em; text-transform: none;
    color: #aab4d8; background: rgba(126,168,255,.10); border: 1px solid rgba(126,168,255,.24);
    border-radius: 6px; padding: 1px 6px;
  }
  .ji-card-title .ji-dot { width: 9px; height: 9px; border-radius: 50%; flex-shrink: 0;
    background: var(--a); box-shadow: 0 0 10px var(--a); }

  /* ── Stat tiles ── */
  .ji-stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin: 6px 0 10px; }
  .ji-stat {
    position: relative; overflow: hidden; border-radius: 16px; padding: 14px 16px 13px;
    background: radial-gradient(130% 150% at 100% 0%, color-mix(in srgb, var(--a) 24%, transparent), transparent 55%), #181828;
    border: 1px solid color-mix(in srgb, var(--a) 34%, transparent);
    box-shadow: 0 12px 28px -16px color-mix(in srgb, var(--a) 70%, transparent);
    transition: transform .18s ease, box-shadow .18s ease;
  }
  .ji-stat:hover { transform: translateY(-2px); box-shadow: 0 16px 34px -14px color-mix(in srgb, var(--a) 80%, transparent); }
  .ji-stat::after { content: ''; position: absolute; left: 0; top: 14px; bottom: 14px; width: 3px;
    border-radius: 0 3px 3px 0; background: var(--a); box-shadow: 0 0 12px var(--a); }
  .ji-stat-head { display: flex; align-items: center; gap: 8px; color: #b3bcdf; font-size: 12px; font-weight: 600; }
  .ji-stat-icon { width: 26px; height: 26px; border-radius: 8px; display: grid; place-items: center; font-size: 14px;
    background: color-mix(in srgb, var(--a) 24%, transparent); }
  .ji-stat-value, .ji-number-value {
    font-weight: 800; letter-spacing: -.02em; line-height: 1.05; -webkit-background-clip: text; background-clip: text;
    color: transparent; background-image: linear-gradient(90deg, #ffffff, color-mix(in srgb, var(--a) 75%, #ffffff));
  }
  .ji-stat-value { font-size: 30px; margin-top: 9px; }
  .ji-stat-sub, .ji-number-sub { font-size: 11.5px; color: #8e98bc; margin-top: 4px; }
  .ji-number { text-align: center; padding: 18px 0 16px; display: flex; flex-direction: column; justify-content: center; }
  .ji-number-value { font-size: 52px; }

  /* ── Navigation, pills and buttons ── */
  button[data-testid="stBaseButton-segmented_control"], button[data-testid="stBaseButton-pills"] {
    background: rgba(24,24,40,.75); border-color: rgba(126,168,255,.18);
  }
  button[data-testid="stBaseButton-segmented_controlActive"], button[data-testid="stBaseButton-pillsActive"],
  button[data-testid="stBaseButton-primary"], button[data-testid="stBaseButton-primaryFormSubmit"] {
    background: linear-gradient(135deg, #7ea8ff 0%, #a98af7 65%, #e58ad8 120%) !important;
    border-color: transparent !important; box-shadow: 0 8px 20px -8px rgba(126,168,255,.7);
  }
  button[data-testid="stBaseButton-segmented_controlActive"] *, button[data-testid="stBaseButton-pillsActive"] *,
  button[data-testid="stBaseButton-primary"] *, button[data-testid="stBaseButton-primaryFormSubmit"] * {
    color: #0b0b16 !important; font-weight: 700;
  }
  button[data-testid="stBaseButton-primary"]:hover, button[data-testid="stBaseButton-primaryFormSubmit"]:hover {
    filter: brightness(1.08); transform: translateY(-1px);
  }

  /* ── Containers ── */
  [data-testid="stExpander"] details {
    border-radius: 12px; border-color: rgba(126,168,255,.16); background: rgba(24,24,40,.55);
  }
  [data-testid="stDataFrame"] { border-radius: 12px; overflow: hidden; border: 1px solid rgba(126,168,255,.12); }
  div[data-baseweb="select"] > div, div[data-baseweb="input"], div[data-baseweb="textarea"] { border-radius: 10px !important; }

  /* ── Theme toggle ── */
  .ji-theme-toggle { display: flex; align-items: center; gap: 6px; font-size: 11px; color: #8e98bc; font-weight: 600; }
</style>
"""

_CSS_LIGHT = """
<style>
  /* ── Global surface override ── */
  .stApp {
    background:
      radial-gradient(1100px 520px at 0% -8%, rgba(100,140,220,.08), transparent 60%),
      radial-gradient(900px 480px at 100% -6%, rgba(160,130,210,.06), transparent 62%),
      radial-gradient(900px 600px at 50% 115%, rgba(100,180,170,.04), transparent 60%),
      #f5f6fa !important;
    background-attachment: fixed;
    color: #1a1d2e !important;
  }
  [data-testid="stHeader"] { background: rgba(245,246,250,0.92) !important; backdrop-filter: blur(12px); }
  .block-container { padding-top: 1.1rem; padding-bottom: 2.5rem; }

  /* ── Sidebar ── */
  [data-testid="stSidebar"] {
    background: linear-gradient(180deg, #f0f1f6 0%, #e8eaf2 100%) !important;
    border-right: 1px solid rgba(100,120,180,.15) !important;
  }
  [data-testid="stSidebar"],
  [data-testid="stSidebar"] * { color: #1a1d2e !important; }
  [data-testid="stSidebar"] .ji-brand-name { color: transparent !important; -webkit-background-clip: text !important; background-clip: text !important; }
  [data-testid="stSidebar"] label { color: #4a5070 !important; }
  [data-testid="stSidebar"] .stCaption, [data-testid="stSidebar"] .stCaption * { color: #6b7294 !important; }
  [data-testid="stSidebar"] div[data-baseweb="select"] > div { background: #ffffff !important; border-color: #d8dce8 !important; }
  [data-testid="stSidebar"] div[data-baseweb="input"] { background: #ffffff !important; border-color: #d8dce8 !important; }
  [data-testid="stSidebar"] div[data-baseweb="textarea"] > div { background: #ffffff !important; border-color: #d8dce8 !important; }
  [data-testid="stSidebar"] hr { border-color: #d8dce8 !important; }
  [data-testid="stSidebar"] [data-testid="stExpander"] details { background: rgba(255,255,255,.6) !important; border-color: #d8dce8 !important; }
  [data-testid="stSidebar"] [data-testid="stForm"] { border-color: #d8dce8 !important; }
  [data-testid="stSidebar"] button[data-testid="stBaseButton-secondary"] { background: #ffffff !important; border-color: #d8dce8 !important; }
  [data-testid="stSidebar"] button[data-testid="stBaseButton-secondary"]:hover { background: #f0f2fa !important; }
  [data-testid="stSidebarCollapseButton"] button { color: #4a5070 !important; }
  [data-testid="stSidebarCollapseButton"] svg { fill: #4a5070 !important; stroke: #4a5070 !important; }

  /* ── Global text overrides (Streamlit dark theme bleeds through) ── */
  .stApp p, .stApp span, .stApp div, .stApp li, .stApp td, .stApp th,
  .stApp [data-testid="stMarkdownContainer"] p,
  .stApp [data-testid="stMarkdownContainer"] li,
  .stApp [data-testid="stMarkdownContainer"] strong,
  .stApp h1, .stApp h2, .stApp h3, .stApp h4, .stApp h5 { color: #1a1d2e !important; }
  .stApp [data-testid="stMarkdownContainer"] a { color: #3060b0 !important; }
  .stApp label { color: #4a5070 !important; }
  .stApp .stCaption, .stApp .stCaption p, .stApp .stCaption span { color: #6b7294 !important; }
  .stApp small { color: #6b7294 !important; }
  .stApp svg { color: #4a5070; }

  /* ── Alert banners (info, warning, error, success) ── */
  .stApp [data-testid="stAlert"] { color: #1a1d2e !important; }
  .stApp [data-testid="stAlert"] p,
  .stApp [data-testid="stAlert"] span,
  .stApp [data-testid="stAlert"] a { color: #1a1d2e !important; }
  .stApp [data-testid="stAlert"] a { text-decoration: underline; }
  .stApp [data-testid="stNotification"] { color: #1a1d2e !important; }
  .stApp [data-testid="stNotification"] p,
  .stApp [data-testid="stNotification"] span { color: #1a1d2e !important; }
  .stApp .stAlert { color: #1a1d2e !important; }
  .stApp .stAlert p, .stApp .stAlert span { color: #1a1d2e !important; }

  /* ── Streamlit CSS variable overrides (the dark config.toml injects these) ── */
  .stApp {
    --background-color: #f5f6fa; --secondary-background-color: #ebedf5;
    --text-color: #1a1d2e; --font: Inter, system-ui, -apple-system, Segoe UI, sans-serif;
    --primary-color: #5a90e0;
  }

  /* ── Inputs ── */
  .stApp div[data-baseweb="select"] > div { background: #ffffff !important; border-color: #d8dce8 !important; color: #1a1d2e !important; }
  .stApp div[data-baseweb="select"] span { color: #1a1d2e !important; }
  .stApp div[data-baseweb="input"],
  .stApp div[data-baseweb="input"] > div { background: #ffffff !important; border-color: #d8dce8 !important; }
  .stApp div[data-baseweb="textarea"],
  .stApp div[data-baseweb="textarea"] > div { background: #ffffff !important; border-color: #d8dce8 !important; }
  .stApp [data-testid="stTextInput"] > div { background: transparent !important; }
  .stApp [data-testid="stTextInput"] > div > div { background: #ffffff !important; border-color: #d8dce8 !important; border-radius: 10px; }
  .stApp [data-testid="stTextArea"] > div > div { background: #ffffff !important; border-color: #d8dce8 !important; }
  .stApp [data-testid="stNumberInput"] > div > div { background: #ffffff !important; border-color: #d8dce8 !important; }
  .stApp [data-testid="stSelectbox"] > div > div { background: #ffffff !important; border-color: #d8dce8 !important; }
  .stApp [data-testid="stMultiSelect"] > div > div { background: #ffffff !important; border-color: #d8dce8 !important; }
  .stApp input { color: #1a1d2e !important; background: transparent !important; }
  .stApp textarea { color: #1a1d2e !important; background: #ffffff !important; }
  .stApp [data-baseweb="tag"] { background: #dce4f4 !important; color: #1a1d2e !important; }
  .stApp [data-baseweb="tag"] span { color: #1a1d2e !important; }
  .stApp ::placeholder { color: #9ea2b8 !important; }

  /* ── Dropdown menus (selectbox, multiselect popups) ── */
  .stApp div[data-baseweb="popover"] > div { background: #ffffff !important; border-color: #d8dce8 !important; box-shadow: 0 4px 16px rgba(0,0,0,.10) !important; }
  .stApp div[data-baseweb="popover"] li { color: #1a1d2e !important; }
  .stApp div[data-baseweb="popover"] li:hover { background: #edf2fc !important; }
  .stApp ul[data-testid="stSelectboxVirtualDropdown"] { background: #ffffff !important; border-color: #d8dce8 !important; }
  .stApp ul[data-testid="stSelectboxVirtualDropdown"] li { color: #1a1d2e !important; }
  .stApp ul[data-testid="stSelectboxVirtualDropdown"] li:hover,
  .stApp ul[data-testid="stSelectboxVirtualDropdown"] li[aria-selected="true"] { background: #edf2fc !important; }
  .stApp [data-baseweb="menu"] { background: #ffffff !important; }
  .stApp [data-baseweb="menu"] li { color: #1a1d2e !important; }
  .stApp [data-baseweb="menu"] li:hover { background: #edf2fc !important; }

  /* ── Code blocks ── */
  .stApp [data-testid="stCode"],
  .stApp pre, .stApp code {
    background: #ebedf5 !important; color: #1a1d2e !important; border-color: #d8dce8 !important;
  }
  .stApp .stCodeBlock { background: #ebedf5 !important; }
  .stApp .stCodeBlock pre { background: #ebedf5 !important; color: #1a1d2e !important; }
  .stApp .stCodeBlock code { background: transparent !important; color: #1a1d2e !important; }

  /* ── File uploader ── */
  .stApp [data-testid="stFileUploader"] { background: transparent !important; }
  .stApp [data-testid="stFileUploader"] section { background: #ffffff !important; border-color: #d8dce8 !important; }
  .stApp [data-testid="stFileUploader"] section > div { color: #4a5070 !important; }
  .stApp [data-testid="stFileUploader"] button { color: #3060b0 !important; }
  .stApp [data-testid="stFileUploaderDropzone"] { background: #f8f9fc !important; border-color: #d8dce8 !important; }
  .stApp [data-testid="stFileUploaderDropzone"] span { color: #4a5070 !important; }
  .stApp [data-testid="stFileUploaderDropzone"] small { color: #6b7294 !important; }

  /* ── Form containers ── */
  .stApp [data-testid="stForm"] { border-color: #d8dce8 !important; background: transparent !important; }

  /* ── Toggle switch ── */
  .stApp [data-testid="stToggle"] label span { color: #1a1d2e !important; }

  /* ── Segmented control & pill buttons — inactive text ── */
  button[data-testid="stBaseButton-segmented_control"] { background: rgba(255,255,255,.85) !important; border-color: rgba(80,110,180,.18) !important; }
  button[data-testid="stBaseButton-segmented_control"] p,
  button[data-testid="stBaseButton-segmented_control"] span { color: #4a5070 !important; }
  button[data-testid="stBaseButton-pills"] { background: rgba(255,255,255,.85) !important; border-color: rgba(80,110,180,.18) !important; }
  button[data-testid="stBaseButton-pills"] p,
  button[data-testid="stBaseButton-pills"] span { color: #4a5070 !important; }

  /* ── Active buttons ── */
  button[data-testid="stBaseButton-segmented_controlActive"], button[data-testid="stBaseButton-pillsActive"],
  button[data-testid="stBaseButton-primary"], button[data-testid="stBaseButton-primaryFormSubmit"] {
    background: linear-gradient(135deg, #5a90e0 0%, #8070d0 65%, #c070b0 120%) !important;
    border-color: transparent !important; box-shadow: 0 4px 14px -4px rgba(80,110,180,.4);
  }
  button[data-testid="stBaseButton-segmented_controlActive"] *, button[data-testid="stBaseButton-pillsActive"] *,
  button[data-testid="stBaseButton-primary"] *, button[data-testid="stBaseButton-primaryFormSubmit"] * {
    color: #ffffff !important; font-weight: 700;
  }
  button[data-testid="stBaseButton-primary"]:hover, button[data-testid="stBaseButton-primaryFormSubmit"]:hover {
    filter: brightness(1.06); transform: translateY(-1px);
  }

  /* ── Secondary / tertiary buttons ── */
  button[data-testid="stBaseButton-secondary"] { background: #ffffff !important; border-color: #d8dce8 !important; }
  button[data-testid="stBaseButton-secondary"] p, button[data-testid="stBaseButton-secondary"] span { color: #4a5070 !important; }
  button[data-testid="stBaseButton-secondary"]:hover { border-color: rgba(80,110,180,.35) !important; background: #f0f2fa !important; }
  button[data-testid="stBaseButton-tertiary"] p, button[data-testid="stBaseButton-tertiary"] span { color: #4a5070 !important; }
  button[data-testid="stBaseButton-tertiary"]:hover { background: #f0f2fa !important; }

  /* ── Download buttons ── */
  .stApp .stDownloadButton button { background: #ffffff !important; border-color: #d8dce8 !important; }
  .stApp .stDownloadButton button p, .stApp .stDownloadButton button span { color: #4a5070 !important; }
  .stApp .stDownloadButton button:hover { border-color: rgba(80,110,180,.35) !important; background: #f0f2fa !important; }

  /* ── Containers ── */
  [data-testid="stExpander"] details {
    border-radius: 12px; border-color: #d8dce8 !important; background: rgba(255,255,255,.75) !important;
  }
  [data-testid="stExpander"] summary span { color: #1a1d2e !important; }
  [data-testid="stExpander"] summary svg { color: #4a5070 !important; }
  [data-testid="stDataFrame"] { border-radius: 12px; overflow: hidden; border: 1px solid #d8dce8 !important; }
  div[data-baseweb="select"] > div, div[data-baseweb="input"], div[data-baseweb="textarea"] { border-radius: 10px !important; }

  /* ── Popover / tooltip ── */
  .stApp [data-testid="stPopover"] > div { background: #ffffff !important; border-color: #d8dce8 !important; }
  .stApp [data-testid="stTooltipContent"] { background: #ffffff !important; color: #1a1d2e !important; border: 1px solid #d8dce8 !important; }

  /* ── Tabs ── */
  .stApp .stTabs [data-baseweb="tab-list"] { border-bottom-color: #d8dce8 !important; }
  .stApp .stTabs [data-baseweb="tab"] { color: #4a5070 !important; }
  .stApp .stTabs [aria-selected="true"] { color: #3060b0 !important; border-bottom-color: #3060b0 !important; }

  /* ── Number input spinner ── */
  .stApp [data-testid="stNumberInput"] button { color: #4a5070 !important; background: #f0f2fa !important; border-color: #d8dce8 !important; }
  .stApp [data-testid="stNumberInput"] input { background: #ffffff !important; }

  /* ── Checkbox / radio ── */
  .stApp [data-testid="stCheckbox"] label span { color: #1a1d2e !important; }
  .stApp [data-testid="stRadio"] label span { color: #1a1d2e !important; }

  /* ── Progress bar ── */
  .stApp [data-testid="stProgress"] > div { background: #e4e7f0 !important; }

  /* ── Metric widget ── */
  .stApp [data-testid="stMetric"] label { color: #6b7294 !important; }
  .stApp [data-testid="stMetric"] [data-testid="stMetricValue"] { color: #1a1d2e !important; }

  /* ── Scrollbar ── */
  .stApp ::-webkit-scrollbar-track { background: #f0f1f6 !important; }
  .stApp ::-webkit-scrollbar-thumb { background: #c8cdd8 !important; }
  .stApp ::-webkit-scrollbar-thumb:hover { background: #b0b6c8 !important; }

  /* ── Brand + hero ── */
  .ji-brand { display: flex; align-items: center; gap: 10px; margin: 0 0 8px; }
  .ji-brand-mark {
    width: 34px; height: 34px; border-radius: 10px; display: grid; place-items: center; font-size: 17px;
    background: linear-gradient(135deg, #4a80e0, #8060d0 55%, #d06080);
    box-shadow: 0 0 16px rgba(80,120,200,.25), 0 2px 6px rgba(0,0,0,.12);
  }
  .stApp .ji-brand-name, .stApp .ji-hero-title {
    font-weight: 800; letter-spacing: -.02em; -webkit-background-clip: text !important; background-clip: text !important; color: transparent !important;
  }
  .stApp .ji-brand-name { font-size: 19px; background-image: linear-gradient(90deg, #1a1d2e, #3060b0 50%, #7050a0) !important; }
  .stApp .ji-hero-title {
    font-size: 27px; line-height: 1.15;
    background-image: linear-gradient(90deg, #1a1d2e 0%, #3060b0 38%, #7050a0 68%, #c06080 100%) !important;
  }
  .ji-chips { display: flex; gap: 6px; flex-wrap: wrap; margin: 7px 0 14px; }
  .ji-chip {
    font-size: 11px; font-weight: 600; color: #4a5070 !important; padding: 3px 10px; border-radius: 999px;
    background: rgba(80,110,180,.08); border: 1px solid rgba(80,110,180,.18);
  }
  .ji-swatches { display: flex; gap: 5px; margin: -4px 0 6px; }
  .ji-swatches span { width: 16px; height: 16px; border-radius: 5px; box-shadow: 0 0 6px rgba(0,0,0,.12); }

  /* ── Cards ── */
  div[class*="st-key-card-"] {
    background: linear-gradient(90deg, #5a90e0, #9070d0 50%, #d06080) top / 100% 2px no-repeat, #ffffff !important;
    border: 1px solid rgba(80,110,180,.14) !important; border-radius: 16px; padding: 16px 18px 10px;
    box-shadow: 0 4px 16px -6px rgba(0,0,0,.08), 0 1px 3px rgba(0,0,0,.04) !important;
    transition: border-color .18s ease, box-shadow .18s ease;
  }
  div[class*="st-key-card-"]:hover {
    border-color: rgba(80,110,180,.30) !important;
    box-shadow: 0 8px 24px -8px rgba(0,0,0,.12), 0 0 20px -6px rgba(80,110,180,.15) !important;
  }
  .ji-card-title {
    font-size: 12px; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; color: #4a5070 !important;
    display: flex; align-items: center; gap: 9px; min-height: 32px;
  }
  .ji-card-title .ji-code {
    font: 600 10px 'JetBrains Mono', ui-monospace, monospace; letter-spacing: .02em; text-transform: none;
    color: #5a6590 !important; background: rgba(80,110,180,.08); border: 1px solid rgba(80,110,180,.20);
    border-radius: 6px; padding: 1px 6px;
  }
  .ji-card-title .ji-dot { width: 9px; height: 9px; border-radius: 50%; flex-shrink: 0;
    background: var(--a); box-shadow: 0 0 8px color-mix(in srgb, var(--a) 50%, transparent); }

  /* ── Stat tiles ── */
  .ji-stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin: 6px 0 10px; }
  .ji-stat {
    position: relative; overflow: hidden; border-radius: 16px; padding: 14px 16px 13px;
    background: radial-gradient(130% 150% at 100% 0%, color-mix(in srgb, var(--a) 10%, transparent), transparent 55%), #ffffff !important;
    border: 1px solid color-mix(in srgb, var(--a) 20%, transparent) !important;
    box-shadow: 0 4px 14px -6px color-mix(in srgb, var(--a) 30%, transparent);
    transition: transform .18s ease, box-shadow .18s ease;
  }
  .ji-stat:hover { transform: translateY(-2px); box-shadow: 0 8px 20px -8px color-mix(in srgb, var(--a) 40%, transparent); }
  .ji-stat::after { content: ''; position: absolute; left: 0; top: 14px; bottom: 14px; width: 3px;
    border-radius: 0 3px 3px 0; background: var(--a); box-shadow: 0 0 8px color-mix(in srgb, var(--a) 50%, transparent); }
  .ji-stat-head { display: flex; align-items: center; gap: 8px; color: #5a6590 !important; font-size: 12px; font-weight: 600; }
  .ji-stat-icon { width: 26px; height: 26px; border-radius: 8px; display: grid; place-items: center; font-size: 14px;
    background: color-mix(in srgb, var(--a) 12%, transparent); }
  .stApp .ji-stat-value, .stApp .ji-number-value {
    font-weight: 800; letter-spacing: -.02em; line-height: 1.05; -webkit-background-clip: text !important; background-clip: text !important;
    color: transparent !important; background-image: linear-gradient(90deg, #1a1d2e, color-mix(in srgb, var(--a) 80%, #1a1d2e)) !important;
  }
  .ji-stat-value { font-size: 30px; margin-top: 9px; }
  .ji-stat-sub, .ji-number-sub { font-size: 11.5px; color: #6b7294 !important; margin-top: 4px; }
  .ji-number { text-align: center; padding: 18px 0 16px; display: flex; flex-direction: column; justify-content: center; }
  .ji-number-value { font-size: 52px; }

  /* ── Theme toggle ── */
  .ji-theme-toggle { display: flex; align-items: center; gap: 6px; font-size: 11px; color: #6b7294; font-weight: 600; }
</style>
"""


# Styles injected into the drag-and-drop component (it renders in its own iframe).
SORTABLE_CSS = """
body { background: transparent; }
.sortable-component { background: transparent; border: none; padding: 0; margin: 0;
  font-family: Inter, system-ui, -apple-system, Segoe UI, sans-serif; }
.sortable-container { background: #12121f; border: 1px dashed rgba(126,168,255,.32);
  border-radius: 12px; margin: 0 0 8px; padding: 2px 4px 4px; counter-reset: none; }
.sortable-container:last-child { border-color: rgba(184,152,245,.45); background: rgba(184,152,245,.06); }
.sortable-container-header { background: transparent; color: #8e98bc; font-size: 11px; font-weight: 700;
  letter-spacing: .08em; text-transform: uppercase; padding: 6px 8px 2px; }
.sortable-container-body { background: transparent; min-height: 46px; }
.sortable-item, .sortable-item:hover {
  background: linear-gradient(135deg, rgba(126,168,255,.24), rgba(184,152,245,.20));
  color: #eef1ff; border: 1px solid rgba(126,168,255,.40); border-radius: 10px;
  font-size: 13px; font-weight: 600; cursor: grab; box-shadow: 0 4px 14px -6px rgba(126,168,255,.55);
}
"""

SORTABLE_CSS_LIGHT = """
body { background: transparent; }
.sortable-component { background: transparent; border: none; padding: 0; margin: 0;
  font-family: Inter, system-ui, -apple-system, Segoe UI, sans-serif; }
.sortable-container { background: #f0f1f6; border: 1px dashed rgba(80,110,180,.28);
  border-radius: 12px; margin: 0 0 8px; padding: 2px 4px 4px; counter-reset: none; }
.sortable-container:last-child { border-color: rgba(130,100,200,.35); background: rgba(130,100,200,.04); }
.sortable-container-header { background: transparent; color: #6b7294; font-size: 11px; font-weight: 700;
  letter-spacing: .08em; text-transform: uppercase; padding: 6px 8px 2px; }
.sortable-container-body { background: transparent; min-height: 46px; }
.sortable-item, .sortable-item:hover {
  background: linear-gradient(135deg, rgba(80,110,180,.14), rgba(130,100,200,.12));
  color: #1a1d2e; border: 1px solid rgba(80,110,180,.30); border-radius: 10px;
  font-size: 13px; font-weight: 600; cursor: grab; box-shadow: 0 2px 8px -4px rgba(80,110,180,.25);
}
"""


def inject_css() -> None:
    dark = st.session_state.get("ji_dark_mode", True)
    st.markdown(_CSS_DARK if dark else _CSS_LIGHT, unsafe_allow_html=True)


def get_sortable_css() -> str:
    dark = st.session_state.get("ji_dark_mode", True)
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
