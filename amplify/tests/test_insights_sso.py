"""Signed sign-in hand-off from QEA to Jira Insights: the token, and the redirect that carries it."""
import time
from urllib.parse import parse_qs, urlsplit

import jwt
import pytest

from amplify import sso
from amplify.auth_utils import generate_jwt, verify_jwt
from amplify.config import settings


@pytest.fixture(autouse=True)
def fresh_ledger():
    sso._used.clear()
    yield
    sso._used.clear()


def forge(**overrides):
    """A token with valid defaults; override or drop (None) any claim, or the key/algorithm."""
    now = int(time.time())
    claims = {"sub": "7", "name": "sam", "aud": sso.AUDIENCE, "iat": now, "exp": now + 60, "jti": "forged-1"}
    key = overrides.pop("key", settings.JWT_SECRET_KEY)
    alg = overrides.pop("alg", "HS256")
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key, algorithm=alg)


# ── the token ─────────────────────────────────────────────────────────────────

def test_roundtrip_carries_the_user():
    token = sso.issue_insights_token(42, "alice")
    assert sso.verify_insights_token(token) == {"user_id": 42, "username": "alice", "embedded": False}


def test_the_embedded_flag_travels_inside_the_signed_token():
    assert sso.verify_insights_token(sso.issue_insights_token(1, "a", embedded=True))["embedded"] is True
    assert sso.verify_insights_token(forge(emb=None, jti="no-emb"))["embedded"] is False


def test_the_embedded_flag_cannot_be_flipped_without_breaking_the_signature():
    import base64, json
    head, payload, sig = sso.issue_insights_token(1, "a").split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    claims["emb"] = True
    forged = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    assert sso.verify_insights_token(f"{head}.{forged}.{sig}") is None


def test_a_token_works_once():
    token = sso.issue_insights_token(1, "a")
    assert sso.verify_insights_token(token) is not None
    assert sso.verify_insights_token(token) is None


def test_every_token_is_distinct():
    a, b = sso.issue_insights_token(1, "a"), sso.issue_insights_token(1, "a")
    assert a != b and sso.verify_insights_token(a) and sso.verify_insights_token(b)


def test_it_lives_about_a_minute():
    claims = jwt.decode(sso.issue_insights_token(1, "a"), options={"verify_signature": False})
    assert claims["exp"] - claims["iat"] == sso.TTL_SECONDS == 60


def test_expired_tokens_are_refused_but_small_clock_skew_is_tolerated():
    now = int(time.time())
    assert sso.verify_insights_token(forge(exp=now - 5, jti="skew")) is not None        # within leeway
    assert sso.verify_insights_token(forge(exp=now - 60, jti="late")) is None           # really expired


@pytest.mark.parametrize("label, token", [
    ("signed with another key",        lambda: forge(key="x" * 32)),
    ("wrong audience",                 lambda: forge(aud="somewhere-else")),
    ("no audience (a plain session)",  lambda: forge(aud=None)),
    ("a different algorithm",          lambda: forge(alg="HS512")),
    ("missing jti",                    lambda: forge(jti=None)),
    ("missing exp",                    lambda: forge(exp=None)),
    ("missing sub",                    lambda: forge(sub=None)),
    ("non-numeric subject",            lambda: forge(sub="admin")),
])
def test_forged_or_malformed_tokens_are_refused(label, token):
    assert sso.verify_insights_token(token()) is None, label


def test_alg_none_is_refused():
    now = int(time.time())
    unsigned = jwt.encode({"sub": "1", "aud": sso.AUDIENCE, "iat": now, "exp": now + 60, "jti": "n"}, None, algorithm="none")
    assert sso.verify_insights_token(unsigned) is None


def test_a_tampered_payload_is_refused():
    head, payload, sig = sso.issue_insights_token(1, "a").split(".")
    flipped = payload[:-2] + ("AA" if payload[-2:] != "AA" else "BB")
    assert sso.verify_insights_token(f"{head}.{flipped}.{sig}") is None


@pytest.mark.parametrize("junk", ["", "abc", "a.b.c", None, 123])
def test_garbage_is_refused_not_raised(junk):
    assert sso.verify_insights_token(junk) is None


