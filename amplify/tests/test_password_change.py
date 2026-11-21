"""Changing your own password, and the "choose a new one" step that follows an admin reset.

Run against the real app so the pages and the enforcement are covered, not just the endpoint.
"""
import sqlite3

import pytest

from amplify.models.user import User

PASSWORD = "secret123"
NEW = "brand-new-pw"


@pytest.fixture
def app(data_dir):
    from amplify.server import app as real_app
    return real_app


def login(app, username="plain", password=PASSWORD):
    client = app.test_client()
    return client, client.post("/api/auth/login", json={"username": username, "password": password})


def change(client, current=PASSWORD, new=NEW, **extra):
    return client.post("/api/auth/change-password", json={"current_password": current, "new_password": new, **extra})


def reset_by_admin(admin, user_id=2, password="temp-pass-1"):
    r = admin.post(f"/api/admin/users/{user_id}/reset", json={"password": password})
    assert r.status_code == 200
    return password


def logged_out_login(app, username, password):
    return app.test_client().post("/api/auth/login", json={"username": username, "password": password}).status_code


# ── changing your own password ────────────────────────────────────────────────

def test_a_user_can_change_their_password(app, admin, user):
    r = change(user)
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert logged_out_login(app, "plain", PASSWORD) == 401                     # the old one no longer works
    assert logged_out_login(app, "plain", NEW) == 200                          # the new one does


def test_they_stay_signed_in_afterwards(admin, user):
    r = change(user)
    assert "access_token=" in r.headers.get("Set-Cookie", "")
    assert user.get("/api/auth/me").status_code == 200


def test_the_current_password_must_be_right(app, admin, user):
    r = change(user, current="not-my-password")
    assert r.status_code == 400 and "incorrect" in r.get_json()["error"]
    assert logged_out_login(app, "plain", PASSWORD) == 200                     # nothing changed


@pytest.mark.parametrize("new, message", [
    ("short", "at least 6"), ("", "new password"), (PASSWORD, "different"),
    ("x" * 73, "too long"), ("é" * 37, "too long"),                              # 74 bytes
])
def test_bad_new_passwords_are_refused_with_a_reason(app, admin, user, new, message):
    r = change(user, new=new)
    assert r.status_code == 400 and message in r.get_json()["error"]
    assert logged_out_login(app, "plain", PASSWORD) == 200


def test_the_longest_allowed_password_works(app, admin, user):
    assert change(user, new="x" * 72).status_code == 200
    assert logged_out_login(app, "plain", "x" * 72) == 200


@pytest.mark.parametrize("body", [[1], "text", 5, None])
def test_a_body_that_is_not_an_object_is_a_400(admin, user, body):
    assert user.post("/api/auth/change-password", json=body).status_code == 400


@pytest.mark.parametrize("body", [{}, {"current_password": PASSWORD}, {"new_password": NEW},
                                  {"current_password": 5, "new_password": NEW}, {"current_password": PASSWORD, "new_password": ["x"]}])
def test_missing_or_non_text_fields_are_a_400(admin, user, body):
    assert user.post("/api/auth/change-password", json=body).status_code == 400


def test_you_have_to_be_signed_in(app, admin):
    assert app.test_client().post("/api/auth/change-password", json={"current_password": "a", "new_password": "bcdefgh"}).status_code in (401, 302)


def test_the_change_is_audit_logged_without_the_passwords(admin, user):
    change(user)
    log = admin.get("/api/admin/activity").get_json()
    entry = next(a for a in log if a["action"] == "change_password")
    assert entry["detail"] == "Password changed"
    assert PASSWORD not in str(log) and NEW not in str(log)


def test_an_admin_can_change_their_own_password_too(app, admin):
    assert change(admin).status_code == 200
    assert logged_out_login(app, "boss", NEW) == 200


# ── the shared password rule is the same everywhere ───────────────────────────

def test_registration_and_admin_reset_refuse_passwords_bcrypt_cannot_hash(app, admin, user):
    too_long = "x" * 73
    r = app.test_client().post("/api/auth/register", json={"username": "newcomer", "password": too_long})
    assert r.status_code == 400 and "too long" in r.get_json()["error"]                 # used to be a 500
    r = admin.post("/api/admin/users/2/reset", json={"password": too_long})
    assert r.status_code == 400 and "too long" in r.get_json()["error"]
    assert admin.post("/api/admin/users/2/reset", json={"password": ["x"]}).status_code == 400
    assert logged_out_login(app, "plain", PASSWORD) == 200


def test_the_rule_itself():
    assert User.password_problem("123456") is None and User.password_problem("12345") and User.password_problem(None)
    assert User.password_problem("x" * 72) is None and User.password_problem("x" * 73)


# ── after an admin reset ──────────────────────────────────────────────────────

def test_an_admin_reset_marks_the_user_as_having_to_choose_a_new_password(app, admin, user):
    assert admin.get("/api/admin/users").get_json()[1]["must_change_password"] is False
    reset_by_admin(admin)
    assert admin.get("/api/admin/users").get_json()[1]["must_change_password"] is True
    client, r = login(app, "plain", "temp-pass-1")
    assert r.status_code == 200 and r.get_json()["must_change_password"] is True


