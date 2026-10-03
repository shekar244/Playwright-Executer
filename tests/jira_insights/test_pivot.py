import pandas as pd
import pytest

from jira_insights.pivot import (MAX_SERIES, OTHER, ReportSpec, apply_filters, build_pivot,
                                 category_rank, distinct_values)

NOW = pd.Timestamp("2026-10-01")
MULTI = {"Labels"}


@pytest.fixture
def df():
    return pd.DataFrame({
        "Key": [f"QA-{i}" for i in range(1, 9)],
        "Issue Type": ["Bug", "Bug", "Bug", "Story", "Story", "Bug", "Task", "Story"],
        "Priority": ["Low", "High", "Medium", "High", "Low", "Highest", "Medium", None],
        "Status Category": ["To Do", "Done", "In Progress", "Done", "To Do", "Done", "To Do", "In Progress"],
        "Labels": ["web", "web, api", None, "api", "web", "mobile", None, "api, web"],
        "Story Points": [None, None, None, 3.0, 5.0, None, 1.0, 8.0],
        "Created": pd.to_datetime(["2026-06-03", "2026-06-20", "2026-08-02", "2026-08-15",
                                   "2026-09-01", "2026-09-10", "2026-09-25", "2026-09-28"]),
    })


def test_count_by_rows_sorted_by_value(df):
    r = build_pivot(df, ReportSpec(rows="Issue Type"), MULTI)
    assert r.row_order == ["Bug", "Story", "Task"]
    assert r.long["Value"].tolist() == [4, 3, 1]
    assert r.total == 8


def test_label_sort_uses_semantic_priority_order(df):
    r = build_pivot(df, ReportSpec(rows="Priority", sort="Label"), MULTI)
    assert r.row_order == ["Highest", "High", "Medium", "Low", "(none)"]


def test_series_builds_wide_table_with_totals(df):
    r = build_pivot(df, ReportSpec(rows="Issue Type", series="Status Category"), MULTI)
    assert r.series_order[0] == "To Do"
    assert r.table.loc["Bug"].to_dict() == {"To Do": 1, "Done": 2, "In Progress": 1, "Total": 4}
    assert r.long.loc[(r.long["Issue Type"] == "Story") & (r.long["Status Category"] == "Done"), "Value"].item() == 1


def test_measure_aggregations(df):
    total = build_pivot(df, ReportSpec(rows="Issue Type", value="Story Points", agg="Sum"), MULTI)
    assert dict(zip(total.long["Issue Type"], total.long["Value"])) == {"Story": 16, "Task": 1, "Bug": 0}
    avg = build_pivot(df, ReportSpec(rows="Issue Type", value="Story Points", agg="Average"), MULTI)
    assert avg.table.loc["Story"].item() == pytest.approx(16 / 3)
    assert pd.isna(avg.table.loc["Bug"].item())                # no points → gap, not zero


def test_date_rows_bucket_by_grain_and_fill_empty_periods(df):
    r = build_pivot(df, ReportSpec(rows="Created", date_grain="Month"), MULTI)
    assert r.row_is_date
    assert [d.strftime("%Y-%m") for d in r.row_order] == ["2026-06", "2026-07", "2026-08", "2026-09"]
    assert r.long["Value"].tolist() == [2, 0, 2, 4]


def test_multi_value_dimension_is_exploded(df):
    r = build_pivot(df, ReportSpec(rows="Labels"), MULTI)
    assert dict(zip(r.long["Labels"], r.long["Value"])) == {"web": 4, "api": 3, "(none)": 2, "mobile": 1}


def test_filters_categorical_multi_value_and_dates(df):
    assert len(apply_filters(df, {"Issue Type": ["Bug"]})) == 4
    assert apply_filters(df, {"Labels": ["api"]}, MULTI)["Key"].tolist() == ["QA-2", "QA-4", "QA-8"]
    assert len(apply_filters(df, {"Priority": ["(none)"]})) == 1
    assert len(apply_filters(df, {"Created": {"last_days": 30}}, now=NOW)) == 4
    ranged = apply_filters(df, {"Created": {"from": "2026-08-01", "to": "2026-08-31"}})
    assert ranged["Key"].tolist() == ["QA-3", "QA-4"]
    assert len(apply_filters(df, {"Missing": ["x"], "Issue Type": []})) == 8   # ignored


def test_series_beyond_palette_fold_into_other():
    many = pd.DataFrame({"Team": ["A"] * 20, "Component": [f"c{i:02d}" for i in range(20)]})
    r = build_pivot(many, ReportSpec(rows="Team", series="Component"))
    assert len(r.series_order) == MAX_SERIES
    assert r.series_order[-1] == OTHER
    assert r.table.loc["A", OTHER] == 20 - (MAX_SERIES - 1)


def test_top_n_folds_or_drops_the_tail(df):
    folded = build_pivot(df, ReportSpec(rows="Issue Type", top_n=1), MULTI)
    assert folded.row_order == ["Bug", OTHER]
    dropped = build_pivot(df, ReportSpec(rows="Issue Type", top_n=1, fold_other=False), MULTI)
    assert dropped.row_order == ["Bug"]


def test_normalize_makes_each_row_100_percent(df):
    r = build_pivot(df, ReportSpec(chart="Stacked column", rows="Issue Type",
                                   series="Status Category", normalize=True), MULTI)
    assert r.table.drop(columns="Total", errors="ignore").sum(axis=1).round(6).eq(100).all()
    assert "Total" not in r.table


def test_cumulative_running_total(df):
    r = build_pivot(df, ReportSpec(chart="Line", rows="Created", date_grain="Month", cumulative=True), MULTI)
    assert r.long["Value"].tolist() == [2, 2, 4, 8]


def test_donut_is_capped_at_six_slices():
    big = pd.DataFrame({"Assignee": [f"user{i}" for i in range(10)]})
    r = build_pivot(big, ReportSpec(chart="Donut", rows="Assignee", series="ignored"))
    assert len(r.row_order) == 6 and r.row_order[-1] == OTHER
    assert r.series_order == []


def test_number_ignores_dimensions(df):
    r = build_pivot(df, ReportSpec(chart="Number", rows="Issue Type",
                                   filters={"Issue Type": ["Bug", "Defect"]}), MULTI)
    assert r.total == 4 and r.empty


def test_same_field_for_rows_and_series_is_not_split(df):
    r = build_pivot(df, ReportSpec(rows="Issue Type", series="Issue Type"), MULTI)
    assert r.series_order == []


def test_empty_slice_returns_empty_result(df):
    r = build_pivot(df, ReportSpec(rows="Issue Type", filters={"Issue Type": ["Epic"]}), MULTI)
    assert r.empty and r.total == 0


def test_spec_round_trip_and_required_columns():
    spec = ReportSpec(name="x", rows="Priority", series="Status", value="Story Points",
                      filters={"Issue Type": ["Bug"]}).with_id()
    again = ReportSpec.from_dict({**spec.to_dict(), "unknown_key": 1})
    assert again == spec and len(spec.id) == 10
    assert spec.required_columns() == {"Priority", "Status", "Story Points", "Issue Type"}


def test_category_rank_and_distinct_values(df):
    assert category_rank(df, "Issue Type") == ["Bug", "Story", "Task"]
    assert category_rank(df, "Created") == []
    assert distinct_values(df, "Labels", MULTI) == ["api", "mobile", "web", "(none)"]


def test_status_category_series_stack_in_workflow_order(df):
    r = build_pivot(df, ReportSpec(rows="Priority", series="Status Category"), MULTI)
    assert r.series_order == ["To Do", "In Progress", "Done"]