def test_a_refused_token_does_not_burn_its_id():
    bad = forge(sub="nope", jti="same-id")
    assert sso.verify_insights_token(bad) is None
    assert sso.verify_insights_token(forge(jti="same-id")) is not None


def test_missing_name_falls_back_to_the_id():
    assert sso.verify_insights_token(forge(name=None))["username"] == "user 7"


def test_the_used_ledger_forgets_expired_entries():
    sso._used["old"] = time.time() - 1
    assert sso.verify_insights_token(sso.issue_insights_token(1, "a"))
    assert "old" not in sso._used


# ── the two token kinds can't stand in for each other ─────────────────────────

def test_a_sign_in_token_is_not_a_qea_session():
    assert verify_jwt(sso.issue_insights_token(1, "a")) is None


def test_a_qea_session_is_not_a_sign_in_token():
    assert sso.verify_insights_token(generate_jwt(1)) is None


# ── GET /api/insights/open ────────────────────────────────────────────────────

def location(response):
    parts = urlsplit(response.headers["Location"])
    return f"{parts.scheme}://{parts.netloc}", parse_qs(parts.query)


def test_open_requires_a_qea_login(app):
    anon = app.test_client()
    r = anon.get("/api/insights/open")
    assert r.status_code == 401 and "Location" not in r.headers


def test_open_redirects_to_insights_with_a_token_for_the_caller(admin):
    r = admin.get("/api/insights/open")
    assert r.status_code == 302
    base, query = location(r)
    assert base == "http://localhost:8501"
    who = sso.verify_insights_token(query["sso"][0])
    assert who["username"] == "boss" and who["user_id"] == 1


def test_each_open_mints_a_fresh_token(admin):
    first = location(admin.get("/api/insights/open"))[1]["sso"][0]
    second = location(admin.get("/api/insights/open"))[1]["sso"][0]
    assert first != second


def test_tokens_are_for_the_caller_not_for_whoever_the_url_names(admin, user):
    query = location(user.get("/api/insights/open?user_id=1&sub=1&username=boss"))[1]
    who = sso.verify_insights_token(query["sso"][0])
    assert who["username"] == "plain" and who["user_id"] == 2
    assert "user_id" not in query


def test_theme_and_embed_options(admin):
    query = location(admin.get("/api/insights/open?theme=dark&embed=1"))[1]
    assert query["theme"] == ["dark"] and query["embed"] == ["true"] and query["embed_options"] == ["dark_theme"]
    assert sso.verify_insights_token(query["sso"][0])["embedded"] is True
    query = location(admin.get("/api/insights/open"))[1]
    assert query["theme"] == ["light"] and "embed" not in query and "embed_options" not in query
    assert sso.verify_insights_token(query["sso"][0])["embedded"] is False


def test_an_odd_theme_value_is_not_passed_on(admin):
    query = location(admin.get("/api/insights/open?theme=%22%3E%3Cscript%3E"))[1]
    assert query["theme"] == ["light"]


def test_the_redirect_target_cannot_be_chosen_by_the_caller(admin):
    r = admin.get("/api/insights/open?next=https://evil.example&url=https://evil.example&redirect=https://evil.example")
    assert "evil.example" not in r.headers["Location"]


def test_public_url_wins_and_has_its_slash_trimmed(admin, monkeypatch):
    monkeypatch.setenv("INSIGHTS_PUBLIC_URL", "https://insights.corp.example/")
    assert location(admin.get("/api/insights/open"))[0] == "https://insights.corp.example"


def test_port_comes_from_insights_port(admin, monkeypatch):
    monkeypatch.setenv("INSIGHTS_PORT", "9123")
    assert location(admin.get("/api/insights/open"))[0] == "http://localhost:9123"


def test_behind_a_tls_proxy_the_scheme_is_https(admin):
    r = admin.get("/api/insights/open", headers={"X-Forwarded-Proto": "https"})
    assert location(r)[0] == "https://localhost:8501"


def test_a_bogus_forwarded_scheme_falls_back_to_http(admin):
    r = admin.get("/api/insights/open", headers={"X-Forwarded-Proto": "javascript"})
    assert location(r)[0] == "http://localhost:8501"


