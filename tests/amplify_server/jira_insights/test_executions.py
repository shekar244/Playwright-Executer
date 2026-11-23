import pandas as pd

from jira_insights.executions import executions_to_frame, result_group, status_label

CLOUD = [
    {"execution": {"id": "e1", "status": {"name": "PASS", "id": 1}, "cycleName": "Sprint 5", "cycleId": "33",
                   "versionId": -1, "executedOn": 1759500000000, "executedByAccountId": "acc-1",
                   "defects": [{"key": "ABC-90"}], "comment": "ok"},
     "issueKey": "ABC-1", "issueSummary": "Login works", "component": "Checkout, Web", "projectKey": "ABC",
     "versionName": "Unscheduled", "priority": "High", "issueLabel": "smoke"},
    {"execution": {"id": "e2", "status": {"name": "UNEXECUTED", "id": -1}, "cycleId": "-1"},
     "issueKey": "ABC-2", "issueSummary": "Search", "projectKey": "ABC"},
]
SERVER = [
    {"id": 101, "issueKey": "QA-7", "issueSummary": "Pay", "status": {"id": 2, "name": "FAIL"},
     "cycleName": "Regression", "versionName": "R1", "folderName": "Payments",
     "executedOn": "03/Oct/26 5:07 PM", "executedByDisplay": "Ava Chen", "labels": ["regression"],
     "component": [{"name": "Cart"}], "totalDefectCount": 2, "projectKey": "QA"},
    {"id": 102, "issueKey": "QA-8", "status": {"id": 3, "name": "WIP"}, "cycleName": "Regression"},
    {"id": 103, "issueKey": "QA-9", "status": {"id": 4}, "cycleName": "Regression"},
]


def test_status_labels_and_result_groups():
    assert status_label({"name": "PASS"}) == "Pass" and status_label({"id": 4}) == "Blocked"
    assert status_label("NOT_EXECUTED") == "Not Executed" and status_label(None) == "Unexecuted"
    assert [result_group(s) for s in ("Pass", "Fail", "WIP", "Blocked", "Unexecuted", "Deferred")] == \
        ["Passed", "Failed", "In progress", "Blocked", "Not run", "Other"]


def test_cloud_search_objects():
    df, multi = executions_to_frame(CLOUD)
    first, second = df.iloc[0], df.iloc[1]
    assert (first["Key"], first["Execution Status"], first["Result"], first["Executed"]) == ("ABC-1", "Pass", "Passed", "Yes")
    assert first["Cycle"] == "Sprint 5" and second["Cycle"] == "Ad hoc"
    assert first["Executed On"] == pd.Timestamp("2025-10-03 14:00:00")
    assert first["Defects"] == 1 and first["Defect Keys"] == "ABC-90"
    assert first["Components"] == "Checkout, Web" and first["Labels"] == "smoke"
    assert (second["Result"], second["Executed"]) == ("Not run", "No")
    assert set(multi) <= {"Labels", "Components", "Defect Keys"} and "Components" in multi


def test_server_executions():
    df, _ = executions_to_frame(SERVER)
    assert df["Execution Status"].tolist() == ["Fail", "WIP", "Blocked"]
    assert df["Result"].tolist() == ["Failed", "In progress", "Blocked"]
    row = df.iloc[0]
    assert row["Executed By"] == "Ava Chen" and row["Folder"] == "Payments" and row["Defects"] == 2
    assert row["Executed On"] == pd.Timestamp("2026-10-03 17:07:00")
    assert row["Components"] == "Cart"


def test_no_records_gives_empty_frame():
    df, multi = executions_to_frame([])
    assert df.empty and multi == []


def test_user_ids_and_name_mapping():
    from jira_insights.executions import apply_user_names, user_ids
    df = pd.DataFrame({"Executed By": ["5b10ac8d82e05b22cc7d4ef5", "JIRAUSER10100", "Ava Chen", None],
                       "Assignee": ["712020:2a1b3c4d-0000-1111-2222-333344445555", None, None, None]})
    assert user_ids(df) == ["5b10ac8d82e05b22cc7d4ef5", "712020:2a1b3c4d-0000-1111-2222-333344445555", "JIRAUSER10100"]
    shown = apply_user_names(df, {"5b10ac8d82e05b22cc7d4ef5": "Ben Ortiz", "JIRAUSER10100": "Chloe Park"})
    assert shown["Executed By"].tolist()[:3] == ["Ben Ortiz", "Chloe Park", "Ava Chen"]
    assert shown["Assignee"][0].startswith("712020:")            # no name yet → id stays visible
    assert df["Executed By"][0] == "5b10ac8d82e05b22cc7d4ef5"     # stored data keeps the raw id
