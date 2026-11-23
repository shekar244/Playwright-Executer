"""The Key column of a Table report (and of the Data view) links to the issue in Jira.

A Streamlit link column is an ordinary link in the viewer's browser: a click opens Jira in a new tab on the user's own
machine and never goes back to the Amplify/Insights server. What matters on this side is what may become a link:
only a plain http(s) Jira address, only real issue keys, and only for display (CSV and Excel keep the plain keys).
"""
import re
from pathlib import Path

import pandas as pd
import pytest

from jira_insights.pivot import TABLE, ReportSpec
from jira_insights.store import Store
from jira_insights.ui import common
from jira_insights.ui.common import _KEY_FROM_LINK, jira_site, with_issue_links

JIRA = "https://jira.example.test"


@pytest.fixture
def df():
    return pd.DataFrame({"Key": ["QA-1", "QA-22", "DATA_X-3"], "Summary": ["a", "b", "c"], "Status": ["Open", "Done", "Open"]})


# ── what may become a link ────────────────────────────────────────────────────

def test_keys_become_links_to_the_issue_and_nothing_else_changes(df):
    before = df.copy(deep=True)

    shown, config = with_issue_links(df, JIRA)

    assert shown["Key"].tolist() == [f"{JIRA}/browse/QA-1", f"{JIRA}/browse/QA-22", f"{JIRA}/browse/DATA_X-3"]
    assert shown[["Summary", "Status"]].equals(df[["Summary", "Status"]]) and list(config) == ["Key"]
    assert df.equals(before)                                  # the table itself (what downloads use) is untouched


@pytest.mark.parametrize("base", [JIRA, JIRA + "/", "https://host.example.test/jira", "http://localhost:8080",
                                  "https://host.example.test/browse", "https://host.example.test/browse/", "https://host.example.test/jira/browse"])
def test_the_link_is_the_base_url_a_slash_and_the_key_and_the_text_is_only_the_key(df, base):
    shown, _ = with_issue_links(df, base)
    trimmed = base.rstrip("/")
    expected_prefix = trimmed + "/" if trimmed.endswith("/browse") else trimmed + "/browse/"   # never .../browse/browse/KEY

    assert shown["Key"].tolist() == [expected_prefix + key for key in df["Key"]]
    assert [re.match(_KEY_FROM_LINK, url).group(1) for url in shown["Key"]] == df["Key"].tolist()
    assert "/browse/browse/" not in shown["Key"].iloc[0]


@pytest.mark.parametrize("unsafe", ["javascript:alert(1)", "file:///etc/passwd", "ftp://jira.example.test", "//jira.example.test",
                                    "jira.example.test", "https://user:secret@jira.example.test", "https://jira.example.test/?x=1",
                                    "https://jira.example.test/#frag", "https://jira.example.test:notaport", "http://", "", "   ", None, 7])
def test_only_a_plain_http_or_https_address_is_ever_linked(df, unsafe):
    assert jira_site(unsafe) == ""
    shown, config = with_issue_links(df, unsafe)
    assert shown is df and config == {}


def test_without_a_jira_address_the_keys_stay_plain_text(df):
    shown, config = with_issue_links(df, None)
    assert shown is df and config == {}


@pytest.mark.parametrize("keys", [["QA-1", "Cycle 4"], ["QA-1", "QA-2/../../admin"], ["TC-1:3", "TC-2:4"], ["qa-1"], ["QA-1 "], ["QA-1\n"], [""]])
def test_a_key_column_that_is_not_all_issue_keys_is_not_linked_at_all(keys):
    frame = pd.DataFrame({"Key": keys})
    shown, config = with_issue_links(frame, JIRA)
    assert shown is frame and config == {}


def test_a_missing_key_stays_empty_while_the_others_link():
    frame = pd.DataFrame({"Key": ["QA-1", None, "QA-3"]})

    shown, _ = with_issue_links(frame, JIRA)

    assert shown["Key"].tolist()[0] == f"{JIRA}/browse/QA-1" and pd.isna(shown["Key"].iloc[1])
    assert shown["Key"].iloc[2] == f"{JIRA}/browse/QA-3"


