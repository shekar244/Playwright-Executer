import pandas as pd
import pytest

from jira_insights.charts import DEFAULT_THEME, OTHER_COLOR, SURFACE, THEMES, build_figure, color_map
from jira_insights.pivot import CHART_TYPES, OTHER, ReportSpec, build_pivot, category_rank

PALETTE = THEMES[DEFAULT_THEME].palette


@pytest.fixture
def df():
    return pd.DataFrame({
        "Issue Type": ["Bug", "Bug", "Story", "Task", "Bug", "Story"],
        "Status": ["Open", "Done", "Done", "Open", "Open", "In QA"],
        "Created": pd.to_datetime(["2026-07-01", "2026-07-15", "2026-08-01",
                                   "2026-08-20", "2026-09-02", "2026-09-03"]),
    })


@pytest.mark.parametrize("chart", [c for c in CHART_TYPES if c != "Number"])
def test_every_chart_type_renders(df, chart):
    spec = ReportSpec(chart=chart, rows="Issue Type", series="Status", show_labels=True)
    fig = build_figure(build_pivot(df, spec), spec, category_rank(df, "Status"))
    assert fig.data, chart
    assert fig.layout.template.layout.paper_bgcolor == SURFACE
    fig.to_json()                                   # fully serialisable for the browser


def test_date_rows_use_a_date_axis(df):
    spec = ReportSpec(chart="Line", rows="Created", date_grain="Month")
    fig = build_figure(build_pivot(df, spec), spec)
    assert fig.layout.xaxis.type == "date"
    assert fig.layout.hovermode == "x unified"
    assert fig.data[0].line.width == 2


def test_single_series_bars_get_a_colour_per_category_by_default(df):
    spec = ReportSpec(chart="Column", rows="Issue Type")
    fig = build_figure(build_pivot(df, spec), spec, category_rank(df, "Issue Type"))
    assert len(fig.data) == 1
    assert list(fig.data[0].marker.color) == list(PALETTE[:3])       # Bug, Story, Task by rank
    assert fig.layout.showlegend is False


def test_single_series_can_use_one_colour_for_every_bar(df):
    spec = ReportSpec(chart="Column", rows="Issue Type", color_by_category=False)
    fig = build_figure(build_pivot(df, spec), spec)
    assert fig.data[0].marker.color == PALETTE[0]


def test_theme_switches_the_palette(df):
    spec = ReportSpec(chart="Column", rows="Issue Type", series="Status")
    order = category_rank(df, "Status")
    aurora = build_figure(build_pivot(df, spec), spec, order, theme="Aurora")
    classic = build_figure(build_pivot(df, spec), spec, order, theme="Classic")
    assert aurora.data[0].marker.color == THEMES["Aurora"].palette[0]
    assert classic.data[0].marker.color == THEMES["Classic"].palette[0]


def test_lines_and_areas_get_a_gradient_wash(df):
    for chart in ("Line", "Area"):
        spec = ReportSpec(chart=chart, rows="Created", date_grain="Month")
        fig = build_figure(build_pivot(df, spec), spec)
        assert fig.data[0].fillgradient.type == "vertical"


def test_number_reports_are_not_figures(df):
    spec = ReportSpec(chart="Number")
    with pytest.raises(ValueError):
        build_figure(build_pivot(df, spec), spec)


def test_stacked_bars_are_separated_by_surface_gap(df):
    spec = ReportSpec(chart="Stacked column", rows="Issue Type", series="Status")
    fig = build_figure(build_pivot(df, spec), spec)
    assert {t.marker.line.color for t in fig.data} == {SURFACE}
    assert {t.marker.line.width for t in fig.data} == {2}
    assert fig.layout.barmode == "stack"


def test_colours_follow_the_entity_not_its_rank():
    order = ["Open", "Done", "In QA", "Blocked"]
    full = color_map(["Open", "Done", "In QA"], order)
    filtered = color_map(["Done", "In QA"], order)      # "Open" filtered out
    assert filtered == {"Done": full["Done"], "In QA": full["In QA"]}


def test_other_is_neutral_and_unranked_categories_get_free_slots():
    cmap = color_map(["New", OTHER, "Open"], ["Open"])
    assert cmap["Open"] == PALETTE[0]
    assert cmap[OTHER] == OTHER_COLOR
    assert cmap["New"] == PALETTE[1]


def test_empty_result_shows_message(df):
    spec = ReportSpec(chart="Column", rows="Issue Type", filters={"Issue Type": ["Epic"]})
    fig = build_figure(build_pivot(df, spec), spec)
    assert "No issues" in fig.layout.annotations[0].text




def test_paper_colour_is_set_on_the_figure_not_only_the_template(df):
    # Streamlit repaints the paper with its theme background unless the figure sets it.
    spec = ReportSpec(chart="Column", rows="Issue Type")
    fig = build_figure(build_pivot(df, spec), spec)
    assert fig.layout.paper_bgcolor == SURFACE and fig.layout.plot_bgcolor == SURFACE


def test_few_columns_get_extra_air_so_bars_stay_thin(df):
    spec = ReportSpec(chart="Column", rows="Issue Type")
    fig = build_figure(build_pivot(df, spec), spec)
    assert fig.layout.bargap > 0.38


def test_height_override_never_shrinks_below_the_natural_height(df):
    from jira_insights.charts import natural_height
    spec = ReportSpec(chart="Column", rows="Issue Type")
    result = build_pivot(df, spec)
    assert build_figure(result, spec).layout.height == natural_height(result, spec) == 360
    assert build_figure(result, spec, height=620).layout.height == 620
    assert build_figure(result, spec, height=100).layout.height == 360
