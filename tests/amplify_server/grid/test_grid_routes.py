"""Tests for the /api/grid/* coordinator proxy (routes/grid.py)."""
import io
import json
import urllib.error
from types import SimpleNamespace

import pytest
from flask import Flask

from amplify_server import auth_utils
from amplify_server.routes import grid


class _Resp:
    def __init__(self, payload, status=200):
        self._body, self.status = json.dumps(payload).encode(), status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def client(monkeypatch, data_dir):   # data_dir: never read a developer's saved Grid settings
    monkeypatch.setattr(auth_utils, "verify_jwt", lambda token: 1)
    monkeypatch.setattr(
        auth_utils.User, "get_by_id",
        staticmethod(lambda uid: SimpleNamespace(id=1, username="shekar", is_active=True, is_admin=False, must_change_password=False)),
    )
    monkeypatch.setenv("GRID_COORDINATOR_URL", "http://coordinator:8000/")
    monkeypatch.setenv("GRID_API_TOKEN", "tok")
    app = Flask(__name__)
    app.register_blueprint(grid.bp)
    c = app.test_client()
    c.set_cookie("access_token", "x")
    return c


def test_requires_auth():
    app = Flask(__name__)
    app.register_blueprint(grid.bp)
    assert app.test_client().get("/api/grid/pods").status_code == 401


def test_not_configured(client, monkeypatch):
    monkeypatch.delenv("GRID_COORDINATOR_URL")
    assert client.get("/api/grid/pods").status_code == 503
    assert client.get("/api/grid/status").get_json() == {"configured": False, "reachable": False}


def test_proxy_sends_token_and_strips_trailing_slash(client, monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout):
        seen["url"], seen["token"] = req.full_url, req.get_header("X-api-token")
        return _Resp({"pods": []})

    monkeypatch.setattr(grid.urllib.request, "urlopen", fake_urlopen)
    assert client.get("/api/grid/pods").get_json() == {"pods": []}
    assert seen == {"url": "http://coordinator:8000/api/pods", "token": "tok"}


def test_start_run_forwards_selection_and_user(client, monkeypatch):
    sent = {}

    def fake_urlopen(req, timeout):
        sent["body"] = json.loads(req.data)
        return _Resp({"run_id": "run-1"})

    monkeypatch.setattr(grid.urllib.request, "urlopen", fake_urlopen)
    resp = client.post("/api/grid/runs", json={
        "tests": [" tests/a.py ", ""], "environment": "qa", "browsers": ["edge", "firefox"], "chunk_size": 2})
    assert resp.get_json() == {"run_id": "run-1"}
    assert sent["body"] == {"tests": ["tests/a.py"], "environment": "qa",
                            "browsers": ["edge", "firefox"], "chunk_size": 2, "requested_by": "shekar"}


def test_start_run_requires_tests(client):
    assert client.post("/api/grid/runs", json={"tests": ["  "]}).status_code == 400


def test_admin_environment_configuration_is_used_by_grid_config_and_run_selection(client, monkeypatch):
    from amplify_server.server_secrets import save_server_config
    save_server_config({"grid": {
        "coordinator_url": "http://coordinator:8000",
        "environments": ["qa", "prod"],
        "default_environment": "prod",
    }})

    def fake_urlopen(req, timeout):
        if req.full_url.endswith("/api/config"):
            return _Resp({"environments": ["dev", "qa", "prod"], "default_environment": "qa",
                          "browsers": ["chromium"], "default_browser": "chromium", "default_chunk_size": 3})
        return _Resp({"run_id": "run-1"})

    monkeypatch.setattr(grid.urllib.request, "urlopen", fake_urlopen)
    config = client.get("/api/grid/config").get_json()
    assert config["environments"] == ["qa", "prod"]
    assert config["default_environment"] == "prod"

    sent = {}
    def fake_start(req, timeout):
        sent["body"] = json.loads(req.data)
        return _Resp({"run_id": "run-2"})
    monkeypatch.setattr(grid.urllib.request, "urlopen", fake_start)
    response = client.post("/api/grid/runs", json={"tests": ["tests/a.py"], "environment": "PROD"})
    assert response.status_code == 200
    assert sent["body"]["environment"] == "prod"

    bad = client.post("/api/grid/runs", json={"tests": ["tests/a.py"], "environment": "staging"})
    assert bad.status_code == 400


@pytest.mark.parametrize("path", ["/api/grid/runs/..%2Fconfig", "/api/grid/chunks/a%20b"])
def test_ids_are_validated(client, path):
    assert client.get(path).status_code in (400, 404)


def test_coordinator_errors_are_translated(client, monkeypatch):
    def reject(req, timeout):
        raise urllib.error.HTTPError(
            req.full_url, 422, "bad", {}, io.BytesIO(json.dumps({"detail": "Unsupported browser 'ie'"}).encode()))

    monkeypatch.setattr(grid.urllib.request, "urlopen", reject)
    resp = client.post("/api/grid/runs", json={"tests": ["tests/a.py"], "browsers": ["ie"]})
    assert resp.status_code == 422 and "Unsupported browser" in resp.get_json()["error"]

    def unauthorized(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 401, "no", {}, io.BytesIO(b"{}"))

    monkeypatch.setattr(grid.urllib.request, "urlopen", unauthorized)
    assert client.get("/api/grid/pods").status_code == 502  # never leak a 401 to the browser session


def test_unreachable_coordinator(client, monkeypatch):
    def boom(req, timeout):
        raise OSError("connection refused")

    monkeypatch.setattr(grid.urllib.request, "urlopen", boom)
    resp = client.get("/api/grid/pods")
    assert resp.status_code == 502 and "unreachable" in resp.get_json()["error"]