def test_until_they_do_the_api_refuses_everything_else(app, admin, user):
    reset_by_admin(admin)
    client, _ = login(app, "plain", "temp-pass-1")
    for url in ("/api/config", "/api/grid/status", "/api/dashboard/overview", "/api/fm/browse"):
        r = client.get(url)
        assert r.status_code == 403, url
    blocked = client.get("/api/config")
    assert blocked.get_json()["code"] == "password_change_required"


def test_the_session_they_already_had_is_held_to_it_too(admin, user):
    assert user.get("/api/config").status_code == 200
    reset_by_admin(admin)
    assert user.get("/api/config").status_code == 403                        # takes effect immediately


def test_a_bearer_token_is_held_to_it_too(app, admin, user):
    reset_by_admin(admin)
    client, r = login(app, "plain", "temp-pass-1")
    token = r.get_json()["access_token"]
    other = app.test_client()
    assert other.get("/api/config", headers={"Authorization": f"Bearer {token}"}).status_code == 403
    assert other.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).get_json()["must_change_password"] is True


def test_what_they_can_still_do_is_look_at_themselves_sign_out_and_change_it(app, admin, user):
    reset_by_admin(admin)
    client, _ = login(app, "plain", "temp-pass-1")
    assert client.get("/api/auth/me").status_code == 200
    assert client.post("/api/auth/logout").status_code == 200
    client, _ = login(app, "plain", "temp-pass-1")
    assert change(client, current="temp-pass-1").status_code == 200


def test_the_app_pages_send_them_to_the_change_password_page(app, admin, user):
    reset_by_admin(admin)
    client, _ = login(app, "plain", "temp-pass-1")
    for page in ("/", "/admin"):
        r = client.get(page)
        assert r.status_code == 302 and r.headers["Location"].endswith("/change-password"), page
    forced = client.get("/change-password").get_data(as_text=True)
    assert "An administrator reset the password" in forced and "Sign out" in forced and "Back to Amplify" not in forced


def test_choosing_a_new_password_lifts_the_requirement(app, admin, user):
    reset_by_admin(admin)
    client, _ = login(app, "plain", "temp-pass-1")
    assert change(client, current="temp-pass-1").status_code == 200
    assert client.get("/api/config").status_code == 200
    assert client.get("/").status_code == 200
    assert admin.get("/api/admin/users").get_json()[1]["must_change_password"] is False
    assert logged_out_login(app, "plain", "temp-pass-1") == 401                # the temporary password no longer works
    assert logged_out_login(app, "plain", NEW) == 200


def test_the_temporary_password_can_not_be_reused_as_the_new_one(app, admin, user):
    reset_by_admin(admin)
    client, _ = login(app, "plain", "temp-pass-1")
    r = change(client, current="temp-pass-1", new="temp-pass-1")
    assert r.status_code == 400 and client.get("/api/config").status_code == 403


def test_the_audit_log_says_it_was_required(app, admin, user):
    reset_by_admin(admin)
    client, _ = login(app, "plain", "temp-pass-1")
    change(client, current="temp-pass-1")
    details = [a["detail"] for a in admin.get("/api/admin/activity").get_json() if a["action"] == "change_password"]
    assert details == ["Password changed (required after an admin reset)"]
    assert any("must choose a new one" in a["detail"] for a in admin.get("/api/admin/activity").get_json() if a["action"] == "admin_reset_password")


def test_other_users_are_not_affected_by_someone_elses_reset(app, admin, user):
    other = app.test_client()
    other.post("/api/auth/register", json={"username": "third", "password": PASSWORD})
    reset_by_admin(admin, user_id=2)
    assert other.get("/api/config").status_code == 200 and admin.get("/api/config").status_code == 200


# ── the pages ─────────────────────────────────────────────────────────────────

def test_anyone_signed_in_can_open_the_change_password_page_from_the_menu(app, admin, user):
    assert "Change password" in user.get("/").get_data(as_text=True)               # the user-menu entry
    page = user.get("/change-password").get_data(as_text=True)
    assert "Current password" in page and "Back to Amplify" in page
    assert "An administrator reset" not in page and "Sign out" not in page


def test_the_page_needs_a_login(app, admin):
    r = app.test_client().get("/change-password")
    assert r.status_code == 302 and r.headers["Location"].endswith("/login")


def test_the_admin_panel_explains_a_reset_and_flags_pending_users(admin):
    page = admin.get("/admin").get_data(as_text=True)
    assert "temporary password" in page and "Must change password" in page


# ── older databases ───────────────────────────────────────────────────────────

def test_an_older_database_gains_the_column_and_nobody_is_flagged(tmp_path, monkeypatch):
    from amplify.config import settings
    from amplify.models.db import init_db
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, email TEXT, "
                 "password_hash TEXT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
                 "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    conn.execute("INSERT INTO users (username, password_hash) VALUES ('veteran', 'x')")
    conn.commit(); conn.close()
    monkeypatch.setattr(settings, "DATABASE_PATH", path)
    init_db(); init_db()                                                           # and it is safe to run twice
    assert sqlite3.connect(path).execute("SELECT must_change_password FROM users").fetchall() == [(0,)]
    assert User.get_by_username("veteran").must_change_password is False
