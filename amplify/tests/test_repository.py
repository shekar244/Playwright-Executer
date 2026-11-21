"""The Repository tab and its access tokens (amplify/repo_store.py, routes/repo.py).

Repos are per user in <workspace>/repos. A PAT is saved server-side (mode 0600), never returned to a browser and
never written into a repo's .git/config: git gets it per call from its environment. Clones made before kept it in the
remote URL; they are migrated (token moved into the saved settings, URL cleaned).
"""
import importlib
import json
import os
import stat
import subprocess
from pathlib import Path

import pytest
from flask import Flask

from amplify import repo_store as rs

TOKEN = "ghp_secretTOKEN1234567890"
URL = "https://github.com/acme/qe-tests.git"


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def make_clone(user_id, name="qe-tests", remote=URL):
    path = rs.repos_dir(user_id) / name
    path.mkdir(parents=True)
    git("init", "-q", "-b", "main", cwd=path)
    git("-c", "user.email=a@b", "-c", "user.name=Tester", "commit", "-q", "--allow-empty", "-m", "first <b>commit</b>", cwd=path)
    git("remote", "add", "origin", remote, cwd=path)
    return path


# ── URLs, tokens, git ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("url, clean, token", [
    (f"https://x-access-token:{TOKEN}@github.com/acme/qe-tests.git", URL, TOKEN),
    ("https://oauth2:a%40b@gitlab.example.com/g/r.git", "https://gitlab.example.com/g/r.git", "a@b"),
    ("https://user@github.com/acme/r.git", "https://github.com/acme/r.git", None),
    (URL, URL, None),
])
def test_credentials_are_split_off_a_url(url, clean, token):
    assert rs.split_credentials(url) == (clean, token)


