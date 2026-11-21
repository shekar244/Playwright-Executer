"""Carry-forward per sprint: an issue that is in more than one sprint was carried forward out of each earlier one.

"Carried Forward From" lists those sprints (every sprint the issue was in except its last), so counting issues by it
puts an issue under each sprint it left: the sprint-by-sprint count of stories or defects carried forward. It is derived
when a dataset is loaded, so datasets pulled before the column existed get it without a re-pull.
"""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from amplify.jira_insights.pivot import ReportSpec, build_pivot
from amplify.jira_insights.sprint_transform import (CARRIED_FORWARD_FROM, add_carry_forward, carried_forward_from,
                                                    sprint_issues_to_frame)
from amplify.jira_insights.store import SPRINT_CARRY_REPORTS, Store


# ── the rule ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("sprints, expected", [
    ("S1, S2, S3", "S1, S2"),                    # carried out of S1 and S2; S3 is where it is now
    ("S1, S2", "S1"),
    ("S1", None),                                # one sprint: not carried anywhere yet
    (None, None), (np.nan, None), ("", None), ("  ", None), (7, None),
    ("S1, S1, S2", "S1"),                        # a repeated sprint counts once
    ("S1,  S2, S3", "S1, S2"),                   # stray spaces around the names are ignored
])
def test_an_issue_was_carried_forward_out_of_every_sprint_but_its_last(sprints, expected):
    assert carried_forward_from(sprints) == expected


def test_sprint_names_with_spaces_dots_and_dates_are_kept_whole():
    cell = "P13N Mkt 2026.13 - (6/10-6/23), P13N Mkt 2026.15, P13N Mkt 2026.23"

    assert carried_forward_from(cell) == "P13N Mkt 2026.13 - (6/10-6/23), P13N Mkt 2026.15"


def test_the_column_is_added_as_a_multi_value_column_without_touching_the_original():
    df = pd.DataFrame({"Key": ["A-1", "A-2"], "Sprint": ["S1, S2", "S2"]})
    before = df.copy(deep=True)

    out, multi = add_carry_forward(df, ["Sprint"])

    assert out[CARRIED_FORWARD_FROM].tolist()[0] == "S1" and pd.isna(out[CARRIED_FORWARD_FROM].iloc[1])
    assert multi == ["Sprint", CARRIED_FORWARD_FROM] and df.equals(before)


def test_adding_it_twice_changes_nothing_and_a_frame_without_sprints_is_left_alone():
    df = pd.DataFrame({"Key": ["A-1"], "Sprint": ["S1, S2"]})
    once, multi = add_carry_forward(df, ["Sprint"])

    twice, multi_again = add_carry_forward(once, multi)
    plain = pd.DataFrame({"Key": ["A-1"]})

    assert twice is once and multi_again == multi
    frame, multi_cols = add_carry_forward(plain, ["Labels"])
    assert frame is plain and multi_cols == ["Labels"]


def test_a_freshly_pulled_sprint_frame_has_it():
    def issue(key, kind, sprints):
        return {"key": key, "fields": {"summary": key, "issuetype": {"name": kind}, "created": "2026-09-01T10:00:00.000+0000",
                "status": {"name": "Open", "statusCategory": {"key": "new", "name": "To Do"}},
                "customfield_10020": [{"id": i, "name": n, "state": "closed"} for i, n in enumerate(sprints, 1)]}}

    df, multi = sprint_issues_to_frame([issue("QA-1", "Story", ["S1", "S2", "S3"]), issue("QA-2", "Bug", ["S3"])],
                                       {"customfield_10020": "Sprint"}, {"name": "S3"})

    assert df[CARRIED_FORWARD_FROM].tolist()[0] == "S1, S2" and pd.isna(df[CARRIED_FORWARD_FROM].iloc[1])
    assert CARRIED_FORWARD_FROM in multi


# ── the sprint-by-sprint reports ──────────────────────────────────────────────

@pytest.fixture
def issues():
    return pd.DataFrame({
        "Key": ["S-1", "S-2", "S-3", "S-4", "D-1", "D-2", "D-3", "E-1"],
        "Issue Type": ["Story", "Story", "Story", "Story", "Bug", "Defect", "Bug", "Epic"],
        "Sprint": ["S1, S2, S3",          # story: left S1 and S2
                   "S2, S3",              # story: left S2
                   "S3",                  # story: never carried
                   "S1, S2",              # story: left S1 (still open in S2)
                   "S1, S2, S3",          # bug: left S1 and S2
                   "S2, S3",              # defect: left S2
                   "S3",                  # bug: never carried
                   "S1, S2"],             # epic: not a story or a defect
    })


def counts(issues, report):
    multi = ["Sprint"]
    frame, multi = add_carry_forward(issues, multi)
    result = build_pivot(frame, ReportSpec.from_dict(report), set(multi))
    return {k: int(v) for k, v in result.table.iloc[:, 0].items() if v}


def test_stories_carried_forward_are_counted_under_every_sprint_they_left(issues):
    stories = next(r for r in SPRINT_CARRY_REPORTS if r["name"].startswith("Stories"))

    # S-1 and S-4 left S1; S-1 and S-2 left S2; S-4's last sprint is S2 and S-3 only ever had S3, so neither left it
    assert counts(issues, stories) == {"S1": 2, "S2": 2}


def test_defects_carried_forward_count_bugs_and_defects_alike(issues):
    defects = next(r for r in SPRINT_CARRY_REPORTS if r["name"].startswith("Defects"))

    assert counts(issues, defects) == {"S1": 1, "S2": 2}     # D-1 left S1 and S2; D-2 left S2


def test_issues_that_were_never_carried_forward_are_not_a_bar(issues):
    for report in SPRINT_CARRY_REPORTS:
        assert "(none)" not in counts(issues, report)


