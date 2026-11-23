"""Saving the Jira / Zephyr server secrets must never lose what the form didn't resend.

The Admin Panel shows secrets masked (abc•••xyz) and can't send them back, so a save that doesn't
retype a token has to leave the saved token alone — that is the bug these tests pin down.
"""
import json

import pytest

from amplify_server import server_secrets as ss

URL = "/api/admin/server-config"
SECRETS = {"api_token": "REAL-JIRA-TOKEN-1234", "access_key": "ACCESS-KEY-ABCDEF", "secret_key": "SECRET-KEY-UVWXYZ"}


@pytest.fixture(autouse=True)
def no_env_secrets(monkeypatch):
    for name in ("JIRA_URL", "JIRA_USERNAME", "JIRA_API_TOKEN", "ZEPHYR_ACCESS_KEY", "ZEPHYR_SECRET_KEY",
                 "ZEPHYR_ACCOUNT_ID", "ZEPHYR_PROJECT_KEY"):
        monkeypatch.delenv(name, raising=False)


def saved():
    return ss._load_server_file().get("zephyr", {})


def form(**fields):
    """What admin.html posts: the plain fields, plus each secret only if it isn't still masked."""
    base = {"jira_url": "https://jira.example", "username": "me@example.com", "account_id": "", "project_key": "QA"}
    return {"zephyr": {**base, **fields}}


# ── the bug: re-saving the form ───────────────────────────────────────────────

def test_saving_without_retyping_the_secrets_keeps_them(admin):
    admin.post(URL, json=form(**SECRETS))
    assert {k: saved()[k] for k in SECRETS} == SECRETS
    admin.post(URL, json=form(project_key="NEW"))            # secrets not sent, as when they are masked
    assert {k: saved()[k] for k in SECRETS} == SECRETS and saved()["project_key"] == "NEW"


def test_the_masked_text_the_form_shows_is_never_stored(admin):
    admin.post(URL, json=form(**SECRETS))
    shown = admin.get(URL).get_json()["zephyr"]
    assert shown["api_token"] != SECRETS["api_token"] and "•" in shown["api_token"]
    admin.post(URL, json=form(**{k: shown[k] for k in SECRETS}))     # a client that echoes the masked values back
    assert {k: saved()[k] for k in SECRETS} == SECRETS


def test_a_new_secret_replaces_only_that_one(admin):
    admin.post(URL, json=form(**SECRETS))
    admin.post(URL, json=form(api_token="  BRAND-NEW-TOKEN  "))
    assert saved()["api_token"] == "BRAND-NEW-TOKEN"
    assert saved()["access_key"] == SECRETS["access_key"] and saved()["secret_key"] == SECRETS["secret_key"]


def test_emptying_a_secret_removes_it_and_only_it(admin):
    admin.post(URL, json=form(**SECRETS))
    admin.post(URL, json=form(api_token=""))
    assert "api_token" not in saved()
    assert saved()["access_key"] == SECRETS["access_key"] and saved()["secret_key"] == SECRETS["secret_key"]


def test_plain_fields_can_be_changed_and_blanked(admin):
    admin.post(URL, json=form(**SECRETS))
    admin.post(URL, json=form(jira_url="https://other.example", username="", project_key=""))
    assert saved()["jira_url"] == "https://other.example" and saved()["username"] == "" and saved()["project_key"] == ""


def test_fields_the_form_never_sends_are_kept(admin):
    ss.save_server_config({"zephyr": {"jira_url": "https://jira.example", "verify_ssl": True, "api_token": "T0KEN-XYZ"}})
    admin.post(URL, json=form())
    assert saved()["verify_ssl"] is True and saved()["api_token"] == "T0KEN-XYZ"


def test_a_secret_that_only_comes_from_the_environment_is_not_copied_into_the_file(admin, monkeypatch):
    monkeypatch.setenv("JIRA_API_TOKEN", "ENV-ONLY-TOKEN-9999")
    shown = admin.get(URL).get_json()["zephyr"]["api_token"]
    assert "•" in shown
    admin.post(URL, json=form(api_token=shown))
    assert "api_token" not in saved()
    assert ss.load_zephyr_config()["api_token"] == "ENV-ONLY-TOKEN-9999"


def test_other_server_settings_are_untouched(admin):
    ss.save_server_config({"ui_tabs": {"grid": False}, "default_tab": "dashboard",
                           "grid": {"coordinator_url": "http://c:8000", "api_token": "g-tok"}})
    admin.post(URL, json=form(**SECRETS))
    data = ss._load_server_file()
    assert data["ui_tabs"]["grid"] is False and data["default_tab"] == "dashboard"
    assert data["grid"] == {"coordinator_url": "http://c:8000", "api_token": "g-tok"}


def test_the_older_unwrapped_payload_still_works(admin):
    admin.post(URL, json={"jira_url": "https://flat.example", "api_token": "FLAT-TOKEN-1234"})
    assert saved()["jira_url"] == "https://flat.example" and saved()["api_token"] == "FLAT-TOKEN-1234"


# ── boundaries ────────────────────────────────────────────────────────────────

def test_only_admins_can_save(admin, user, app):
    assert user.post(URL, json=form(**SECRETS)).status_code == 403
    assert app.test_client().post(URL, json=form(**SECRETS)).status_code == 401
    assert saved() == {}


@pytest.mark.parametrize("body", [{"zephyr": "x"}, {"zephyr": ["a"]}, {"zephyr": 5}])
def test_a_payload_that_is_not_an_object_is_a_400_and_changes_nothing(admin, body):
    admin.post(URL, json=form(**SECRETS))
    assert admin.post(URL, json=body).status_code == 400
    assert {k: saved()[k] for k in SECRETS} == SECRETS


def test_the_audit_log_never_carries_a_secret(admin):
    admin.post(URL, json=form(**SECRETS))
    log = json.dumps(admin.get("/api/admin/activity").get_json())
    assert all(secret not in log for secret in SECRETS.values())
