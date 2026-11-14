"""
Server-level secrets for Jira and Zephyr.

Credentials are resolved from environment variables first, then from a
server config file at /app/data/server_config.json (or ~/.amplify-qea/
server_config.json locally). Individual users do NOT need to configure
these — they are shared across all users on the server.

Environment variables (highest priority):
  JIRA_URL             — https://yourcompany.atlassian.net
  JIRA_USERNAME        — Atlassian email
  JIRA_API_TOKEN       — Atlassian API token
  JIRA_VERIFY_SSL      — 1/true/yes to verify SSL (default: false)
  ZEPHYR_ACCESS_KEY    — Zephyr for Jira Cloud access key
  ZEPHYR_SECRET_KEY    — Zephyr for Jira Cloud secret key
  ZEPHYR_ACCOUNT_ID    — Atlassian account ID
  ZEPHYR_PROJECT_KEY   — Default Jira project key

Server config file (second priority):
  /app/data/server_config.json  (container)
  ~/.amplify-qea/server_config.json  (local dev)

  {
    "zephyr": {
      "jira_url": "...",
      "username": "...",
      "api_token": "...",
      "access_key": "...",
      "secret_key": "...",
      "account_id": "...",
      "project_key": "...",
      "verify_ssl": false
    }
  }
"""
import json
import os
from pathlib import Path

from amplify.config import settings


def _server_config_path() -> Path:
    return settings.DATABASE_PATH.parent / "server_config.json"


def _load_server_file() -> dict:
    p = _server_config_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_server_config(data: dict) -> None:
    p = _server_config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_zephyr_config() -> dict:
    """Load Zephyr/Jira config from env vars → server file → empty dict."""
    env = os.environ.get

    env_cfg = {}
    if env("JIRA_URL"):
        env_cfg["jira_url"] = env("JIRA_URL", "").strip().rstrip("/")
    if env("JIRA_USERNAME"):
        env_cfg["username"] = env("JIRA_USERNAME", "").strip()
    if env("JIRA_API_TOKEN"):
        env_cfg["api_token"] = env("JIRA_API_TOKEN", "").strip()
    if env("ZEPHYR_ACCESS_KEY"):
        env_cfg["access_key"] = env("ZEPHYR_ACCESS_KEY", "").strip()
    if env("ZEPHYR_SECRET_KEY"):
        env_cfg["secret_key"] = env("ZEPHYR_SECRET_KEY", "").strip()
    if env("ZEPHYR_ACCOUNT_ID"):
        env_cfg["account_id"] = env("ZEPHYR_ACCOUNT_ID", "").strip()
    if env("ZEPHYR_PROJECT_KEY"):
        env_cfg["project_key"] = env("ZEPHYR_PROJECT_KEY", "").strip()
    if env("JIRA_VERIFY_SSL") is not None:
        env_cfg["verify_ssl"] = env("JIRA_VERIFY_SSL", "").strip().lower() in ("1", "true", "yes")

    if env_cfg:
        file_cfg = _load_server_file().get("zephyr", {})
        merged = {**file_cfg, **env_cfg}
        return merged

    file_cfg = _load_server_file().get("zephyr", {})
    if file_cfg:
        return file_cfg

    return {}
