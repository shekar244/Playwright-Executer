import pandas as pd
import pytest

from jira_insights.kpi import DEFAULT_TILES, KpiSpec, Metric, default_tiles, evaluate, is_available, metric_value
from jira_insights.pivot import apply_filters
from jira_insights.store import Store


@pytest.fixture
def df():
    return pd.DataFrame({
        "Issue Type": ["Story", "Story", "Bug", "Production Bug", "Task"],
        "Open/Closed": ["Open", "Closed", "Open", "Closed", "Open"],
        "Story point estimate": [3, 5, None, None, 1],
    })


def tiles(df, total=10):
    return {t.label: evaluate(t, df, total) for t in default_tiles() if is_available(t, df)}


def test_default_tiles_reproduce_the_original_kpis(df):
    out = tiles(df)
    assert out["Issues"] == ("5", "of 10 in dataset")
    assert out["Open"] == ("3", "60% of issues")
    assert out["Stories"] == ("2", "40% of issues")
    assert out["Defects"] == ("2", "40% of issues")              # "Production Bug" counts via contains
    assert out["Defects per story"] == ("1.00", "defects ÷ stories")
    assert out["Story points"] == ("9", "5 delivered")           # alias: Story point estimate


def test_contains_filter_is_case_insensitive_regex(df):
    assert len(apply_filters(df, {"Issue Type": {"contains": "BUG|task"}})) == 3


def test_custom_tile_with_average_and_ratio(df):
    avg = KpiSpec("Avg points", metric=Metric("Story point estimate", "Average"), caption="text", caption_text="per item")
    assert evaluate(avg, df, 5) == ("3", "per item")
    closed_share = KpiSpec("Closed rate", metric=Metric(filters={"Open/Closed": ["Closed"]}),
                           divide_by=Metric(), caption="none")
    assert evaluate(closed_share, df, 5) == ("0.40", "")
    no_stories = KpiSpec("Ratio", metric=Metric(), divide_by=Metric(filters={"Issue Type": ["Epic"]}))
    assert evaluate(no_stories, df, 5)[0] == "—"


def test_tiles_needing_missing_columns_are_hidden(df):
    t = KpiSpec("Sprint", metric=Metric(filters={"Sprint": ["S1"]}))
    assert not is_available(t, df)
    assert metric_value(df, Metric("Story Points", "Sum")) == 9   # alias resolves the other way too


def test_store_seeds_saves_and_resets_tiles(tmp_path):
    store = Store(tmp_path)
    seeded = store.list_kpis()
    assert [t.label for t in seeded] == [t.label for t in DEFAULT_TILES] and all(t.id for t in seeded)
    seeded[0].label = "All issues"
    store.save_kpis(list(reversed(seeded)))
    again = Store(tmp_path).list_kpis()
    assert again[-1].label == "All issues" and again[0].label == "Story points"
    assert again[0].caption_metric.filters == {"Open/Closed": ["Closed"]}      # nested metrics round-trip
    store.reset_kpis()
    assert store.list_kpis()[0].label == "Issues"
