"""Admin-controlled visibility: main-app tabs and Jira Insights options.

Both are server-wide {key: bool} maps in server_config.json. Admins change them; every
user receives them. These tests use a throwaway data dir and a minimal Flask app so they
touch neither real user data nor unrelated blueprints.
"""
import json
import threading

import pytest
from werkzeug.serving import make_server

from amplify_server import server_secrets as ss
from jira_insights import options as insights_options

ALL_TABS = [e["key"] for e in ss.UI_TAB_CATALOG]
ALL_INSIGHTS = [e["key"] for e in ss.INSIGHTS_OPTION_CATALOG]
CFG_TABS = [k for k in ALL_TABS if k.startswith("cfg_")]
MAIN_TABS = [k for k in ALL_TABS if not k.startswith("cfg_")]
SOURCES = [k for k in ALL_INSIGHTS if k.startswith("src_")]
VIEWS = [k for k in ALL_INSIGHTS if k.startswith("view_")]


# ── catalogs ──────────────────────────────────────────────────────────────────

def test_catalogs_cover_every_tab_and_option():
    assert {"grid", "tools", "insights", "testdata"} <= set(ALL_TABS)
    assert set(CFG_TABS) == {"cfg_features", "cfg_repo", "cfg_git", "cfg_tools",
                             "cfg_allure", "cfg_jfrog", "cfg_mapping", "cfg_zephyr"}
    assert len(SOURCES) == 4 and len(VIEWS) == 5 and {"excluded_fields", "colour_theme"} <= set(ALL_INSIGHTS)


def test_defaults(data_dir):
    tabs = ss.load_ui_tabs()
    assert tabs["insights"] is False and all(v for k, v in tabs.items() if k != "insights")
    assert all(ss.load_insights_options().values())


# ── validation (shared rules, each enforced server-side) ─────────────────────

@pytest.mark.parametrize("save, bad, message", [
    (ss.save_ui_tabs, {"nope": True}, "Unknown tab"),
    (ss.save_ui_tabs, {"grid": "false"}, "true or false"),
    (ss.save_ui_tabs, {k: False for k in MAIN_TABS}, "main tab"),
    (ss.save_ui_tabs, {k: False for k in CFG_TABS}, "hide Config instead"),
    (ss.save_insights_options, {"nope": True}, "Unknown option"),
    (ss.save_insights_options, {"colour_theme": 0}, "true or false"),
    (ss.save_insights_options, {k: False for k in SOURCES}, "create a dataset"),
    (ss.save_insights_options, {k: False for k in VIEWS}, "one view"),
])
def test_invalid_saves_are_rejected_and_change_nothing(data_dir, save, bad, message):
    before = json.dumps(ss._load_server_file(), sort_keys=True)
    with pytest.raises(ValueError, match=message):
        save(bad)
    assert json.dumps(ss._load_server_file(), sort_keys=True) == before


def test_hiding_config_with_its_subtabs_is_allowed(data_dir):
    assert ss.save_ui_tabs({"tools": False})["tools"] is False


def test_partial_updates_merge_and_persist_independently(data_dir):
    ss.save_ui_tabs({"grid": False})
    ss.save_insights_options({"colour_theme": False})
    assert ss.load_ui_tabs()["grid"] is False and ss.load_ui_tabs()["dashboard"] is True
    assert ss.load_insights_options()["colour_theme"] is False
    on_disk = ss._load_server_file()
    assert on_disk["ui_tabs"]["grid"] is False and on_disk["insights_options"]["colour_theme"] is False


def test_saving_flags_does_not_clobber_jira_secrets(data_dir):
    ss.save_server_config({"zephyr": {"jira_url": "https://jira.example", "api_token": "tok"}})
    ss.save_ui_tabs({"grid": False})
    ss.save_insights_options({"src_jql": False})
    assert ss._load_server_file()["zephyr"]["api_token"] == "tok"


