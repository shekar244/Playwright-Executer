import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
TEST_ROOT = ROOT / "tests" / "amplify_server"
if str(TEST_ROOT) not in sys.path:
    sys.path.insert(0, str(TEST_ROOT))


# ── shared fixtures: a throwaway data dir + a minimal app with real login ─────
import importlib

import pytest


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Isolated users DB / workspaces / server_config.json — never touches ~/.amplify-qea."""
    from amplify_server.config import settings
    monkeypatch.setattr(settings, "DATABASE_PATH", tmp_path / "amplify_server.db")
    monkeypatch.setattr(settings, "WORKSPACES_ROOT", tmp_path / "workspaces")
    monkeypatch.delenv("QEA_API_URL", raising=False)
    from jira_insights import options as insights_options
    insights_options._cache.update(at=0.0, url=None, value=None)
    from amplify_server.models.db import init_db
    init_db()
    return tmp_path


@pytest.fixture
def app(data_dir):
    """Flask app with the auth, admin, config and insights blueprints."""
    from flask import Flask
    flask_app = Flask(__name__)
    for name in ("auth", "admin", "config", "insights"):
        flask_app.register_blueprint(importlib.import_module(f"amplify_server.routes.{name}").bp)
    return flask_app


@pytest.fixture
def admin(app):
    """A logged-in admin client (the first registered user is the admin)."""
    c = app.test_client()
    assert c.post("/api/auth/register", json={"username": "boss", "password": "secret123"}).status_code == 201
    return c


@pytest.fixture
def user(app, admin):
    """A logged-in regular user client."""
    c = app.test_client()
    assert c.post("/api/auth/register", json={"username": "plain", "password": "secret123"}).status_code == 201
    return c
