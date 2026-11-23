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
import re
from pathlib import Path

from amplify_server.config import settings


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
    """Load Zephyr/Jira config: server file wins → env vars as fallback."""
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

    file_cfg = _load_server_file().get("zephyr", {})

    # Server config file wins over env vars (admin panel saves here)
    merged = {**env_cfg, **file_cfg}
    merged.setdefault("verify_ssl", False)

    return merged if merged else {}


ZEPHYR_SECRET_FIELDS = ("api_token", "access_key", "secret_key")
SECRET_MASK = "•"          # the Admin Panel shows secrets as abc•••xyz; that text is never a real value


def save_zephyr_config(updates: dict) -> None:
    """Merge the Jira/Zephyr form into the saved config without losing what it didn't send.

    Secrets are shown masked and the form can't resend them, so for a secret field:
      missing, or still masked   → unchanged        new text → replaced        empty → removed
    Every other field is replaced by what was sent; fields not sent keep their saved value.
    """
    data = _load_server_file()
    current = data.get("zephyr")
    merged = dict(current) if isinstance(current, dict) else {}
    for key, value in updates.items():
        if isinstance(value, str):
            value = value.strip()
            if key in ZEPHYR_SECRET_FIELDS:
                if SECRET_MASK in value:
                    continue
                if not value:
                    merged.pop(key, None)
                    continue
        merged[key] = value
    data["zephyr"] = merged
    save_server_config(data)


# ── Grid coordinator (server-wide) ────────────────────────────────────────────
# Where the Grid tab sends distributed runs. Set in the Admin Panel (saved under "grid" in
# server_config.json) or with GRID_COORDINATOR_URL / GRID_API_TOKEN as defaults; a value saved in the
# Admin Panel wins, and clearing it falls back to the environment variable.

_GRID_TOKEN_MAX = 512


def _grid_value(name: str, env_name: str) -> tuple[str, str]:
    """(value, where it came from: "saved" | "env" | "none")."""
    saved = _load_server_file().get("grid", {})
    saved = saved.get(name) if isinstance(saved, dict) else None
    if isinstance(saved, str) and saved.strip():
        return saved.strip(), "saved"
    env = os.environ.get(env_name, "").strip()
    return (env, "env") if env else ("", "none")


def load_grid_config() -> dict:
    """{"coordinator_url", "api_token"} — for server-side use only; the token never goes to a browser."""
    return {"coordinator_url": _grid_value("coordinator_url", "GRID_COORDINATOR_URL")[0].rstrip("/"),
            "api_token": _grid_value("api_token", "GRID_API_TOKEN")[0]}


def load_grid_environments() -> dict:
    """Configured Grid environment choices and default; [] means use the coordinator values."""
    saved = _load_server_file().get("grid", {})
    saved = saved if isinstance(saved, dict) else {}
    environments = saved.get("environments")
    if not isinstance(environments, list):
        environments = []
    environments = [str(value).strip().lower() for value in environments
                    if isinstance(value, str) and str(value).strip()]
    default_environment = str(saved.get("default_environment") or "").strip().lower()
    if default_environment not in environments:
        default_environment = environments[0] if environments else ""
    return {"environments": environments, "default_environment": default_environment}


def grid_config_summary() -> dict:
    """What the Admin Panel may show: the URL, whether a token exists, and where each came from."""
    url, url_source = _grid_value("coordinator_url", "GRID_COORDINATOR_URL")
    token, token_source = _grid_value("api_token", "GRID_API_TOKEN")
    return {"coordinator_url": url.rstrip("/"), "url_source": url_source,
            "has_token": bool(token), "token_source": token_source}


def normalize_coordinator_url(value) -> str:
    """A clean http(s) base URL, or "" for blank. Raises ValueError for anything else."""
    from urllib.parse import urlsplit
    if value is None or not str(value).strip():
        return ""
    text = str(value).strip()
    try:
        parts = urlsplit(text)
        parts.port                                            # raises on a malformed port
    except ValueError:
        raise ValueError("That is not a valid URL") from None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("Coordinator URL must start with http:// or https:// and name a host")
    if parts.username or parts.password:
        raise ValueError("Put the token in the API token field, not in the URL")
    if parts.query or parts.fragment or any(c.isspace() for c in text):
        raise ValueError("Coordinator URL should be just the address, e.g. https://grid.example.com:8000")
    return text.rstrip("/")