def test_ipv6_hosts_keep_their_brackets(app, admin):
    # The cookie jar is per-host, so authenticate this request with a bearer token instead.
    token = admin.post("/api/auth/login", json={"username": "boss", "password": "secret123"}).get_json()["access_token"]
    r = app.test_client().get("/api/insights/open", headers={"Authorization": f"Bearer {token}"},
                              environ_overrides={"HTTP_HOST": "[::1]:7777"})
    assert location(r)[0] == "http://[::1]:8501"


def test_the_redirect_is_not_cached_and_sends_no_referrer(admin):
    r = admin.get("/api/insights/open")
    assert r.headers["Cache-Control"] == "no-store"
    assert r.headers["Referrer-Policy"] == "no-referrer"


def test_a_disabled_account_gets_no_token(admin, user):
    assert admin.post("/api/admin/users/2/toggle").get_json()["is_active"] is False
    r = user.get("/api/insights/open")
    assert r.status_code == 403 and "Location" not in r.headers


# ── no well-known signing key ─────────────────────────────────────────────────

OLD_DEFAULT = "amplify-jwt-secret-change-in-prod"


@pytest.fixture
def unset_key(tmp_path, monkeypatch):
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    monkeypatch.setattr(settings, "_DATA_ROOT", tmp_path)
    return tmp_path


def test_a_configured_key_is_used_as_given(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "_DATA_ROOT", tmp_path)
    monkeypatch.setenv("JWT_SECRET_KEY", "  from-the-environment ")
    assert settings._jwt_secret() == "from-the-environment" and not (tmp_path / ".jwt_secret").exists()


def test_without_one_a_random_key_is_made_once_and_kept_private(unset_key):
    first = settings._jwt_secret()
    assert first != OLD_DEFAULT and len(first) >= 48
    assert settings._jwt_secret() == first                                  # same key on every start
    assert (unset_key / ".jwt_secret").stat().st_mode & 0o777 == 0o600


def test_a_blank_setting_counts_as_unset(unset_key, monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "   ")
    assert settings._jwt_secret() != "" and (unset_key / ".jwt_secret").exists()


def test_separate_installs_get_different_keys(tmp_path, monkeypatch):
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    keys = set()
    for name in ("a", "b"):
        monkeypatch.setattr(settings, "_DATA_ROOT", tmp_path / name)
        (tmp_path / name).mkdir()
        keys.add(settings._jwt_secret())
    assert len(keys) == 2


def test_two_apps_starting_together_agree_on_one_key(unset_key):
    import threading
    path = unset_key / ".jwt_secret"
    path.write_text("")                                                      # the other app has created it...
    threading.Timer(0.2, lambda: path.write_text("winner-key")).start()      # ...and is about to fill it in
    assert settings._jwt_secret() == "winner-key"


def test_an_unwritable_data_dir_still_gives_a_random_key(tmp_path, monkeypatch):
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    monkeypatch.setattr(settings, "_DATA_ROOT", tmp_path / "missing" / "dir")
    key = settings._jwt_secret()
    assert key and key != OLD_DEFAULT


def test_a_token_signed_with_the_old_public_default_is_refused():
    assert sso.verify_insights_token(forge(key=OLD_DEFAULT, jti="old-default")) is None
    assert settings.JWT_SECRET_KEY != OLD_DEFAULT


# ── a new tab without a QEA session ───────────────────────────────────────────

def test_a_new_tab_without_a_session_goes_to_the_login_page(app):
    r = app.test_client().get("/api/insights/open", headers={"Sec-Fetch-Dest": "document"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/login")


def test_the_iframe_and_api_callers_still_get_a_401(app):
    anon = app.test_client()
    assert anon.get("/api/insights/open", headers={"Sec-Fetch-Dest": "iframe"}).status_code == 401
    assert anon.get("/api/insights/open").status_code == 401


def test_a_signed_in_new_tab_still_goes_to_insights(admin):
    r = admin.get("/api/insights/open", headers={"Sec-Fetch-Dest": "document"})
    assert r.status_code == 302 and location(r)[0] == "http://localhost:8501"
