import pandas as pd
import pytest

from jira_insights.transform import flatten_value, frame_from_export, issues_to_frame

NOW = pd.Timestamp("2026-10-01 00:00:00")


@pytest.mark.parametrize("raw, expected", [
    (None, None),
    ("text", "text"),
    (3.0, 3.0),
    ({"displayName": "Ada Lovelace", "name": "ada"}, "Ada Lovelace"),
    ({"name": "High", "id": "2"}, "High"),
    ({"value": "Sev 1", "id": "10"}, "Sev 1"),
    ({"value": "Web", "child": {"value": "Checkout"}}, "Web / Checkout"),
    ([{"name": "API"}, {"name": "UI"}], "API, UI"),
    ([], None),
    ({"type": "doc", "content": []}, None),
    ("com.atlassian.greenhopper.service.sprint.Sprint@1a[id=7,state=CLOSED,name=Sprint 42,goal=]", "Sprint 42"),
    ([{"id": 7, "name": "Sprint 41"}, {"id": 8, "name": "Sprint 42"}], "Sprint 41, Sprint 42"),
])
def test_flatten_value(raw, expected):
    assert flatten_value(raw) == expected


def _issue(key, **fields):
    return {"key": key, "fields": fields}


@pytest.fixture
def frame():
    issues = [
        _issue("QA-1", summary="Login fails", issuetype={"name": "Bug"},
               status={"name": "In QA", "statusCategory": {"name": "In Progress"}},
               priority={"name": "High"}, labels=["regression", "web"],
               created="2026-09-01T10:00:00.000+0000", resolutiondate=None,
               customfield_10016=None, customfield_99999=None, subtasks=[{"key": "QA-9"}]),
        _issue("QA-2", summary="Checkout story", issuetype={"name": "Story"},
               status={"name": "Done", "statusCategory": {"name": "Done"}},
               priority={"name": "Medium"}, labels=[],
               created="2026-09-10T00:00:00.000+0000", resolutiondate="2026-09-20T00:00:00.000+0000",
               customfield_10016=5.0, customfield_99999=None, subtasks=[]),
    ]
    names = {"customfield_10016": "Story Points", "customfield_99999": "Empty Field", "summary": "Ignored"}
    return issues_to_frame(issues, names, now=NOW)


def test_issue_frame_columns_use_friendly_names(frame):
    df, _ = frame
    assert list(df.columns[:5]) == ["Key", "Summary", "Issue Type", "Status", "Status Category"]
    assert df.loc[0, "Issue Type"] == "Bug"
    assert df.loc[1, "Story Points"] == 5.0
    assert "Empty Field" not in df.columns            # all-null custom fields are dropped
    assert df["Sub-tasks"].tolist() == [1, 0]


def test_issue_frame_reports_multi_value_columns(frame):
    df, multi = frame
    assert multi == ["Labels"]
    assert df.loc[0, "Labels"] == "regression, web"


def test_issue_frame_derives_open_closed_age_and_resolution(frame):
    df, _ = frame
    assert df["Open/Closed"].tolist() == ["Open", "Closed"]
    assert pd.api.types.is_datetime64_any_dtype(df["Created"])
    assert df.loc[0, "Age (days)"] == pytest.approx(29.6, abs=0.05)      # open → aged until NOW
    assert df.loc[1, "Age (days)"] == 10.0
    assert df.loc[1, "Resolution Time (days)"] == 10.0
    assert pd.isna(df.loc[0, "Resolution Time (days)"])


def test_custom_field_named_like_a_system_column_is_disambiguated():
    df, _ = issues_to_frame([_issue("QA-1", summary="s", customfield_1="x")],
                            {"customfield_1": "Summary"}, now=NOW)
    assert df.loc[0, "Summary"] == "s"
    assert df.loc[0, "Summary (customfield_1)"] == "x"


def test_csv_export_merges_repeated_headers_and_parses_types():
    csv = (
        "Summary,Issue key,Issue Type,Status,Labels,Labels,Custom field (Story Points),Created,Resolved\n"
        "Login fails,QA-1,Bug,Open,regression,web,3,01/Sep/26 10:00 AM,\n"
        "Checkout,QA-2,Story,Done,,,5,10/Sep/26 9:00 AM,20/Sep/26 9:00 AM\n"
    ).encode()
    df, multi = frame_from_export(csv, "export.csv", now=NOW)

    assert multi == ["Labels"]
    assert df["Key"].tolist() == ["QA-1", "QA-2"]
    assert df.loc[0, "Labels"] == "regression, web"
    assert pd.isna(df.loc[1, "Labels"])
    assert df["Story Points"].tolist() == [3, 5]
    assert df.loc[0, "Created"] == pd.Timestamp("2026-09-01 10:00")
    assert df["Open/Closed"].tolist() == ["Open", "Closed"]        # no status category → Resolved
    assert df.loc[1, "Resolution Time (days)"] == 10.0


def test_header_only_export_returns_empty_frame():
    df, multi = frame_from_export(b"Summary,Issue key,Labels,Labels\n", "empty.csv", now=NOW)
    assert df.empty and multi == []


def test_open_closed_falls_back_to_status_names_without_category_or_resolved():
    csv = ("Issue key,Issue Type,Status\n"
           "QA-1,Task,Closed\nQA-2,Bug,In Progress\nQA-3,Story,Done\nQA-4,Bug,Won't Do\n").encode()
    df, _ = frame_from_export(csv, "export.csv", now=NOW)
    assert df["Open/Closed"].tolist() == ["Closed", "Open", "Closed", "Closed"]
