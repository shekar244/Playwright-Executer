"""The Jira Insights page honours the admin's option visibility.

Renders the real app.py headlessly with Streamlit's AppTest, so it checks what a user would
actually be shown. Needs the Insights dependencies (plotly, pygwalker, ...); skipped without them.
"""
from pathlib import Path

import pandas as pd
import pytest

pytest.importorskip("plotly")
pytest.importorskip("pygwalker")
from streamlit.testing.v1 import AppTest  # noqa: E402

from amplify import server_secrets as ss  # noqa: E402
from amplify.jira_insights import options as insights_options  # noqa: E402
from amplify.jira_insights.store import Store  # noqa: E402
from amplify.jira_insights.ui.data_source import CORE_COLUMNS  # noqa: E402
from amplify.sso import issue_insights_token  # noqa: E402

APP = Path(__file__).resolve().parents[2] / "jira_insights" / "app.py"

SIDEBAR_SECTIONS = {
    "src_jql": "New dataset from JQL",
    "src_zephyr": "New dataset from Zephyr",
    "src_sprint": "New dataset from Sprint",
    "src_upload": "Upload Jira export",
    "excluded_fields": "Excluded fields",
}


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    from amplify.config import settings
    monkeypatch.setattr(settings, "DATABASE_PATH", tmp_path / "amplify.db")    # server_config.json sits beside it
    monkeypatch.setenv("JIRA_INSIGHTS_HOME", str(tmp_path / "ws"))
    monkeypatch.delenv("QEA_API_URL", raising=False)
    for name in ("JIRA_URL", "JIRA_USERNAME", "JIRA_API_TOKEN"):      # Jira deliberately not configured
        monkeypatch.delenv(name, raising=False)
    return tmp_path / "ws"


def render(hidden=(), *, pending=None):
    ss.save_insights_options({key: False for key in hidden})
    insights_options._cache.update(at=0.0, url=None, value=None)
    at = AppTest.from_file(str(APP), default_timeout=60)
    at.query_params["sso"] = issue_insights_token(1, "tester")      # signed in the way QEA does it
    if pending:
        at.session_state["view_pending"] = pending
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def sidebar_sections(at):
    labels = [e.label for e in at.expander]
    return {key for key, text in SIDEBAR_SECTIONS.items() if any(text in label for label in labels)}


def has_colour_theme(at):
    return any("Colour theme" in s.label for s in at.selectbox)


def test_everything_is_shown_by_default(workspace):
    at = render()
    assert sidebar_sections(at) == set(SIDEBAR_SECTIONS)
    assert has_colour_theme(at)


@pytest.mark.parametrize("hidden", [
    ["src_jql"], ["src_zephyr"], ["src_sprint"], ["src_upload"], ["excluded_fields"],
    ["src_jql", "src_upload", "excluded_fields"],
])
def test_hidden_sidebar_sections_are_not_rendered(workspace, hidden):
    at = render(hidden)
    assert sidebar_sections(at) == set(SIDEBAR_SECTIONS) - set(hidden)
    assert has_colour_theme(at)


def test_hidden_colour_theme_is_not_rendered(workspace):
    at = render(["colour_theme"])
    assert not has_colour_theme(at)
    assert sidebar_sections(at) == set(SIDEBAR_SECTIONS)


def test_get_started_text_only_mentions_what_is_enabled(workspace):
    at = render(["src_jql", "src_sprint"])
    text = "\n".join(m.value for m in at.markdown)
    assert "New dataset from Zephyr" in text and "Upload Jira export" in text
    assert "New dataset from JQL" not in text and "New dataset from Sprint" not in text


def captions(at):
    return " ".join(c.value for c in at.caption)


def test_unconfigured_jira_points_users_to_an_admin(workspace):
    text = captions(render())
    assert "Jira isn't configured yet" in text
    assert "Ask an admin to set up Jira" in text and "Admin Panel" in text
    assert "Config → Zephyr" not in text and "JIRA_API_TOKEN" not in text   # users can't act on those


def test_upload_hint_only_shown_while_upload_is_enabled(workspace):
    assert "upload a Jira export below" in captions(render())
    assert "upload a Jira export below" not in captions(render(["src_upload"]))


def make_dataset(workspace):
    df = pd.DataFrame({c: ["x", "y"] for c in CORE_COLUMNS})
    Store(workspace).save_dataset("demo", df, source="upload")


def test_only_enabled_views_can_be_active(workspace):
    make_dataset(workspace)
    hide_all_but_data = ["view_dashboard", "view_sprint", "view_builder", "view_explorer"]
    at = render(hide_all_but_data)
    assert at.session_state["view"] == "Data"        # default "Dashboard" is hidden → first allowed


def test_navigation_to_a_hidden_view_is_refused(workspace):
    make_dataset(workspace)
    # Dashboard / Sprint Reports jump to Report Builder through view_pending; it is hidden here.
    at = render(["view_dashboard", "view_sprint", "view_builder", "view_explorer"], pending="Report Builder")
    assert at.session_state["view"] == "Data"