def test_a_table_without_a_key_column_or_without_rows_is_left_alone(df):
    no_key = df.drop(columns=["Key"])
    assert with_issue_links(no_key, JIRA) == (no_key, {}) or with_issue_links(no_key, JIRA)[1] == {}
    empty = df.iloc[0:0]
    assert with_issue_links(empty, JIRA)[1] == {}


# ── in the real app ───────────────────────────────────────────────────────────

APP = Path(__file__).resolve().parents[3] / "src" / "jira_insights" / "app.py"


@pytest.fixture
def workspace(tmp_path, monkeypatch, df):
    pytest.importorskip("plotly")
    pytest.importorskip("pygwalker")
    from amplify_server.config import settings
    monkeypatch.setattr(settings, "DATABASE_PATH", tmp_path / "amplify_server.db")
    monkeypatch.setenv("JIRA_INSIGHTS_HOME", str(tmp_path / "ws"))
    monkeypatch.delenv("QEA_API_URL", raising=False)
    store = Store(tmp_path / "ws")
    store.save_dataset("Linked", df, source="jql", extra={"jira_url": JIRA})
    return store


def open_view(view, *, state=None):
    from streamlit.testing.v1 import AppTest
    from amplify_server import sso
    from jira_insights import options as insights_options
    insights_options._cache.update(at=0.0, url=None, value=None)
    at = AppTest.from_file(str(APP), default_timeout=60)
    at.query_params["sso"] = sso.issue_insights_token(1, "tester")
    at.session_state["view_pending"] = view
    for key, value in (state or {}).items():
        at.session_state[key] = value
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def table_in_builder(columns):
    """The Builder resets its widgets on the first run, so the chart and columns are set after it (as the other tests do)."""
    at = open_view("Report Builder")
    at.session_state["rb_chart"] = TABLE
    at.session_state["rb_columns"] = columns
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def test_the_builder_preview_of_a_table_report_links_the_keys(workspace):
    at = table_in_builder(["Key", "Summary"])

    assert at.dataframe[0].value["Key"].tolist() == [f"{JIRA}/browse/QA-1", f"{JIRA}/browse/QA-22", f"{JIRA}/browse/DATA_X-3"]


def test_a_table_report_on_the_dashboard_links_the_keys(workspace):
    workspace.save_report(ReportSpec(name="Open", chart=TABLE, columns=["Key", "Status"], filters={"Status": ["Open"]}))

    at = open_view("Dashboard")

    frames = [d.value for d in at.dataframe if list(d.value.columns) == ["Key", "Status"]]
    assert frames and frames[0]["Key"].tolist() == [f"{JIRA}/browse/QA-1", f"{JIRA}/browse/DATA_X-3"]


def test_a_table_without_the_key_column_is_shown_as_it_is(workspace):
    at = table_in_builder(["Summary", "Status"])

    assert at.dataframe[0].value["Summary"].tolist() == ["a", "b", "c"]


def test_the_data_view_links_the_keys_through_the_same_code(workspace):
    at = open_view("Data")

    assert at.dataframe[0].value["Key"].iloc[0] == f"{JIRA}/browse/QA-1"


def test_csv_and_excel_still_get_the_plain_keys(workspace, monkeypatch):
    sent = []
    monkeypatch.setattr(common, "download_buttons", lambda frame, name, key, index=False: sent.append(frame.copy()))

    table_in_builder(["Key", "Status"])

    tables = [frame for frame in sent if list(frame.columns) == ["Key", "Status"]]   # the Builder also previews a pivot first
    assert tables and tables[-1]["Key"].tolist() == ["QA-1", "QA-22", "DATA_X-3"]


def test_a_dataset_that_does_not_know_its_jira_keeps_plain_keys(tmp_path, monkeypatch, df):
    pytest.importorskip("plotly")
    pytest.importorskip("pygwalker")
    from amplify_server.config import settings
    monkeypatch.setattr(settings, "DATABASE_PATH", tmp_path / "amplify_server.db")
    monkeypatch.setenv("JIRA_INSIGHTS_HOME", str(tmp_path / "ws"))
    monkeypatch.delenv("QEA_API_URL", raising=False)
    Store(tmp_path / "ws").save_dataset("Uploaded", df, source="upload")

    at = table_in_builder(["Key"])

    assert at.dataframe[0].value["Key"].tolist() == ["QA-1", "QA-22", "DATA_X-3"]