def test_the_token_reaches_git_through_its_environment_never_its_command_line(monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(cmd=cmd, env=kw["env"])
        return subprocess.CompletedProcess(cmd, 1, "", f"fatal: could not read from https://x:{TOKEN}@github.com")

    monkeypatch.setattr(rs.subprocess, "run", fake_run)
    code, out = rs.run_git(["pull"], "/tmp", TOKEN, URL)

    assert TOKEN not in " ".join(seen["cmd"]) and seen["env"]["AMPLIFY_GIT_TOKEN"] == TOKEN
    assert seen["cmd"][:3] == ["git", "-c", "credential.helper="] and "username=x-access-token" in seen["cmd"][4]
    assert TOKEN not in out and "***" in out                              # git's own error output is masked


def test_the_inline_credential_helper_really_answers_git(tmp_path):
    """git itself asks the helper (no network): it must hand back the user name and the token from the env."""
    env = dict(os.environ, AMPLIFY_GIT_TOKEN=TOKEN, GIT_TERMINAL_PROMPT="0")
    out = subprocess.run(["git", *rs.credential_args(URL), "credential", "fill"],
                         input="protocol=https\nhost=github.com\n\n", capture_output=True, text=True, env=env, cwd=tmp_path)

    assert "username=x-access-token" in out.stdout and f"password={TOKEN}" in out.stdout


@pytest.mark.parametrize("url, user", [(URL, "x-access-token"), ("https://gitlab.acme.io/a/b.git", "oauth2"),
                                       ("https://bitbucket.org/a/b.git", "x-token-auth")])
def test_the_user_name_git_sends_with_a_token(url, user):
    assert rs.git_username(url) == user


# ── saved settings and the migration ──────────────────────────────────────────

def test_saved_settings_are_private_to_the_server_and_the_token_is_write_only(data_dir):
    rs.upsert(1, "qe-tests", URL, branch="main", token=TOKEN)
    rs.upsert(1, "qe-tests", URL, branch="dev")                           # no token given: the saved one stays

    path = data_dir / "workspaces" / "1" / "repo_configs.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert rs.token_for(1, URL) == TOKEN and rs.load_configs(1)[0]["branch"] == "dev"
    assert rs.public(rs.load_configs(1)[0]) == {"name": "qe-tests", "url": URL, "branch": "dev", "has_token": True}
    rs.upsert(1, "qe-tests", URL, clear_token=True)
    assert rs.token_for(1, URL) == ""


def test_an_old_clone_with_the_token_in_its_remote_is_migrated(data_dir):
    path = make_clone(1, remote=f"https://x-access-token:{TOKEN}@github.com/acme/qe-tests.git")

    assert rs.migrate_embedded_credentials(1) == ["qe-tests"]

    assert TOKEN not in (path / ".git" / "config").read_text()
    assert rs.remote_url(path) == URL and rs.token_for(1, URL) == TOKEN
    assert rs.load_configs(1)[0]["branch"] == "main"
    assert rs.migrate_embedded_credentials(1) == []                       # nothing left to do the second time


def test_describe_reports_branch_commit_and_changes(data_dir):
    path = make_clone(1)
    (path / "f.txt").write_text("x")
    git("add", "f.txt", cwd=path)

    info = rs.describe(1, path, active=True)

    assert (info["name"], info["branch"], info["remote"], info["active"], info["dirty"]) == ("qe-tests", "main", URL, True, True)
    assert info["head"]["subject"] == "first <b>commit</b>" and info["head"]["author"] == "Tester" and len(info["head"]["sha"]) == 40


@pytest.mark.parametrize("name", ["..", "../x", ".git", "a/b", "", None, "-rf"])
def test_only_a_plain_clone_name_is_ever_resolved(data_dir, name):
    assert rs.clone_by_name(1, name) is None


# ── the endpoints ─────────────────────────────────────────────────────────────

@pytest.fixture
def client(data_dir):
    app = Flask(__name__)
    for name in ("auth", "repo"):
        app.register_blueprint(importlib.import_module(f"amplify.routes.{name}").bp)
    c = app.test_client()
    assert c.post("/api/auth/register", json={"username": "boss", "password": "secret123"}).status_code == 201
    return c


def test_the_workspace_lists_clones_saved_repos_and_where_they_live_and_never_a_token(client, data_dir):
    make_clone(1, remote=f"https://x-access-token:{TOKEN}@github.com/acme/qe-tests.git")
    rs.upsert(1, "other", "https://github.com/acme/other.git", token="t2")

    resp = client.get("/api/repo/workspace")

    body = resp.get_json()
    assert resp.status_code == 200 and TOKEN not in resp.get_data(as_text=True) and "t2" not in json.dumps(body["saved"])
    assert [r["name"] for r in body["repos"]] == ["qe-tests"] and body["repos"][0]["has_token"] is True
    assert {s["name"]: s["cloned"] for s in body["saved"]} == {"qe-tests": True, "other": False}
    assert body["location"]["path"].endswith("/workspaces/1/repos") and "PVC" in body["location"]["storage"]


def test_use_makes_a_clone_the_active_repo(client, data_dir):
    path = make_clone(1)

    assert client.post("/api/repo/use", json={"name": "qe-tests"}).status_code == 200
    status = client.get("/api/repo/status").get_json()
    assert status["repo_path"] == str(path) and status["branch"] == "main" and status["remote"] == URL
    assert client.post("/api/repo/use", json={"name": "../../etc"}).status_code == 404


def test_a_token_can_be_set_and_removed_and_the_configs_never_return_it(client, data_dir):
    make_clone(1)

    assert client.post("/api/repo/token", json={"name": "qe-tests", "token": TOKEN}).get_json()["has_token"] is True
    configs = client.get("/api/repo/configs").get_json()
    assert configs == [{"name": "qe-tests", "url": URL, "branch": "", "has_token": True}]
    assert client.post("/api/repo/token", json={"name": "qe-tests", "clear_token": True}).get_json()["has_token"] is False
    assert client.post("/api/repo/token", json={"name": "nope", "token": "x"}).status_code == 404


def test_saving_a_config_with_an_empty_token_keeps_the_saved_one(client):
    client.post("/api/repo/configs", json={"name": "r", "url": URL, "token": TOKEN})
    client.post("/api/repo/configs", json={"name": "r", "url": URL, "branch": "dev", "token": ""})

    assert rs.token_for(1, URL) == TOKEN and client.get("/api/repo/configs").get_json()[0]["branch"] == "dev"


def test_removing_a_clone_keeps_its_saved_settings_and_clears_the_active_repo(client, data_dir):
    path = make_clone(1)
    client.post("/api/repo/token", json={"name": "qe-tests", "token": TOKEN})
    client.post("/api/repo/use", json={"name": "qe-tests"})

    resp = client.post("/api/repo/remove", json={"name": "qe-tests"})

    assert resp.get_json() == {"ok": True, "was_active": True} and not path.exists()
    assert client.get("/api/repo/status").get_json()["has_repo"] is False and rs.token_for(1, URL) == TOKEN


def test_cloning_saves_the_repo_and_never_puts_the_token_in_the_remote(client, data_dir, monkeypatch):
    calls = []

    def fake_git(args, cwd, token=None, url="", timeout=120):
        calls.append((args, token))
        if args[0] == "clone":
            dest = Path(args[-1])
            dest.mkdir(parents=True)
            git("init", "-q", "-b", "main", cwd=dest)
            git("remote", "add", "origin", args[-2], cwd=dest)
        return 0, "ok"

    monkeypatch.setattr(rs, "run_git", fake_git)
    resp = client.post("/api/repo/clone", json={"url": f"https://x-access-token:{TOKEN}@github.com/acme/qe-tests", "branch": "main"})

    assert resp.status_code == 201 and resp.get_json()["name"] == "qe-tests"
    clone_args, token = next(c for c in calls if c[0][0] == "clone")
    assert token == TOKEN and TOKEN not in " ".join(clone_args) and URL in clone_args
    assert rs.token_for(1, URL) == TOKEN and rs.load_configs(1)[0]["branch"] == "main"


def test_a_saved_repo_is_cloned_by_name_with_its_saved_token(client, data_dir, monkeypatch):
    rs.upsert(1, "qe-tests", URL, branch="dev", token=TOKEN)
    calls = []
    monkeypatch.setattr(rs, "run_git", lambda args, cwd, token=None, url="", timeout=120: calls.append((args, token)) or (1, "nope"))

    client.post("/api/repo/clone", json={"name": "qe-tests"})

    args, token = calls[0]
    assert args[:4] == ["clone", "--progress", "--branch", "dev"] and token == TOKEN


def test_remote_branches_take_the_token_in_the_body_never_the_address(client):
    assert client.get(f"/api/repo/branches?url={URL}&token={TOKEN}").status_code == 400
