import pandas as pd

from jira_insights.store import Store
from jira_insights.ui.style import ACCENTS, accent_for, number_html, stat_tiles_html


def test_tiles_escape_user_text():
    out = stat_tiles_html([("🐞", "<b>Defects</b>", "12", "of <script>", "#f07090")])
    assert "<b>Defects</b>" not in out and "&lt;b&gt;Defects&lt;/b&gt;" in out
    assert "&lt;script&gt;" in out and "--a:#f07090" in out
    assert "&lt;img" in number_html("<img>", "Issues", "#7ea8ff")


def test_accent_is_stable_per_key():
    assert accent_for("report-1") == accent_for("report-1")
    assert accent_for("report-1") in ACCENTS


def test_user_edited_icon_and_colour_cannot_inject_html():
    out = stat_tiles_html([("<img src=x>", "Tile", "1", "", "red;}</style><script>")])
    assert "<img" not in out and "<script>" not in out
    assert f"--a:{ACCENTS[0]}" in out                                   # bad colour → default accent


def test_settings_round_trip(tmp_path):
    store = Store(tmp_path)
    assert store.get_setting("theme", "Aurora") == "Aurora"
    store.set_setting("theme", "Classic")
    assert Store(tmp_path).get_setting("theme") == "Classic"


def test_number_tile_can_stretch_to_its_row():
    assert "min-height:416px" in number_html("12", "Issues", "#7ea8ff", min_height=416)
    assert "min-height" not in number_html("12", "Issues", "#7ea8ff")


def test_cards_never_trap_the_fullscreen_overlay():
    # transform / overflow:hidden on a card make Streamlit's fixed fullscreen view render inside it.
    import re
    from jira_insights.ui.style import _CSS_DARK
    css = re.sub(r"/\*.*?\*/", "", _CSS_DARK, flags=re.S)
    card_rules = [block for block in css.split("}") if 'st-key-card-' in block]
    assert card_rules and not any("transform" in r or "overflow: hidden" in r for r in card_rules)



def test_card_title_shows_the_report_number_escaped(monkeypatch):
    import streamlit as st
    from jira_insights.ui.style import card_title
    seen = []
    monkeypatch.setattr(st, "markdown", lambda body, **kw: seen.append(body))
    card_title("Defects <b>", "#7ea8ff", "R-012")
    assert '<span class="ji-code">R-012</span>' in seen[0] and "Defects &lt;b&gt;" in seen[0]
