"""
Jira Insights settings — Jira connection details and workspace location.

Jira credentials are reused from the Test Management config section
(Config → Zephyr: Jira URL, username, API token, verify SSL) so Jira is
configured once. Environment variables win, which lets containers inject
secrets without writing them to config.json:

  JIRA_URL, JIRA_USERNAME, JIRA_API_TOKEN, JIRA_VERIFY_SSL
  JIRA_INSIGHTS_HOME   — workspace directory (datasets, reports, chart specs)
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

TOOL_ROOT = Path(__file__).resolve().parent.parent

_IN_CONTAINER = os.environ.get("CONTAINER", "0") == "1"
_DEFAULT_WORKSPACE = (
    Path("/app/data/jira_insights_default") if _IN_CONTAINER
    else TOOL_ROOT / "jira_insights" / "workspace"
)


@dataclass(frozen=True)
class JiraSettings:
    url: str = ""
    username: str = ""        # empty → Bearer auth (Jira Data Center PAT)
    token: str = ""
    verify_ssl: bool = False

    @property
    def configured(self) -> bool:
        return bool(self.url and self.token)


def workspace_dir(user_id: int | str | None = None) -> Path:
    if "JIRA_INSIGHTS_HOME" in os.environ:
        return Path(os.environ["JIRA_INSIGHTS_HOME"])

    uid = user_id or _resolve_user_id()
    if uid:
        from amplify.storage import get_user_workspace_dir
        user_ws = get_user_workspace_dir(int(uid))
        insights_ws = user_ws / "jira_insights"
        insights_ws.mkdir(parents=True, exist_ok=True)
        return insights_ws

    return Path(_DEFAULT_WORKSPACE)


def _resolve_user_id() -> str | None:
    """Get user ID from Streamlit query params, then env var fallback."""
    try:
        import streamlit as st
        uid = st.query_params.get("user_id")
        if uid:
            return uid
    except Exception:
        pass
    return os.environ.get("USER_ID")


def _truthy(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


def load_jira_settings() -> JiraSettings:
    try:
        from amplify.server_secrets import load_zephyr_config
        cfg = load_zephyr_config()
    except Exception:
        cfg = {}

    if not cfg:
        try:
            from amplify.config_reader import ConfigReader
            cfg = ConfigReader().load().get("zephyr", {}) or {}
        except Exception:
            cfg = {}

    return JiraSettings(
        url=cfg.get("jira_url", "").strip().rstrip("/"),
        username=cfg.get("username", "").strip(),
        token=cfg.get("api_token", "").strip(),
        verify_ssl=bool(cfg.get("verify_ssl", False)),
    )