def test_corrupt_or_foreign_saved_values_fall_back_to_defaults(data_dir):
    ss.save_server_config({"ui_tabs": {"grid": "yes", "bogus": True}, "insights_options": "garbage"})
    assert ss.load_ui_tabs()["grid"] is True and "bogus" not in ss.load_ui_tabs()
    assert ss.load_insights_options() == {k: True for k in ALL_INSIGHTS}


# ── endpoints ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("url, body_key, hide", [
    ("/api/admin/ui-tabs", "ui_tabs", "grid"),
    ("/api/admin/insights-options", "insights_options", "colour_theme"),
])
def test_only_admins_can_read_or_change(admin, user, app, url, body_key, hide):
    anon = app.test_client()
    for client, status in ((user, 403), (anon, 401)):
        assert client.get(url).status_code == status
        assert client.post(url, json={body_key: {hide: False}}).status_code == status
    assert admin.get(url).status_code == 200


def test_admin_roundtrip_and_audit_log(admin):
    r = admin.post("/api/admin/ui-tabs", json={"ui_tabs": {"grid": False}})
    assert r.status_code == 200 and r.get_json()["ui_tabs"]["grid"] is False
    got = admin.get("/api/admin/ui-tabs").get_json()
    assert got["ui_tabs"]["grid"] is False and len(got["catalog"]) == len(ALL_TABS)

    admin.post("/api/admin/insights-options", json={"insights_options": {"src_jql": False}})
    got = admin.get("/api/admin/insights-options").get_json()
    assert got["insights_options"]["src_jql"] is False and len(got["catalog"]) == len(ALL_INSIGHTS)

    actions = {a["action"]: a["detail"] for a in admin.get("/api/admin/activity").get_json()}
    assert "grid" in actions["admin_update_ui_tabs"] and "src_jql" in actions["admin_update_insights_options"]


@pytest.mark.parametrize("url, body_key", [("/api/admin/ui-tabs", "ui_tabs"),
                                           ("/api/admin/insights-options", "insights_options")])
def test_bad_requests_get_400(admin, url, body_key):
    assert admin.post(url, json={body_key: "x"}).status_code == 400
    assert admin.post(url, json={}).status_code == 400
    assert admin.post(url, json={body_key: {"nope": True}}).status_code == 400


def test_every_user_receives_the_admins_tab_choice_via_config(admin, user):
    admin.post("/api/admin/ui-tabs", json={"ui_tabs": {"grid": False, "cfg_jfrog": False}})
    for client in (admin, user):
        tabs = client.get("/api/config").get_json()["ui_tabs"]
        assert tabs["grid"] is False and tabs["cfg_jfrog"] is False and tabs["executor"] is True


def test_a_users_own_config_file_cannot_override_the_server(admin, user, data_dir):
    admin.post("/api/admin/ui-tabs", json={"ui_tabs": {"grid": False}})
    for path in (data_dir / "workspaces").glob("*/config/config.json"):
        cfg = json.loads(path.read_text())
        cfg["ui_tabs"] = {"grid": True}
        path.write_text(json.dumps(cfg))
    assert user.get("/api/config").get_json()["ui_tabs"]["grid"] is False


# ── landing tab ───────────────────────────────────────────────────────────────

def test_everyone_lands_on_executor_until_an_admin_chooses(admin, user):
    assert ss.load_default_tab() == "executor"
    for client in (admin, user):
        assert client.get("/api/config").get_json()["default_tab"] == "executor"
    assert admin.get("/api/admin/ui-tabs").get_json()["default_tab"] == "executor"


def test_the_admins_choice_reaches_every_user(admin, user):
    r = admin.post("/api/admin/ui-tabs", json={"ui_tabs": {}, "default_tab": "dashboard"})
    assert r.status_code == 200 and r.get_json()["default_tab"] == "dashboard"
    assert admin.get("/api/admin/ui-tabs").get_json()["default_tab"] == "dashboard"
    for client in (admin, user):
        assert client.get("/api/config").get_json()["default_tab"] == "dashboard"


