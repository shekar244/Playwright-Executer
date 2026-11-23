"""KPI cards can be filled: plain, full colour or gradient, in the colour picked for the tile.

A filled card draws its figure, label and icon in a colour computed from the fill, so the number is never the box's
own colour. Tiles saved before the option existed are plain, as they always were.
"""
import random
import re
from pathlib import Path

import pandas as pd
import pytest

from jira_insights.kpi import DEFAULT_TILES, TILE_STYLES, KpiSpec, Metric
from jira_insights.store import Store
from jira_insights.ui.style import contrast_ratio, stat_tiles_html, tile_colors

CELL = ("🐞", "Defects", "12", "of 40", "#f07090")


def colours():
    rng = random.Random(7)
    extremes = ["#000000", "#ffffff", "#808080", "#777777", "#888888", "#ffd60a", "#0a84ff", "#ff453a", "#30d158", "#e0e0e0", "#101010"]
    return extremes + [f"#{rng.randrange(1 << 24):06x}" for _ in range(400)]


# ── the colours ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("style", ["solid", "gradient"])
def test_the_figure_is_never_the_colour_of_the_box_and_is_always_readable(style):
    for accent in colours():
        c = tile_colors(accent, style)
        stops = [c["a"]] if style == "solid" else [c["a"], c["a2"]]
        assert c["ink"] not in (c["a"], c["a2"]), accent
        assert min(contrast_ratio(c["ink"], s) for s in stops) >= 4.0, (accent, style)


def test_a_dark_colour_gets_light_ink_and_a_light_colour_gets_dark_ink():
    assert tile_colors("#0a1a4a", "solid")["ink"] == "#ffffff"
    assert tile_colors("#ffd60a", "solid")["ink"] != "#ffffff"
    assert tile_colors("#ffffff", "gradient")["ink"] != "#ffffff"


@pytest.mark.parametrize("accent", ["#0a84ff", "#ffd60a", "#808080", "#1b1b3a", "#f07090", "#30d158"])
def test_a_gradient_ends_in_a_shade_the_ink_reads_at_least_as_well_on(accent):
    c = tile_colors(accent, "gradient")

    assert c["a"] == accent and c["a2"] != c["a"]
    assert contrast_ratio(c["ink"], c["a2"]) >= contrast_ratio(c["ink"], c["a"])


def test_a_colour_that_is_not_a_plain_hex_value_falls_back():
    assert tile_colors("red;}</style><script>", "solid")["a"].startswith("#")
    assert re.fullmatch(r"#[0-9a-f]{6}", tile_colors("nonsense", "gradient")["a2"])


# ── the card ──────────────────────────────────────────────────────────────────

def test_a_tile_without_a_style_is_the_plain_card_it_always_was():
    out = stat_tiles_html([CELL])

    assert 'class="ji-stat"' in out and "ji-stat-solid" not in out and "--ink" not in out


@pytest.mark.parametrize("style", ["default", "bogus", "", None, 3, "solid;}</style><script>alert(1)</script>"])
def test_an_unknown_style_means_the_plain_card_and_cannot_inject_anything(style):
    out = stat_tiles_html([(*CELL, style)])

    assert 'class="ji-stat"' in out and "--ink" not in out and "<script" not in out


def test_full_colour_fills_the_card_and_sets_the_figure_colour():
    out = stat_tiles_html([(*CELL, "solid")])
    ink = tile_colors("#f07090", "solid")["ink"]

    assert 'class="ji-stat ji-stat-solid"' in out and f"--ink:{ink}" in out and "--a:#f07090" in out


def test_gradient_sets_both_ends_of_the_fill():
    out = stat_tiles_html([(*CELL, "gradient")])
    c = tile_colors("#f07090", "gradient")

    assert 'class="ji-stat ji-stat-gradient"' in out and f"--a2:{c['a2']}" in out and f"--ink:{c['ink']}" in out


def test_text_and_colours_of_a_filled_card_are_still_escaped():
    out = stat_tiles_html([("<img src=x>", "<b>Tile</b>", "<i>1</i>", "<script>", "red;}</style><script>", "gradient")])

    assert "<img" not in out and "<b>" not in out and "<i>" not in out and "<script" not in out
    assert re.findall(r'style="([^"]*)"', out)[0].count(";") == 2          # only the three hex variables


def test_a_row_can_mix_plain_and_filled_cards():
    out = stat_tiles_html([CELL, (*CELL, "solid"), (*CELL, "gradient")])

    assert out.count('class="ji-stat"') == 1 and out.count("ji-stat-solid") == 1 and out.count("ji-stat-gradient") == 1


# ── the tile ──────────────────────────────────────────────────────────────────

def test_the_styles_on_offer():
    assert list(TILE_STYLES) == ["default", "solid", "gradient"] and TILE_STYLES["solid"] == "Full colour"


def test_the_style_survives_saving_and_loading():
    tile = KpiSpec("Defects", "🐞", "#f07090", Metric(), style="gradient").with_id()

    assert KpiSpec.from_dict(tile.to_dict()).style == "gradient"


