"""Who gets into Jira Insights: only a signed QEA hand-off or the sign-in form — never the URL.

Renders the real app.py with Streamlit's AppTest. Needs the Insights dependencies; skipped without them.
"""
import time
from pathlib import Path

import jwt
import pytest

pytest.importorskip("plotly")
pytest.importorskip("pygwalker")
import streamlit as st  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402

from amplify_server import sso  # noqa: E402
from amplify_server.auth_utils import generate_jwt  # noqa: E402
from amplify_server.config import settings  # noqa: E402
from jira_insights import settings as insights_settings  # noqa: E402

APP = Path(__file__).resolve().parents[3] / "src" / "jira_insights" / "app.py"
SIDEBAR_MARKER = "New dataset from JQL"       # an expander that exists only for signed-in users


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATABASE_PATH", tmp_path / "amplify_server.db")
    monkeypatch.setattr(settings, "WORKSPACES_ROOT", tmp_path / "workspaces")
    monkeypatch.delenv("JIRA_INSIGHTS_HOME", raising=False)       # exercise the real per-user workspaces
    monkeypatch.delenv("USER_ID", raising=False)
    monkeypatch.delenv("QEA_API_URL", raising=False)
    sso._used.clear()
    yield tmp_path
    sso._used.clear()


def run(**params):
    at = AppTest.from_file(str(APP), default_timeout=60)
    for key, value in params.items():
        at.query_params[key] = value
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def signed_in(at):
    return "ji_auth_user_id" in at.session_state


def shows_sign_in_form(at):
    return any(t.label == "Username" for t in at.text_input)


def shows_workspace(at):
    return any(SIDEBAR_MARKER in e.label for e in at.expander)


def info_text(at):
    return " ".join(i.value for i in at.info)


# ── nothing in the URL grants access ──────────────────────────────────────────

@pytest.mark.parametrize("params", [
    {},
    {"user_id": "1"},
    {"embed": "true"},
    {"embed": "true", "user_id": "1"},
    {"user_id": "1", "theme": "dark"},
])
def test_unsigned_urls_get_the_sign_in_form(params):
    at = run(**params)
    assert shows_sign_in_form(at) and not signed_in(at) and not shows_workspace(at)


def test_the_form_is_shown_inside_the_embedded_view_too():
    at = run(embed="true", user_id="2")
    assert shows_sign_in_form(at) and not shows_workspace(at)


# ── a signed token signs you in ───────────────────────────────────────────────

def test_a_valid_token_signs_in_without_a_form():
    at = run(sso=sso.issue_insights_token(5, "sam"))
    assert signed_in(at) and not shows_sign_in_form(at) and shows_workspace(at)
    assert at.session_state["ji_auth_user_id"] == 5 and at.session_state["ji_auth_username"] == "sam"


def test_the_token_is_removed_from_the_url_after_use():
    at = run(sso=sso.issue_insights_token(5, "sam"))
    assert "sso" not in at.query_params


def test_works_embedded_and_standalone_with_the_right_chrome():
    embedded = run(sso=sso.issue_insights_token(5, "sam", embedded=True), embed="true")
    assert shows_workspace(embedded) and not any(b.label == "Logout" for b in embedded.button)   # QEA owns the session
    standalone = run(sso=sso.issue_insights_token(5, "sam"))
    assert any(b.label == "Logout" for b in standalone.button)
    assert any("sam" in c.value for c in standalone.caption)


def test_a_token_works_once():
    token = sso.issue_insights_token(5, "sam")
    assert signed_in(run(sso=token))
    replay = run(sso=token)
    assert not signed_in(replay) and shows_sign_in_form(replay)
    assert "expired or was already used" in info_text(replay)


def test_an_expired_token_gets_the_form_and_an_explanation():
    now = int(time.time())
    stale = jwt.encode({"sub": "5", "name": "sam", "aud": sso.AUDIENCE, "iat": now - 600, "exp": now - 300,
                        "jti": "old"}, settings.JWT_SECRET_KEY, algorithm="HS256")
    at = run(sso=stale)
    assert not signed_in(at) and shows_sign_in_form(at) and "expired or was already used" in info_text(at)


def test_a_token_signed_with_another_key_is_refused():
    now = int(time.time())
    forged = jwt.encode({"sub": "1", "name": "boss", "aud": sso.AUDIENCE, "iat": now, "exp": now + 60,
                         "jti": "f"}, "y" * 32, algorithm="HS256")
    assert not signed_in(run(sso=forged))


def test_a_qea_session_token_is_not_accepted_here():
    assert not signed_in(run(sso=generate_jwt(1)))


def test_garbage_in_the_sso_parameter_is_just_refused():
    at = run(sso="not-a-token")
    assert not signed_in(at) and shows_sign_in_form(at)


# ── the workspace is the signed-in user's, whatever else the URL says ─────────

def test_a_url_user_id_cannot_redirect_a_signed_in_user_to_someone_elses_workspace(isolated):
    run(sso=sso.issue_insights_token(1, "one"), user_id="2")
    workspaces = isolated / "workspaces"
    assert (workspaces / "1" / "jira_insights" / "datasets").is_dir()
    assert not (workspaces / "2").exists()


def test_workspace_dir_follows_the_session_never_the_url(monkeypatch, isolated):
    monkeypatch.setattr(st, "session_state", {"ji_auth_user_id": 7})
    monkeypatch.setattr(st, "query_params", {"user_id": "9"})
    assert insights_settings.workspace_dir() == isolated / "workspaces" / "7" / "jira_insights"


def test_workspace_dir_without_a_signed_in_user_never_reads_the_url(monkeypatch):
    monkeypatch.setattr(st, "session_state", {})
    monkeypatch.setattr(st, "query_params", {"user_id": "9"})
    assert "9" not in insights_settings.workspace_dir().parts


def test_standalone_workspace_is_outside_the_source_package(monkeypatch):
    monkeypatch.delenv("JIRA_INSIGHTS_HOME", raising=False)
    monkeypatch.delenv("CONTAINER", raising=False)
    workspace = insights_settings._standalone_workspace()
    assert workspace == Path.home() / ".amplify-qea" / "jira_insights"
    assert "src" not in workspace.parts