def save_grid_config(coordinator_url=None, api_token=None, clear_token: bool = False,
                     environments=None, default_environment=None) -> None:
    """Save what was given; leave the rest alone.

    coordinator_url  None = unchanged · "" = remove the saved URL (the env var applies again)
    api_token        None or "" = keep the saved token · text = replace it
    clear_token      True = remove the saved token

    Raises ValueError on bad input (nothing is saved).
    """
    saved = _load_server_file().get("grid", {})
    saved = dict(saved) if isinstance(saved, dict) else {}
    if coordinator_url is not None:
        url = normalize_coordinator_url(coordinator_url)
        if url:
            saved["coordinator_url"] = url
        else:
            saved.pop("coordinator_url", None)
    if clear_token:
        saved.pop("api_token", None)
    elif api_token is not None and str(api_token).strip():
        token = str(api_token).strip()
        if len(token) > _GRID_TOKEN_MAX or any(ord(c) < 32 or ord(c) == 127 for c in token):
            raise ValueError("The API token has characters that can't be sent in a header")
        saved["api_token"] = token
    if environments is not None:
        if not isinstance(environments, list):
            raise ValueError("Grid environments must be a list")
        normalized = []
        seen = set()
        for value in environments:
            if not isinstance(value, str):
                raise ValueError("Each Grid environment must be text")
            name = value.strip().lower()
            if not name:
                continue
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
                raise ValueError("Environment names may contain only letters, numbers, hyphens and underscores")
            if name not in seen:
                normalized.append(name)
                seen.add(name)
        if normalized:
            saved["environments"] = normalized
        else:
            saved.pop("environments", None)
            saved.pop("default_environment", None)
    if default_environment is not None:
        default = str(default_environment).strip().lower()
        configured = environments if environments is not None else saved.get("environments")
        configured = configured if isinstance(configured, list) else []
        configured = [str(v).strip().lower() for v in configured if isinstance(v, str) and str(v).strip()]
        if default and default not in configured:
            raise ValueError("The default Grid environment must be in the environments list")
        if default:
            saved["default_environment"] = default
        else:
            saved.pop("default_environment", None)
    data = _load_server_file()
    data["grid"] = saved
    save_server_config(data)


# ── Admin-controlled visibility flags (server-wide) ───────────────────────────
# Two sets live in server_config.json, each a {key: bool} map the admin edits in
# the Admin Panel and every user receives:
#   "ui_tabs"          tabs of the main app             (UI_TAB_CATALOG)
#   "insights_options" sidebar options + views of Jira Insights (INSIGHTS_OPTION_CATALOG)
# A catalog entry is {"key", "label", "group", "default"}; unknown keys are rejected on save.

def _load_flags(section: str, defaults: dict) -> dict:
    saved = _load_server_file().get(section, {})
    if not isinstance(saved, dict):
        saved = {}
    return {key: saved[key] if isinstance(saved.get(key), bool) else default
            for key, default in defaults.items()}


def _merge_flags(section: str, defaults: dict, updates: dict, noun: str) -> dict:
    merged = _load_flags(section, defaults)
    for key, value in updates.items():
        if key not in defaults:
            raise ValueError(f"Unknown {noun} '{key}'")
        if not isinstance(value, bool):
            raise ValueError(f"'{key}' must be true or false")
        merged[key] = value
    return merged


def _persist_flags(section: str, merged: dict) -> None:
    data = _load_server_file()
    data[section] = merged
    save_server_config(data)


def _defaults(catalog: list) -> dict:
    return {e["key"]: e["default"] for e in catalog}


def _keys(catalog: list, group: str) -> list:
    return [e["key"] for e in catalog if e["group"] == group]


# ── Main-app tabs ─────────────────────────────────────────────────────────────
# DOM ids follow a convention so the frontend needs no separate map:
#   group "main"   → #navbtn-<key>
#   group "config" → key "cfg_<name>" → #ctab-<name>

UI_TAB_CATALOG = [
    {"key": "executor",     "label": "Executor",        "group": "main",   "default": True},
    {"key": "repository",   "label": "Repository",      "group": "main",   "default": True},
    {"key": "dashboard",    "label": "Reporting Dashboard", "group": "main", "default": True},
    {"key": "grid",         "label": "Grid",            "group": "main",   "default": True},
    {"key": "zephyr",       "label": "Test Management", "group": "main",   "default": True},
    {"key": "testdata",     "label": "Test Data",       "group": "main",   "default": True},
    {"key": "filemanager",  "label": "File Manager (admins only)", "group": "main", "default": True},
    {"key": "insights",     "label": "Jira Insights",   "group": "main",   "default": False},
    {"key": "tools",        "label": "Config",          "group": "main",   "default": True},
    {"key": "cfg_features", "label": "Features",        "group": "config", "default": True},
    {"key": "cfg_repo",     "label": "Repo",            "group": "config", "default": True},
    {"key": "cfg_git",      "label": "Git",             "group": "config", "default": True},
    {"key": "cfg_tools",    "label": "CLI Tools",       "group": "config", "default": True},
    {"key": "cfg_allure",   "label": "Allure",          "group": "config", "default": True},
    {"key": "cfg_jfrog",    "label": "JFrog",           "group": "config", "default": True},
    {"key": "cfg_mapping",  "label": "Field Mapping",   "group": "config", "default": True},
    {"key": "cfg_zephyr",   "label": "Zephyr",          "group": "config", "default": True},
]
_DEFAULT_UI_TABS = _defaults(UI_TAB_CATALOG)
# Tabs only admins ever get (their endpoints are admin-only too). Ticking one in Tab Visibility never
# shows it to a regular user, and it can't be the landing tab or the only visible tab for them.
ADMIN_ONLY_TABS = ("filemanager",)