def test_the_choice_survives_a_restart(admin, data_dir):
    admin.post("/api/admin/ui-tabs", json={"ui_tabs": {}, "default_tab": "filemanager"})
    assert json.loads((data_dir / "server_config.json").read_text())["default_tab"] == "filemanager"
    assert ss.load_default_tab() == "filemanager"


def test_saving_visibility_alone_keeps_the_landing_tab(admin):
    admin.post("/api/admin/ui-tabs", json={"ui_tabs": {}, "default_tab": "dashboard"})
    admin.post("/api/admin/ui-tabs", json={"ui_tabs": {"grid": False}})
    assert ss.load_default_tab() == "dashboard"


@pytest.mark.parametrize("bad, message", [
    ("cfg_repo",   "not a main tab"),          # a Config sub-tab is not somewhere to land
    ("nope",       "not a main tab"),
    (7,            "not a main tab"),
    ("insights",   "hidden"),                  # Jira Insights is off by default
])
def test_only_a_visible_main_tab_can_be_the_landing_tab(admin, bad, message):
    r = admin.post("/api/admin/ui-tabs", json={"ui_tabs": {}, "default_tab": bad})
    assert r.status_code == 400 and message in r.get_json()["error"]
    assert ss.load_default_tab() == "executor"


def test_hiding_and_choosing_the_same_tab_in_one_save_is_refused_and_saves_nothing(admin):
    r = admin.post("/api/admin/ui-tabs", json={"ui_tabs": {"dashboard": False}, "default_tab": "dashboard"})
    assert r.status_code == 400 and "Reporting Dashboard" in r.get_json()["error"]
    assert ss.load_ui_tabs()["dashboard"] is True and ss.load_default_tab() == "executor"


def test_a_tab_hidden_later_falls_back_and_returns_when_shown_again(admin):
    admin.post("/api/admin/ui-tabs", json={"ui_tabs": {}, "default_tab": "dashboard"})
    admin.post("/api/admin/ui-tabs", json={"ui_tabs": {"dashboard": False}})
    assert ss.load_default_tab() == "executor"
    assert admin.get("/api/config").get_json()["default_tab"] == "executor"
    admin.post("/api/admin/ui-tabs", json={"ui_tabs": {"dashboard": True}})
    assert ss.load_default_tab() == "dashboard"                 # still their choice, not forgotten


def test_with_executor_hidden_the_fallback_is_the_first_visible_tab(admin):
    admin.post("/api/admin/ui-tabs", json={"ui_tabs": {"executor": False}})
    assert ss.load_default_tab() == "repository"            # hide the Executor: users land on the Repository tab
    admin.post("/api/admin/ui-tabs", json={"ui_tabs": {"executor": False, "repository": False, "dashboard": False}})
    assert ss.load_default_tab() == "grid"


def test_a_corrupt_saved_landing_tab_is_ignored(data_dir):
    ss.save_server_config({"default_tab": ["not", "a", "string"]})
    assert ss.load_default_tab() == "executor"
    ss.save_server_config({"default_tab": "cfg_repo"})
    assert ss.load_default_tab() == "executor"


def test_only_admins_can_set_the_landing_tab(user, app, admin):
    assert user.post("/api/admin/ui-tabs", json={"ui_tabs": {}, "default_tab": "dashboard"}).status_code == 403
    assert app.test_client().post("/api/admin/ui-tabs", json={"ui_tabs": {}, "default_tab": "dashboard"}).status_code == 401
    assert ss.load_default_tab() == "executor"


def test_the_landing_tab_is_in_the_audit_log(admin):
    admin.post("/api/admin/ui-tabs", json={"ui_tabs": {}, "default_tab": "dashboard"})
    actions = {a["action"]: a["detail"] for a in admin.get("/api/admin/activity").get_json()}
    assert "landing tab: dashboard" in actions["admin_update_ui_tabs"]


