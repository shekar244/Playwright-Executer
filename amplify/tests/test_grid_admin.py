"""Admin-configurable Grid coordinator: set the URL and token in the Admin Panel, not only by environment.

The proxy is exercised end to end against a real local HTTP server that plays the coordinator and
insists on the X-API-Token header, so a wrong URL, a missing token or a leaked secret all show up.
"""
import importlib
import json
import threading
from types import SimpleNamespace

import pytest
from flask import Flask, jsonify, request
from werkzeug.serving import make_server

from amplify import server_secrets as ss

TOKEN = "s3cret-token-value"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("GRID_COORDINATOR_URL", raising=False)
    monkeypatch.delenv("GRID_API_TOKEN", raising=False)


@pytest.fixture
def app(data_dir):
    flask_app = Flask(__name__)
    for name in ("auth", "admin", "config", "grid"):
        flask_app.register_blueprint(importlib.import_module(f"amplify.routes.{name}").bp)
    return flask_app


@pytest.fixture
def coordinator():
    fake = Flask("fake-coordinator")
    calls = []

    @fake.route("/api/config")
    def config():
        calls.append(request.headers.get("X-API-Token"))
        if request.headers.get("X-API-Token") != TOKEN:
            return jsonify({"detail": "Missing or invalid X-API-Token"}), 401
        return jsonify({"environments": ["qa", "stage"], "browsers": ["chromium", "firefox", "edge"],
                        "default_environment": "qa", "default_browser": "chromium", "default_chunk_size": 3})

    server = make_server("127.0.0.1", 0, fake, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield SimpleNamespace(url=f"http://127.0.0.1:{server.server_port}", calls=calls)
    server.shutdown()
    server.server_close()


def save(admin, **body):
    return admin.post("/api/admin/grid", json=body)


# ── resolving the settings ────────────────────────────────────────────────────

def test_nothing_is_configured_by_default(data_dir):
    assert ss.grid_config_summary() == {"coordinator_url": "", "url_source": "none",
                                        "has_token": False, "token_source": "none"}
    assert ss.load_grid_config() == {"coordinator_url": "", "api_token": ""}


def test_environment_variables_are_the_defaults(data_dir, monkeypatch):
    monkeypatch.setenv("GRID_COORDINATOR_URL", "http://env-coordinator:8000/")
    monkeypatch.setenv("GRID_API_TOKEN", "env-token")
    assert ss.load_grid_config() == {"coordinator_url": "http://env-coordinator:8000", "api_token": "env-token"}
    summary = ss.grid_config_summary()
    assert summary["url_source"] == "env" and summary["token_source"] == "env" and summary["has_token"]


def test_a_saved_value_beats_the_environment_and_clearing_it_goes_back(data_dir, monkeypatch):
    monkeypatch.setenv("GRID_COORDINATOR_URL", "http://env-coordinator:8000")
    monkeypatch.setenv("GRID_API_TOKEN", "env-token")
    ss.save_grid_config("https://saved.example.com", "saved-token")
    assert ss.load_grid_config() == {"coordinator_url": "https://saved.example.com", "api_token": "saved-token"}
    assert ss.grid_config_summary()["url_source"] == "saved"
    ss.save_grid_config("", clear_token=True)
    assert ss.load_grid_config() == {"coordinator_url": "http://env-coordinator:8000", "api_token": "env-token"}


def test_the_url_is_tidied(data_dir):
    ss.save_grid_config("  https://grid.example.com:8000/base/  ")
    assert ss.load_grid_config()["coordinator_url"] == "https://grid.example.com:8000/base"


def test_a_blank_token_keeps_the_saved_one_and_none_leaves_the_url_alone(data_dir):
    ss.save_grid_config("http://c:8000", "first")
    ss.save_grid_config(None, "")
    ss.save_grid_config("http://c:8000", None)
    assert ss.load_grid_config() == {"coordinator_url": "http://c:8000", "api_token": "first"}
    ss.save_grid_config(None, "second")
    assert ss.load_grid_config()["api_token"] == "second" and ss.load_grid_config()["coordinator_url"] == "http://c:8000"


def test_removing_the_token_keeps_the_url(data_dir):
    ss.save_grid_config("http://c:8000", "tok")
    ss.save_grid_config(None, "ignored when clearing", clear_token=True)
    assert ss.load_grid_config() == {"coordinator_url": "http://c:8000", "api_token": ""}


@pytest.mark.parametrize("bad", [
    "ftp://host", "http://", "javascript:alert(1)", "file:///etc/passwd", "host:8000", "http://user:pw@host",
    "http://host?x=1", "http://host#frag", "http://ho st", "http://host:99999", "not a url"])
def test_bad_urls_are_refused_and_change_nothing(data_dir, bad):
    ss.save_grid_config("http://good:8000", "tok")
    before = json.dumps(ss._load_server_file(), sort_keys=True)
    with pytest.raises(ValueError):
        ss.save_grid_config(bad)
    assert json.dumps(ss._load_server_file(), sort_keys=True) == before


@pytest.mark.parametrize("bad", ["line\nbreak", "tab\there", "x" * 513, "nul\x00"])
def test_tokens_that_cannot_be_sent_in_a_header_are_refused(data_dir, bad):
    with pytest.raises(ValueError, match="header"):
        ss.save_grid_config("http://c:8000", bad)
    assert ss.load_grid_config()["api_token"] == ""


def test_saving_grid_settings_keeps_everything_else(data_dir):
    ss.save_server_config({"zephyr": {"jira_url": "https://jira.example"}, "ui_tabs": {"grid": False}})
    ss.save_grid_config("http://c:8000", "tok")
    data = ss._load_server_file()
    assert data["zephyr"]["jira_url"] == "https://jira.example" and data["ui_tabs"]["grid"] is False


def test_a_corrupt_saved_section_is_ignored(data_dir, monkeypatch):
    ss.save_server_config({"grid": "nonsense"})
    assert ss.load_grid_config() == {"coordinator_url": "", "api_token": ""}
    ss.save_server_config({"grid": {"coordinator_url": ["x"], "api_token": 5}})
    assert ss.load_grid_config() == {"coordinator_url": "", "api_token": ""}


# ── the admin endpoint ────────────────────────────────────────────────────────

def test_only_admins_can_read_or_change_it(admin, user, app):
    anon = app.test_client()
    for client, status in ((user, 403), (anon, 401)):
        assert client.get("/api/admin/grid").status_code == status
        assert save(client, coordinator_url="http://c:8000").status_code == status
    assert admin.get("/api/admin/grid").status_code == 200
    assert ss.load_grid_config()["coordinator_url"] == ""


def test_the_token_is_write_only(admin, coordinator):
    r = save(admin, coordinator_url=coordinator.url, api_token=TOKEN)
    assert r.status_code == 200 and r.get_json()["has_token"] is True
    for response in (r, admin.get("/api/admin/grid")):
        assert TOKEN not in response.get_data(as_text=True)
    assert admin.get("/api/admin/grid").get_json() == {
        "coordinator_url": coordinator.url, "url_source": "saved", "has_token": True, "token_source": "saved"}


def test_saving_checks_the_connection_and_says_what_is_on_offer(admin, coordinator):
    status = save(admin, coordinator_url=coordinator.url, api_token=TOKEN).get_json()["status"]
    assert status == {"configured": True, "reachable": True, "url": coordinator.url, "error": None,
                      "environments": 2, "browsers": 3}


def test_a_wrong_token_is_reported_not_hidden(admin, coordinator):
    r = save(admin, coordinator_url=coordinator.url, api_token="wrong")
    assert r.status_code == 200                                  # it was saved…
    status = r.get_json()["status"]
    assert status["reachable"] is False and "X-API-Token" in status["error"]   # …and the admin is told


def test_an_unreachable_coordinator_is_reported(admin):
    status = save(admin, coordinator_url="http://127.0.0.1:1", api_token="t").get_json()["status"]
    assert status["configured"] is True and status["reachable"] is False and "unreachable" in status["error"]


def test_clearing_everything_reports_not_configured(admin, coordinator):
    save(admin, coordinator_url=coordinator.url, api_token=TOKEN)
    r = save(admin, coordinator_url="", clear_token=True)
    assert r.get_json()["status"] == {"configured": False, "reachable": False}
    assert admin.get("/api/admin/grid").get_json()["has_token"] is False


@pytest.mark.parametrize("body", [{"coordinator_url": 5}, {"api_token": ["x"]}, {"clear_token": "yes"},
                                  {"coordinator_url": "ftp://x"}, {"coordinator_url": "http://u:p@h"}])
def test_bad_requests_get_400_and_change_nothing(admin, body):
    assert admin.post("/api/admin/grid", json=body).status_code == 400
    assert ss.load_grid_config() == {"coordinator_url": "", "api_token": ""}


def test_changes_are_audit_logged_without_the_secret(admin, coordinator):
    save(admin, coordinator_url=coordinator.url, api_token=TOKEN)
    save(admin, clear_token=True)
    log = admin.get("/api/admin/activity").get_json()
    details = [a["detail"] for a in log if a["action"] == "admin_update_grid"]
    assert any("token replaced" in d for d in details) and any("token removed" in d for d in details)
    assert all(TOKEN not in d for d in details) and TOKEN not in json.dumps(log)


# ── the Grid tab uses what the admin saved ────────────────────────────────────

def test_users_get_environments_and_browsers_once_an_admin_has_set_it_up(admin, user, coordinator):
    before = user.get("/api/grid/config")
    assert before.status_code == 503 and "Admin Panel" in before.get_json()["error"]
    assert user.get("/api/grid/status").get_json() == {"configured": False, "reachable": False}

    save(admin, coordinator_url=coordinator.url, api_token=TOKEN)

    cfg = user.get("/api/grid/config")
    assert cfg.status_code == 200
    assert cfg.get_json()["environments"] == ["qa", "stage"] and cfg.get_json()["browsers"] == ["chromium", "firefox", "edge"]
    assert coordinator.calls[-1] == TOKEN                         # the saved token really was sent
    assert user.get("/api/grid/status").get_json()["reachable"] is True


def test_a_wrong_saved_token_never_leaks_a_401_to_users(admin, user, coordinator):
    save(admin, coordinator_url=coordinator.url, api_token="wrong")
    assert user.get("/api/grid/config").status_code == 502


def test_removing_the_setup_takes_the_grid_away_again(admin, user, coordinator):
    save(admin, coordinator_url=coordinator.url, api_token=TOKEN)
    save(admin, coordinator_url="", clear_token=True)
    assert user.get("/api/grid/config").status_code == 503


def test_the_environment_variable_still_works_when_nothing_is_saved(user, coordinator, monkeypatch):
    monkeypatch.setenv("GRID_COORDINATOR_URL", coordinator.url)
    monkeypatch.setenv("GRID_API_TOKEN", TOKEN)
    assert user.get("/api/grid/config").status_code == 200
