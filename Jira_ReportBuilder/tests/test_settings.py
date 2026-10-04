import json
import stat

import pytest

from jira_insights.settings import (JiraSettings, connection_file, load_connection, load_jira_settings,
                                    read_credentials_file, save_credentials_file, save_jira_settings,
                                    saved_app_settings, workspace_dir)

ENV_VARS = ("JIRA_URL", "JIRA_USERNAME", "JIRA_API_TOKEN", "JIRA_VERIFY_SSL",
            "ZEPHYR_ACCESS_KEY", "ZEPHYR_SECRET_KEY", "ZEPHYR_ACCOUNT_ID")

AMPLIFY_INI = """\
[zephyr]
jira_url    = https://acme.atlassian.net/
username    = qa@acme.com
api_token   = abc%123
access_key  = AK
secret_key  = SK
account_id  = 5b10
project_key = ABC
verify_ssl  = false
"""


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("JIRA_INSIGHTS_HOME", str(tmp_path / "ws"))
    for key in ENV_VARS:
        monkeypatch.delenv(key, raising=False)
    return tmp_path


def test_defaults_when_nothing_saved(isolated):
    conn = load_connection()
    assert workspace_dir() == isolated / "ws"
    assert conn.source == "app" and not conn.settings.configured and conn.settings.verify_ssl is True


def test_app_values_round_trip_with_owner_only_file():
    save_jira_settings(JiraSettings(" https://acme.atlassian.net/ ", " qa@acme.com ", " tok ", False,
                                    "AK", "SK", "5b10", "ABC"))
    s = load_jira_settings()
    assert (s.url, s.username, s.token, s.verify_ssl) == ("https://acme.atlassian.net", "qa@acme.com", "tok", False)
    assert s.configured and s.zephyr_configured and s.project_key == "ABC"
    assert stat.S_IMODE(connection_file().stat().st_mode) == 0o600


def test_legacy_flat_connection_file_still_loads(isolated):
    connection_file().parent.mkdir(parents=True)
    connection_file().write_text(json.dumps({"url": "https://old", "username": "u", "token": "t", "verify_ssl": True}))
    assert load_jira_settings().token == "t"


def test_amplify_style_ini_is_read_with_percent_signs_intact(isolated):
    ini = isolated / "secure" / "config.ini"
    ini.parent.mkdir()
    ini.write_text(AMPLIFY_INI)
    s, found, sections = read_credentials_file(str(ini))
    assert s == JiraSettings("https://acme.atlassian.net", "qa@acme.com", "abc%123", False, "AK", "SK", "5b10", "ABC")
    assert sections == ("zephyr",) and "token" in found and "zephyr_secret_key" in found


def test_jira_section_wins_and_aliases_and_home_paths_work(isolated, monkeypatch):
    monkeypatch.setenv("HOME", str(isolated))
    (isolated / "creds.cfg").write_text(
        "[DEFAULT]\nverify = no\n[jira]\nurl = https://jira.corp\ntoken = PAT\n"
        "[zephyr]\njira_url = https://ignored\naccess_key = AK\nsecret_key = SK\naccount_id = 7\n")
    s, _, sections = read_credentials_file("~/creds.cfg")
    assert s.url == "https://jira.corp" and s.token == "PAT" and s.username == ""   # DC PAT → Bearer
    assert s.verify_ssl is False and s.zephyr_configured
    assert set(sections) == {"jira", "zephyr", "default"}
    assert read_credentials_file("~/creds.cfg", "zephyr")[0].url == "https://ignored"


def test_amplify_config_json_zephyr_section(isolated):
    cfg = isolated / "config.json"
    cfg.write_text(json.dumps({"repo_root": "/x", "zephyr": {"jira_url": "https://acme.atlassian.net",
                                                              "username": "qa", "api_token": "t", "verify_ssl": True}}))
    s, _, sections = read_credentials_file(str(cfg))
    assert s.configured and "zephyr" in sections


@pytest.mark.parametrize("content, name, message", [
    (None, "missing.ini", "File not found"),
    ("x", "creds.txt", "Use a .ini"),
    ("[zephyr]\nproject_key = ABC\n", "empty.ini", "No Jira or Zephyr credentials"),
    ("not an ini", "broken.ini", "Could not parse"),
])
def test_unusable_credentials_files_explain_why(isolated, content, name, message):
    path = isolated / name
    if content is not None:
        path.write_text(content)
    with pytest.raises(ValueError, match=message):
        read_credentials_file(str(path))


def test_file_source_is_reread_and_keeps_typed_values(isolated):
    save_jira_settings(JiraSettings("https://typed", "me", "typed-token"))
    ini = isolated / "config.ini"
    ini.write_text(AMPLIFY_INI)
    save_credentials_file(str(ini))
    conn = load_connection()
    assert conn.source == "file" and conn.settings.token == "abc%123" and not conn.error
    assert "abc%123" not in connection_file().read_text()            # only the path is stored
    assert saved_app_settings().token == "typed-token"                # typed values kept for later

    ini.write_text(AMPLIFY_INI.replace("abc%123", "rotated"))         # edits apply without re-saving
    assert load_jira_settings().token == "rotated"
    ini.unlink()
    broken = load_connection()
    assert "File not found" in broken.error and not broken.settings.configured


def test_environment_variables_override_any_source(isolated, monkeypatch):
    ini = isolated / "config.ini"
    ini.write_text(AMPLIFY_INI)
    save_credentials_file(str(ini))
    monkeypatch.setenv("JIRA_URL", "https://env.example.com/")
    monkeypatch.setenv("ZEPHYR_ACCOUNT_ID", "env-account")
    conn = load_connection()
    assert conn.settings.url == "https://env.example.com" and conn.settings.zephyr_account_id == "env-account"
    assert conn.settings.token == "abc%123"
    assert conn.overrides == ("JIRA_URL", "ZEPHYR_ACCOUNT_ID")
