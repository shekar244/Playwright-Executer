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

_CSS = """
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

  /* ── Cards ──
     No transform / filter / overflow:hidden on cards: any of them would trap Streamlit's
     fixed-position fullscreen overlay inside the card. The top accent line is a background. */
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


def inject_css() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)


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