def test_saving_the_landing_tab_does_not_clobber_jira_secrets(admin):
    ss.save_server_config({"zephyr": {"jira_url": "https://jira.example"}})
    admin.post("/api/admin/ui-tabs", json={"ui_tabs": {}, "default_tab": "dashboard"})
    assert ss.load_zephyr_config()["jira_url"] == "https://jira.example"


def test_the_old_per_user_endpoint_is_gone(user):
    assert user.post("/api/config/ui-tabs", json={"ui_tabs": {"grid": False}}).status_code in (404, 405)


def test_public_insights_options_expose_only_booleans(app, admin):
    ss.save_server_config({"zephyr": {"api_token": "SECRET-TOKEN"}})
    admin.post("/api/admin/insights-options", json={"insights_options": {"src_zephyr": False}})
    r = app.test_client().get("/api/insights/options")          # no login
    assert r.status_code == 200
    body = r.get_json()
    assert body["src_zephyr"] is False and set(body) == set(ALL_INSIGHTS)
    assert all(isinstance(v, bool) for v in body.values())
    assert "SECRET-TOKEN" not in r.get_data(as_text=True)


# ── Insights-side resolution ──────────────────────────────────────────────────

def test_allowed_views_and_pick_view():
    views = insights_options.VIEW_KEYS
    opts = {k: True for k in ALL_INSIGHTS}
    assert insights_options.allowed_views(views, opts) == list(views)

    opts.update(view_dashboard=False, view_data=False)
    allowed = insights_options.allowed_views(views, opts)
    assert allowed == ["Sprint Reports", "Report Builder", "Explorer"]
    assert insights_options.pick_view(None, "Dashboard", allowed) == "Sprint Reports"     # current is hidden
    assert insights_options.pick_view("Report Builder", "Explorer", allowed) == "Report Builder"
    assert insights_options.pick_view("Dashboard", "Explorer", allowed) == "Explorer"     # requested is hidden → stay
    assert insights_options.allowed_views(views, {k: False for k in ALL_INSIGHTS}) == list(views)  # never empty


def test_get_options_reads_the_local_file_and_caches(data_dir, monkeypatch):
    assert insights_options.get_options()["colour_theme"] is True
    ss.save_insights_options({"colour_theme": False})
    assert insights_options.get_options()["colour_theme"] is True        # still cached
    monkeypatch.setattr(insights_options, "CACHE_SECONDS", 0)
    assert insights_options.get_options()["colour_theme"] is False       # refreshed


@pytest.fixture
def qea_server(app, admin):
    """The QEA app on a real local port, for the HTTP path used when Insights has its own disk."""
    server = make_server("127.0.0.1", 0, app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", admin, server
    server.shutdown()
    thread.join(timeout=5)


def test_get_options_over_http_and_keeps_last_good_when_qea_is_down(qea_server, monkeypatch):
    url, admin, server = qea_server
    admin.post("/api/admin/insights-options", json={"insights_options": {"src_sprint": False}})

    # Model an Insights service with its OWN disk: its local reader knows nothing about the
    # admin's choice, so a correct answer can only have come over HTTP / from the cache.
    monkeypatch.setattr(insights_options, "load_insights_options", lambda: {k: True for k in ALL_INSIGHTS})
    monkeypatch.setenv("QEA_API_URL", url)
    monkeypatch.setattr(insights_options, "CACHE_SECONDS", 0)       # re-resolve on every call

    assert insights_options.get_options()["src_sprint"] is False    # fetched from QEA

    server.shutdown()
    server.server_close()                                           # QEA goes down (connection refused)
    assert insights_options.get_options()["src_sprint"] is False    # last good value, not default-on


def test_get_options_with_unreachable_qea_and_no_history_uses_defaults(data_dir, monkeypatch):
    monkeypatch.setenv("QEA_API_URL", "http://127.0.0.1:9")
    assert all(insights_options.get_options().values())