def test_the_last_sprint_of_an_issue_is_never_counted(issues):
    for report in SPRINT_CARRY_REPORTS:
        assert "S3" not in counts(issues, report)


def test_the_reports_need_the_sprint_derived_column_and_the_issue_type():
    for report in SPRINT_CARRY_REPORTS:
        assert ReportSpec.from_dict(report).required_columns() == {CARRIED_FORWARD_FROM, "Issue Type"}


# ── datasets: old ones get the column, new reports reach old workspaces ───────

@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "ws")


def save_old_style(store, name="Old", with_sprint=True):
    df = pd.DataFrame({"Key": ["A-1", "A-2"], "Issue Type": ["Story", "Bug"]})
    if with_sprint:
        df["Sprint"] = ["S1, S2, S3", "S3"]
    return store.save_dataset(name, df, source="jql", multi_cols=["Sprint"] if with_sprint else [])


def test_a_dataset_saved_before_the_column_existed_gets_it_when_loaded(store):
    meta = save_old_style(store)

    df, loaded = store.load_dataset(meta["slug"])

    assert df[CARRIED_FORWARD_FROM].tolist()[0] == "S1, S2"
    assert CARRIED_FORWARD_FROM in loaded["multi_value_cols"] and "Sprint" in loaded["multi_value_cols"]


def test_the_stored_file_is_not_rewritten_by_loading_it(store):
    meta = save_old_style(store)
    stored = store._path("datasets", meta["slug"], ".parquet")
    before = stored.read_bytes()

    store.load_dataset(meta["slug"])

    assert stored.read_bytes() == before and CARRIED_FORWARD_FROM not in pd.read_parquet(stored).columns


def test_the_column_listing_that_decides_which_reports_fit_includes_it(store):
    meta = save_old_style(store)

    assert CARRIED_FORWARD_FROM in store.dataset_columns(meta["slug"])


def test_a_dataset_without_sprints_is_left_as_it_was(store):
    meta = save_old_style(store, "Plain", with_sprint=False)

    df, loaded = store.load_dataset(meta["slug"])

    assert CARRIED_FORWARD_FROM not in df.columns and CARRIED_FORWARD_FROM not in store.dataset_columns(meta["slug"])
    assert loaded["multi_value_cols"] == []


def names(store):
    return {r.name for r in store.list_reports()}


def test_a_workspace_seeded_before_the_carry_forward_reports_existed_gets_them_once(store):
    store.set_setting("sprint_starters_seeded", True)              # the old workspace: starters already added

    assert store.seed_sprint_starters() is True

    assert {r["name"] for r in SPRINT_CARRY_REPORTS} <= names(store)
    assert store.seed_sprint_starters() is False                    # and only once


def test_deleting_a_carry_forward_report_sticks(store):
    store.seed_sprint_starters()
    gone = next(r for r in store.list_reports() if r.name.startswith("Stories carried"))
    store.delete_report(gone.id)

    store.seed_sprint_starters()

    assert not any(n.startswith("Stories carried") for n in names(store))
    assert any(n.startswith("Defects carried") for n in names(store))


def test_the_reports_are_not_duplicated_when_one_with_that_name_exists(store):
    store.save_report(ReportSpec.from_dict({**SPRINT_CARRY_REPORTS[0], "chart": "Bar"}))   # the user's own version

    store.seed_sprint_starters()

    same = [r for r in store.list_reports() if r.name == SPRINT_CARRY_REPORTS[0]["name"]]
    assert len(same) == 1 and same[0].chart == "Bar"


# ── in the real app ───────────────────────────────────────────────────────────

APP = Path(__file__).resolve().parents[2] / "jira_insights" / "app.py"


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    pytest.importorskip("plotly")
    pytest.importorskip("pygwalker")
    from amplify.config import settings
    monkeypatch.setattr(settings, "DATABASE_PATH", tmp_path / "amplify.db")
    monkeypatch.setenv("JIRA_INSIGHTS_HOME", str(tmp_path / "ws"))
    monkeypatch.delenv("QEA_API_URL", raising=False)
    store = Store(tmp_path / "ws")
    df = pd.DataFrame({
        "Key": ["S-1", "S-2", "S-3", "D-1"], "Issue Type": ["Story", "Story", "Story", "Bug"],
        "Status": ["Open", "Done", "Open", "Done"], "Status Category": ["To Do", "Done", "To Do", "Done"],
        "Sprint": ["S1, S2, S3", "S2, S3", "S3", "S1, S2, S3"], "Sprint Name": ["S3"] * 4, "Story Points": [3.0, 2.0, 1.0, None],
    })
    store.save_dataset("Sprint 3", df, source="sprint", multi_cols=["Sprint"],
                       extra={"sprint": {"name": "S3", "state": "active", "id": 3}})
    store.set_setting("sprint_starters_seeded", True)              # a workspace that predates the new reports
    return store


def open_view(view):
    from streamlit.testing.v1 import AppTest
    from amplify import sso
    from amplify.jira_insights import options as insights_options
    insights_options._cache.update(at=0.0, url=None, value=None)
    at = AppTest.from_file(str(APP), default_timeout=90)
    at.query_params["sso"] = sso.issue_insights_token(1, "tester")
    at.session_state["view_pending"] = view
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def test_the_sprint_reports_tab_shows_the_carry_forward_reports_for_an_existing_workspace(workspace):
    assert not any(n.startswith("Stories carried") for n in names(workspace))

    at = open_view("Sprint Reports")

    assert {r["name"] for r in SPRINT_CARRY_REPORTS} <= names(workspace)
    shown = " ".join(str(m.value) for m in at.markdown)
    assert "Stories carried forward, by sprint" in shown and "Defects carried forward, by sprint" in shown
