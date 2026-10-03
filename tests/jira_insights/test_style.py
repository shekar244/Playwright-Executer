import pandas as pd

from jira_insights.store import Store
from jira_insights.ui.dashboard import kpi_tiles
from jira_insights.ui.style import ACCENTS, accent_for, number_html, stat_tiles_html


def test_tiles_escape_user_text():
    out = stat_tiles_html([("🐞", "<b>Defects</b>", "12", "of <script>", "#f07090")])
    assert "<b>Defects</b>" not in out and "&lt;b&gt;Defects&lt;/b&gt;" in out
    assert "&lt;script&gt;" in out and "--a:#f07090" in out
    assert "&lt;img" in number_html("<img>", "Issues", "#7ea8ff")


def test_accent_is_stable_per_key():
    assert accent_for("report-1") == accent_for("report-1")
    assert accent_for("report-1") in ACCENTS


def test_kpi_tiles_summarise_stories_defects_and_points():
    df = pd.DataFrame({
        "Issue Type": ["Story", "Story", "Bug", "Defect", "Task"],
        "Open/Closed": ["Open", "Closed", "Open", "Closed", "Open"],
        "Story point estimate": [3, 5, None, None, 1],
    })
    tiles = {label: (value, caption) for _, label, value, caption, _ in kpi_tiles(df, total=10)}
    assert tiles["Issues"] == ("5", "of 10 in dataset")
    assert tiles["Open"] == ("3", "60% of issues")
    assert tiles["Stories"][0] == "2" and tiles["Defects"][0] == "2"
    assert tiles["Defects per story"][0] == "1.00"
    assert tiles["Story points"] == ("9", "5 delivered")


def test_settings_round_trip(tmp_path):
    store = Store(tmp_path)
    assert store.get_setting("theme", "Aurora") == "Aurora"
    store.set_setting("theme", "Classic")
    assert Store(tmp_path).get_setting("theme") == "Classic"
