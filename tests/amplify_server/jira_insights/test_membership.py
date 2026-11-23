import pandas as pd

from jira_insights.pivot import ReportSpec
from jira_insights.store import Store
from jira_insights.ui import membership

JIRA = {"Key", "Issue Type", "Status", "Priority"}
RUNS = {"Key", "Result", "Executed", "Cycle", "Priority"}


def reports(store):
    specs = [ReportSpec(name="Stories by status", rows="Status", filters={"Issue Type": ["Story"]}),
             ReportSpec(name="Pass rate by cycle", chart="Gauge", rows="Cycle", gauge_where={"Result": ["Passed"]}),
             ReportSpec(name="By priority", rows="Priority")]
    return [store.save_report(s) for s in specs]


def test_automatic_boards_show_every_report_that_fits(tmp_path):
    store = Store(tmp_path)
    store.list_reports()
    jira_r, runs_r, both_r = reports(store)
    ids = lambda rows: {i for row in rows for i in row}
    all_reports = store.list_reports()
    assert {jira_r.id, both_r.id} <= ids(membership.board_for(store, "jira", all_reports, JIRA, None))
    assert runs_r.id not in ids(membership.board_for(store, "jira", all_reports, JIRA, None))
    assert ids(membership.board_for(store, "runs", all_reports, RUNS, None)) >= {runs_r.id, both_r.id}


def test_custom_board_shows_only_its_reports_and_never_unfit_ones(tmp_path):
    store = Store(tmp_path)
    jira_r, runs_r, both_r = reports(store)
    all_reports = store.list_reports()
    store.set_dashboard("jira", [[both_r.id], [runs_r.id]])                  # runs report can't show on Jira
    assert membership.board_for(store, "jira", all_reports, JIRA, None) == [[both_r.id]]
    assert membership.label(runs_r).startswith("🧪 R-") and membership.label(jira_r).startswith("🧾 R-")
