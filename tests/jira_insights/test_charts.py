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


def test_gauges_one_per_category_coloured_by_threshold():
    from jira_insights.charts import CRITICAL, GOOD, WARNING
    runs = pd.DataFrame({"Cycle": ["A"] * 10 + ["B"] * 10 + ["C"] * 10,
                         "Result": ["Passed"] * 9 + ["Failed"] + ["Passed"] * 6 + ["Failed"] * 4 + ["Passed"] * 3 + ["Failed"] * 7})
    spec = ReportSpec(chart="Gauge", rows="Cycle", gauge_where={"Result": ["Passed"]}, gauge_bands=[50, 80],
                      sort="Label")
    fig = build_figure(build_pivot(runs, spec), spec)
    assert [t.type for t in fig.data] == ["indicator"] * 3
    assert [round(t.value) for t in fig.data] == [90, 60, 30]
    assert [t.gauge.bar.color for t in fig.data] == [GOOD, WARNING, CRITICAL]
    assert fig.data[0].number.suffix == "%" and tuple(fig.data[0].gauge.axis.range) == (0, 100)
    lower = build_figure(build_pivot(runs, spec), ReportSpec(**{**spec.to_dict(), "higher_is_better": False}))
    assert lower.data[0].gauge.bar.color == CRITICAL


def test_single_gauge_gets_a_round_automatic_maximum(df):
    spec = ReportSpec(chart="Gauge")
    fig = build_figure(build_pivot(df, spec), spec)
    assert len(fig.data) == 1 and fig.data[0].value == 6 and tuple(fig.data[0].gauge.axis.range) == (0, 10)


def test_execution_results_use_status_colours():
    from jira_insights.charts import CRITICAL, GOOD
    runs = pd.DataFrame({"Cycle": ["A", "A", "B"], "Result": ["Passed", "Failed", "Passed"]})
    spec = ReportSpec(chart="Stacked bar", rows="Cycle", series="Result")
    fig = build_figure(build_pivot(runs, spec), spec)
    assert {t.name: t.marker.color for t in fig.data} == {"Passed": GOOD, "Failed": CRITICAL}



def test_four_bands_and_reversed_direction():
    from jira_insights.charts import CRITICAL, GOOD, LIGHT_GOOD, WARNING, band_colors, gauge_color
    spec = ReportSpec(chart="Meter", gauge_bands=[40, 60, 80])
    assert band_colors(spec) == [CRITICAL, WARNING, LIGHT_GOOD, GOOD]
    assert [gauge_color(v, 0, 100, spec) for v in (10, 50, 70, 95)] == [CRITICAL, WARNING, LIGHT_GOOD, GOOD]
    lower = ReportSpec(chart="Meter", gauge_bands=[40, 60, 80], higher_is_better=False)
    assert gauge_color(10, 0, 100, lower) == GOOD and gauge_color(95, 0, 100, lower) == CRITICAL
    rating = ReportSpec(chart="Meter", gauge_min=1, gauge_max=5, gauge_bands=[25, 50, 75])
    assert gauge_color(4.2, 1, 5, rating) == GOOD and gauge_color(2.9, 1, 5, rating) == WARNING


def test_meters_draw_bands_and_a_needle_per_category():
    ratings = pd.DataFrame({"Area": ["Overall"] * 2 + ["Coach"] * 2 + ["On time"] * 2,
                            "Rating": [4, 4.4, 4.5, 4.1, 3.0, 2.8]})
    spec = ReportSpec(chart="Meter", rows="Area", value="Rating", agg="Average", gauge_min=1, gauge_max=5,
                      gauge_bands=[25, 50, 75], sort="Label")
    fig = build_figure(build_pivot(ratings, spec), spec)
    bands = [t for t in fig.data if t.type == "barpolar"]
    needles = [t for t in fig.data if t.type == "scatterpolar" and t.mode == "lines"]
    assert len(bands) == 3 and len(needles) == 3 and fig.layout.polar3.sector == (0, 180)
    assert len(bands[0].marker.color) == 4 and tuple(bands[0].width) == (45, 45, 45, 45)
    titles = [t.text[0] for t in fig.data if t.type == "scatterpolar" and t.mode == "text"]
    on_time = needles[titles.index("On time")]
    assert round(on_time.theta[0], 1) == round((2.9 - 1) / 4 * 180, 1)          # needle angle = completion
    hubs = [t for t in fig.data if t.type == "scatterpolar" and t.mode == "markers+text"]
    assert {lbl: h.text[0] for lbl, h in zip(titles, hubs)} == \
        {"Coach": "<b>4.3</b>", "On time": "<b>2.9</b>", "Overall": "<b>4.2</b>"}


def test_single_completion_meter_runs_0_to_100():
    runs = pd.DataFrame({"Status": ["Done", "Done", "Done", "To Do"]})
    spec = ReportSpec(chart="Meter", gauge_where={"Status": ["Done"]})
    fig = build_figure(build_pivot(runs, spec), spec)
    needle = next(t for t in fig.data if t.type == "scatterpolar" and t.mode == "lines")
    assert needle.theta[0] == 135                                                 # 75 % complete
    assert fig.layout.polar.angularaxis.ticktext[-1] == "100.0"


def test_manual_colours_replace_the_automatic_ones(df):
    from jira_insights.charts import colorable, effective_overrides
    spec = ReportSpec(chart="Column", rows="Issue Type", color_mode="manual", colors={"Bug": "#ff0000"})
    result = build_pivot(df, spec)
    assert set(colorable(result, spec)) == {"Bug", "Story", "Task"}
    fig = build_figure(result, spec, overrides=effective_overrides(spec, result, {}))
    fills = dict(zip(fig.data[0].x, fig.data[0].marker.color))
    assert fills["Bug"] == "#ff0000" and fills["Story"] == PALETTE[1]           # others stay automatic


def test_one_colour_charts_offer_a_single_picker(df):
    from jira_insights.charts import SINGLE_COLOR, colorable, effective_overrides
    spec = ReportSpec(chart="Line", rows="Created", color_mode="manual", colors={SINGLE_COLOR: "#123456"})
    result = build_pivot(df, spec)
    assert colorable(result, spec) == {SINGLE_COLOR: PALETTE[0]}
    fig = build_figure(result, spec, overrides=effective_overrides(spec, result, {}))
    assert fig.data[0].line.color == "#123456"


def test_field_defaults_apply_in_auto_mode_and_manual_wins(df):
    from jira_insights.charts import effective_overrides
    defaults = {"Status": {"Open": "#00aa00", "Done": "#aa0000"}, "Issue Type": {"Bug": "#0000ff"}}
    auto = ReportSpec(chart="Stacked column", rows="Issue Type", series="Status")
    result = build_pivot(df, auto)
    assert effective_overrides(auto, result, defaults) == {"Open": "#00aa00", "Done": "#aa0000"}
    fig = build_figure(result, auto, overrides=effective_overrides(auto, result, defaults))
    assert {t.name: t.marker.color for t in fig.data}["Open"] == "#00aa00"
    manual = ReportSpec(**{**auto.to_dict(), "color_mode": "manual", "colors": {"Open": "#ffffff", "x": "red;"}})
    assert effective_overrides(manual, result, defaults) == {"Open": "#ffffff", "Done": "#aa0000"}  # bad value dropped


def test_reports_saved_before_colours_default_to_auto():
    old = ReportSpec.from_dict({"chart": "Column", "rows": "Status"})
    assert old.color_mode == "auto" and old.colors == {}
