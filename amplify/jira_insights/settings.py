"""
Jira Insights settings — Jira connection details and workspace location.

The Jira connection is set up once, by an admin, in Amplify QEA (Admin Panel →
Server Secrets) and shared by Test Management and Jira Insights. Users never
enter or see the credentials. Environment variables supply initial defaults,
which lets containers inject secrets; a value saved in the Admin Panel wins:

  JIRA_URL, JIRA_USERNAME, JIRA_API_TOKEN
  JIRA_INSIGHTS_HOME   — workspace directory (datasets, reports, chart specs)
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

TOOL_ROOT = Path(__file__).resolve().parent.parent

# Shown wherever Jira is missing, so users are pointed at the right person, not at a screen they cannot use.
SETUP_BY_ADMIN = "Ask an admin to set up Jira in Amplify QEA (Admin Panel → Server Secrets)."

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
    """The signed-in Insights user, else the USER_ID env var.

    Never a URL parameter: anything in the URL is editable by the person holding it.
    """
    try:
        import streamlit as st
        uid = st.session_state.get("ji_auth_user_id")
        if uid:
            return str(uid)
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