def load_ui_tabs() -> dict:
    """Effective tab visibility: saved values over defaults; unknown keys dropped."""
    return _load_flags("ui_tabs", _DEFAULT_UI_TABS)


def load_default_tab(is_admin: bool = True) -> str:
    """The tab a user lands on: the admin's choice while it is still visible to them, otherwise the
    first main tab they can see (Executor unless an admin hid it). Regular users never get an
    admins-only tab, so for them a choice like File Manager falls back to the first other tab."""
    tabs = load_ui_tabs()
    visible = [k for k in _keys(UI_TAB_CATALOG, "main") if tabs[k] and (is_admin or k not in ADMIN_ONLY_TABS)]
    saved = _load_server_file().get("default_tab")
    return saved if saved in visible else (visible[0] if visible else "executor")


def save_ui_tabs(tabs: dict, default_tab: str | None = None) -> dict:
    """Merge `tabs` into the saved visibility and persist it — together with the landing tab when
    one is given, so the two can never disagree. Raises ValueError on bad input (nothing is saved)."""
    merged = _merge_flags("ui_tabs", _DEFAULT_UI_TABS, tabs, "tab")
    main = _keys(UI_TAB_CATALOG, "main")
    if not any(merged[k] for k in main if k not in ADMIN_ONLY_TABS):
        raise ValueError("At least one main tab must stay visible for regular users "
                         "(File Manager is admins only and doesn't count)")
    if merged["tools"] and not any(merged[k] for k in _keys(UI_TAB_CATALOG, "config")):
        raise ValueError("Config is visible but every Config sub-tab is hidden — hide Config instead")
    if default_tab is not None:
        labels = {e["key"]: e["label"] for e in UI_TAB_CATALOG if e["group"] == "main"}
        if not isinstance(default_tab, str) or default_tab not in labels:
            raise ValueError(f"'{default_tab}' is not a main tab")
        if not merged[default_tab]:
            raise ValueError(f"'{labels[default_tab]}' is hidden — pick a visible tab as the landing tab")
    data = _load_server_file()
    data["ui_tabs"] = merged
    if default_tab is not None:
        data["default_tab"] = default_tab
    save_server_config(data)
    return merged


# ── Jira Insights options ─────────────────────────────────────────────────────
# group "sidebar": the sidebar sections (keys src_* are the ways to create a dataset).
# group "views":   the view switcher along the top of the Insights page.

INSIGHTS_OPTION_CATALOG = [
    {"key": "src_jql",         "label": "New dataset from JQL",              "group": "sidebar", "default": True},
    {"key": "src_zephyr",      "label": "New dataset from Zephyr (test runs)", "group": "sidebar", "default": True},
    {"key": "src_sprint",      "label": "New dataset from Sprint",           "group": "sidebar", "default": True},
    {"key": "src_upload",      "label": "Upload Jira export (CSV / Excel)",  "group": "sidebar", "default": True},
    {"key": "excluded_fields", "label": "Excluded fields",                   "group": "sidebar", "default": True},
    {"key": "colour_theme",    "label": "Colour theme",                      "group": "sidebar", "default": True},
    {"key": "view_dashboard",  "label": "Dashboard",                         "group": "views",   "default": True},
    {"key": "view_sprint",     "label": "Sprint Reports",                    "group": "views",   "default": True},
    {"key": "view_builder",    "label": "Report Builder",                    "group": "views",   "default": True},
    {"key": "view_explorer",   "label": "Explorer",                          "group": "views",   "default": True},
    {"key": "view_data",       "label": "Data",                              "group": "views",   "default": True},
]
_DEFAULT_INSIGHTS_OPTIONS = _defaults(INSIGHTS_OPTION_CATALOG)
_INSIGHTS_SOURCE_KEYS = [k for k in _DEFAULT_INSIGHTS_OPTIONS if k.startswith("src_")]


def load_insights_options() -> dict:
    """Effective Jira Insights option visibility: saved values over defaults."""
    return _load_flags("insights_options", _DEFAULT_INSIGHTS_OPTIONS)


def save_insights_options(options: dict) -> dict:
    """Merge `options` into the saved set and persist it. Raises ValueError on bad input."""
    merged = _merge_flags("insights_options", _DEFAULT_INSIGHTS_OPTIONS, options, "option")
    if not any(merged[k] for k in _INSIGHTS_SOURCE_KEYS):
        raise ValueError("At least one way to create a dataset must stay enabled "
                         "(JQL, Zephyr, Sprint or Upload)")
    if not any(merged[k] for k in _keys(INSIGHTS_OPTION_CATALOG, "views")):
        raise ValueError("At least one view must stay enabled")
    _persist_flags("insights_options", merged)
    return merged
