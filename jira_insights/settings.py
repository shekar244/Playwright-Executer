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
_DEFAULT_WORKSPACE = TOOL_ROOT / "jira_insights" / "workspace"


@dataclass(frozen=True)
class JiraSettings:
    url: str = ""
    username: str = ""        # empty → Bearer auth (Jira Data Center PAT)
    token: str = ""
    verify_ssl: bool = False

    @property
    def configured(self) -> bool:
        return bool(self.url and self.token)


def workspace_dir() -> Path:
    return Path(os.environ.get("JIRA_INSIGHTS_HOME") or _DEFAULT_WORKSPACE)


def _truthy(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


def load_jira_settings() -> JiraSettings:
    try:
        from ui_launcher.config_reader import ConfigReader
        zephyr = ConfigReader().load().get("zephyr", {}) or {}
    except Exception:
        zephyr = {}

    env = os.environ.get
    verify = env("JIRA_VERIFY_SSL")
    return JiraSettings(
        url=(env("JIRA_URL") or zephyr.get("jira_url", "")).strip().rstrip("/"),
        username=(env("JIRA_USERNAME") or zephyr.get("username", "")).strip(),
        token=(env("JIRA_API_TOKEN") or zephyr.get("api_token", "")).strip(),
        verify_ssl=_truthy(verify) if verify is not None else bool(zephyr.get("verify_ssl", False)),
    )
