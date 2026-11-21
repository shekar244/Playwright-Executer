"""The File Manager is for admins only — endpoints, page and landing tab.

Its endpoints take absolute paths anywhere on the server, so before this a regular user could read the
Jira / Zephyr / Grid tokens in server_config.json (and overwrite JSON/YAML/.env files). These tests run
against the real app, so they cover the page a user is actually sent as well as the API.
"""
import json

import pytest

from amplify import server_secrets as ss


@pytest.fixture
def app(data_dir):
    from amplify.server import app as real_app
    return real_app


@pytest.fixture
def workdir(tmp_path):
    (tmp_path / "notes.json").write_text('{"hello": "world"}')
    return tmp_path


FM = [("get", "/api/fm/browse", {}), ("get", "/api/fm/read", {}), ("post", "/api/fm/save", {"path": "x.json", "content": "{}"})]


def call(client, method, url, body, path):
    if method == "post":
        return client.post(url, json={**body, "path": path})
    return client.get(url, query_string={"path": path})


# ── endpoints ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("method, url, body", FM)
def test_a_regular_user_gets_403_from_every_file_manager_endpoint(user, workdir, method, url, body):
    path = str(workdir / ("notes.json" if url != "/api/fm/browse" else ""))
    r = call(user, method, url, body, path)
    assert r.status_code == 403 and r.get_json()["error"] == "Admin access required"


@pytest.mark.parametrize("method, url, body", FM)
def test_anonymous_requests_are_refused(app, workdir, method, url, body):
    r = call(app.test_client(), method, url, body, str(workdir / "notes.json"))
    assert r.status_code in (401, 302)


def test_admins_can_still_browse_read_and_save(admin, workdir):
    assert any(e["name"] == "notes.json" for e in admin.get("/api/fm/browse", query_string={"path": str(workdir)}).get_json()["entries"])
    assert admin.get("/api/fm/read", query_string={"path": str(workdir / "notes.json")}).get_json()["parsed"] == {"hello": "world"}
    r = admin.post("/api/fm/save", json={"path": str(workdir / "notes.json"), "content": '{"hello": "admin"}'})
    assert r.status_code == 200 and json.loads((workdir / "notes.json").read_text()) == {"hello": "admin"}


def test_a_regular_user_cannot_read_the_servers_secrets(admin, user):
    ss.save_server_config({"zephyr": {"api_token": "SECRET-JIRA-TOKEN-XYZ"}, "grid": {"api_token": "SECRET-GRID-TOKEN"}})
    path = str(ss._server_config_path())
    denied = user.get("/api/fm/read", query_string={"path": path})
    assert denied.status_code == 403 and b"SECRET" not in denied.data
    assert b"SECRET-JIRA-TOKEN-XYZ" in admin.get("/api/fm/read", query_string={"path": path}).data   # admins may


def test_a_regular_user_cannot_overwrite_files_either(admin, user, workdir):
    r = user.post("/api/fm/save", json={"path": str(workdir / "notes.json"), "content": '{"hello": "pwned"}'})
    assert r.status_code == 403 and json.loads((workdir / "notes.json").read_text()) == {"hello": "world"}


# ── the page ──────────────────────────────────────────────────────────────────

def test_admins_get_the_file_manager_tab_and_page(admin):
    page = admin.get("/").get_data(as_text=True)
    assert 'id="navbtn-filemanager"' in page and 'id="page-filemanager"' in page


def test_regular_users_get_neither_the_tab_nor_the_page(admin, user):
    page = user.get("/").get_data(as_text=True)
    assert 'id="navbtn-filemanager"' not in page and 'id="page-filemanager"' not in page
    assert "/api/fm/" not in page                                  # not even the script that calls it
    assert 'id="navbtn-executor"' in page and 'id="navbtn-dashboard"' in page      # everything else is unchanged


def test_switching_to_a_tab_that_is_not_there_is_ignored_by_the_page_script(user):
    page = user.get("/").get_data(as_text=True)
    assert "if (!btn || btn.style.display === 'none') return;" in page


# ── landing tab and visibility rules ──────────────────────────────────────────

def test_the_file_manager_is_marked_admins_only_in_the_admin_panel(admin):
    assert "filemanager" in ss.ADMIN_ONLY_TABS
    labels = {t["key"]: t["label"] for t in admin.get("/api/admin/ui-tabs").get_json()["catalog"]}
    assert "admins only" in labels["filemanager"].lower()


def test_an_admin_can_land_on_the_file_manager_but_regular_users_fall_back(admin, user):
    assert admin.post("/api/admin/ui-tabs", json={"ui_tabs": {}, "default_tab": "filemanager"}).status_code == 200
    assert admin.get("/api/config").get_json()["default_tab"] == "filemanager"
    assert user.get("/api/config").get_json()["default_tab"] == "executor"       # the first tab they can open


def test_the_fallback_skips_hidden_tabs_too(admin, user):
    admin.post("/api/admin/ui-tabs", json={"ui_tabs": {"executor": False}, "default_tab": "filemanager"})
    assert user.get("/api/config").get_json()["default_tab"] == "repository"


def test_an_admin_cannot_leave_regular_users_with_no_tab(admin):
    only_fm = {k: False for k in ("executor", "repository", "dashboard", "grid", "zephyr", "testdata", "insights", "tools")}
    r = admin.post("/api/admin/ui-tabs", json={"ui_tabs": {**only_fm, "filemanager": True}})
    assert r.status_code == 400 and "regular users" in r.get_json()["error"]
    assert ss.load_ui_tabs()["executor"] is True                                  # nothing was saved
    ok = admin.post("/api/admin/ui-tabs", json={"ui_tabs": {**only_fm, "dashboard": True, "filemanager": True}})
    assert ok.status_code == 200


def test_hiding_the_file_manager_from_the_tab_list_still_works(admin):
    admin.post("/api/admin/ui-tabs", json={"ui_tabs": {"filemanager": False}})
    assert admin.get("/api/config").get_json()["ui_tabs"]["filemanager"] is False
    assert ss.load_default_tab(is_admin=True) == "executor"