@pytest.mark.parametrize("stored", [{}, {"style": None}, {"style": "neon"}, {"style": 5}])
def test_tiles_saved_without_or_with_an_unknown_style_are_plain(stored):
    assert KpiSpec.from_dict({"label": "Old", **stored}).style == "default"


def test_the_built_in_tiles_stay_plain_and_positional_construction_still_works():
    assert all(t.style == "default" for t in DEFAULT_TILES)
    assert KpiSpec("L", "i", "#000000", Metric(), None, "text", "t", None, True, ["Col"], "abc").id == "abc"


# ── the editor, in the real app ───────────────────────────────────────────────

APP = Path(__file__).resolve().parents[3] / "src" / "jira_insights" / "app.py"


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    pytest.importorskip("plotly")
    pytest.importorskip("pygwalker")
    from amplify_server.config import settings
    monkeypatch.setattr(settings, "DATABASE_PATH", tmp_path / "amplify_server.db")
    monkeypatch.setenv("JIRA_INSIGHTS_HOME", str(tmp_path / "ws"))
    monkeypatch.delenv("QEA_API_URL", raising=False)
    store = Store(tmp_path / "ws")
    store.save_dataset("Demo", pd.DataFrame({"Key": ["A-1", "A-2", "A-3"], "Issue Type": ["Story", "Bug", "Story"],
                                             "Open/Closed": ["Open", "Closed", "Open"], "Status Category": ["To Do", "Done", "To Do"]}),
                       source="upload")
    store.save_kpis([
        KpiSpec("Defects per story", "⚖️", "#f0d080", Metric(filters={"Issue Type": ["Bug"]}),
                divide_by=Metric(filters={"Issue Type": ["Story"]}), caption="text", caption_text="defects ÷ stories",
                requires=["Issue Type"]),
        KpiSpec("Open", "🔓", "#f0a070", Metric(filters={"Open/Closed": ["Open"]}), style="solid"),
    ])
    return store


def open_editor():
    from streamlit.testing.v1 import AppTest
    from amplify_server import sso
    from jira_insights import options as insights_options
    insights_options._cache.update(at=0.0, url=None, value=None)
    at = AppTest.from_file(str(APP), default_timeout=90)
    at.query_params["sso"] = sso.issue_insights_token(1, "tester")
    at.session_state["view_pending"] = "Dashboard"
    at.run()
    at.toggle(key="dash_edit_tiles").set_value(True).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def test_the_editor_offers_the_card_style_and_shows_the_chosen_one(workspace):
    at = open_editor()
    at.selectbox(key="kpi_pick").select(workspace.list_kpis()[1].id).run()          # the "Open" tile is saved as Full colour

    style = at.radio(key="kpi_style")

    assert style.options == ["Plain", "Full colour", "Gradient"] and style.value == "solid"
    assert any("ji-stat-solid" in str(m.value) for m in at.markdown)               # the preview is filled


def test_choosing_gradient_and_saving_changes_the_tile_and_its_preview(workspace):
    at = open_editor()
    tile = workspace.list_kpis()[0]
    at.selectbox(key="kpi_pick").select(tile.id).run()

    at.radio(key="kpi_style").set_value("gradient").run()
    at.get("color_picker")[0].set_value("#1155cc").run()
    assert any("ji-stat-gradient" in str(m.value) and "#1155cc" in str(m.value) for m in at.markdown)
    at.button(key="kpi-save").click().run()

    saved = next(t for t in workspace.list_kpis() if t.label == "Defects per story")
    assert saved.style == "gradient" and saved.accent == "#1155cc"


def test_saving_a_tile_keeps_its_id_its_required_columns_and_its_number_format(workspace):
    """The editor used to build the tile with positional arguments that put the tile id in `as_percent` and dropped
    `requires`: editing 'Defects per story' turned it into a percentage."""
    tile = workspace.list_kpis()[0]
    assert tile.as_percent is False and tile.requires == ["Issue Type"]
    at = open_editor()
    at.selectbox(key="kpi_pick").select(tile.id).run()

    at.text_input(key="kpi_label").set_value("Bugs per story").run()
    at.button(key="kpi-save").click().run()

    saved = next(t for t in workspace.list_kpis() if t.label == "Bugs per story")
    assert (saved.id, saved.as_percent, saved.requires) == (tile.id, False, ["Issue Type"])
    assert len(workspace.list_kpis()) == 2


def test_the_dashboard_strip_draws_each_tile_in_its_own_style(workspace):
    from streamlit.testing.v1 import AppTest
    from amplify_server import sso
    from jira_insights import options as insights_options
    insights_options._cache.update(at=0.0, url=None, value=None)
    at = AppTest.from_file(str(APP), default_timeout=90)
    at.query_params["sso"] = sso.issue_insights_token(1, "tester")
    at.session_state["view_pending"] = "Dashboard"
    at.run()

    strip = next(str(m.value) for m in at.markdown if 'class="ji-stats"' in str(m.value))

    assert strip.count('class="ji-stat"') == 1 and strip.count("ji-stat-solid") == 1
