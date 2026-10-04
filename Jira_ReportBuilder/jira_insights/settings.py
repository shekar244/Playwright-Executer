"""
Jira Report Builder settings — Jira / Zephyr connection and workspace location.

The connection comes from one of two sources, chosen under 🔌 Jira / Zephyr config:

  app   — values typed into the sidebar, saved to <workspace>/jira_connection.json
          (owner-only permissions)
  file  — an external credentials file the user points to by path, re-read on
          every use and never copied into the workspace:
            • config.ini / .cfg / .conf  — [zephyr] or [jira] section (see README)
            • Amplify QEA config.json    — its "zephyr" section

Environment variables win over both, which lets containers inject secrets:

  JIRA_URL, JIRA_USERNAME, JIRA_API_TOKEN, JIRA_VERIFY_SSL
  ZEPHYR_ACCESS_KEY, ZEPHYR_SECRET_KEY, ZEPHYR_ACCOUNT_ID
  JIRA_INSIGHTS_HOME   — workspace directory (default: <app folder>/workspace)
"""
from __future__ import annotations

import configparser
import json
import os
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_WORKSPACE = APP_ROOT / "workspace"

CREDENTIAL_SUFFIXES = (".ini", ".cfg", ".conf", ".json")
_MAX_FILE_BYTES = 1_000_000

# setting field → accepted key names in a credentials file (Amplify QEA names first)
_ALIASES = {
    "url": ("jira_url", "url", "base_url", "server"),
    "username": ("username", "user", "email", "jira_username", "jira_user"),
    "token": ("api_token", "token", "jira_api_token", "jira_token", "pat", "password"),
    "verify_ssl": ("verify_ssl", "verify", "ssl_verify"),
    "zephyr_access_key": ("access_key", "zephyr_access_key", "zapi_access_key"),
    "zephyr_secret_key": ("secret_key", "zephyr_secret_key", "zapi_secret_key"),
    "zephyr_account_id": ("account_id", "zephyr_account_id", "atlassian_account_id"),
    "project_key": ("project_key", "project"),
}
_ENV = {
    "url": "JIRA_URL", "username": "JIRA_USERNAME", "token": "JIRA_API_TOKEN",
    "verify_ssl": "JIRA_VERIFY_SSL", "zephyr_access_key": "ZEPHYR_ACCESS_KEY",
    "zephyr_secret_key": "ZEPHYR_SECRET_KEY", "zephyr_account_id": "ZEPHYR_ACCOUNT_ID",
}


@dataclass(frozen=True)
class JiraSettings:
    url: str = ""
    username: str = ""        # empty → Bearer auth (Jira Data Center PAT)
    token: str = ""
    verify_ssl: bool = True
    zephyr_access_key: str = ""
    zephyr_secret_key: str = ""
    zephyr_account_id: str = ""
    project_key: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.url and self.token)

    @property
    def zephyr_configured(self) -> bool:
        return bool(self.zephyr_access_key and self.zephyr_secret_key and self.zephyr_account_id)


@dataclass(frozen=True)
class Connection:
    settings: JiraSettings
    source: str = "app"                       # "app" | "file"
    file_path: str = ""
    file_section: str = ""                    # "" = auto
    error: str = ""                           # why the credentials file couldn't be used
    found: tuple = ()                         # setting fields present in the file
    sections: tuple = ()                      # sections available in the file
    overrides: tuple = field(default=())      # JIRA_* / ZEPHYR_* env vars in effect


def workspace_dir() -> Path:
    return Path(os.environ.get("JIRA_INSIGHTS_HOME") or _DEFAULT_WORKSPACE)


def connection_file() -> Path:
    return workspace_dir() / "jira_connection.json"


def _truthy(value) -> bool:
    return value if isinstance(value, bool) else str(value).strip().lower() in ("1", "true", "yes", "on")


def _saved() -> dict:
    try:
        data = json.loads(connection_file().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _from_mapping(values: dict) -> JiraSettings:
    kwargs = {}
    for f in fields(JiraSettings):
        raw = values.get(f.name)
        if raw is None:
            continue
        kwargs[f.name] = _truthy(raw) if f.name == "verify_ssl" else str(raw).strip()
    if "url" in kwargs:
        kwargs["url"] = kwargs["url"].rstrip("/")
    return JiraSettings(**kwargs)


# ── Credentials file ──────────────────────────────────────────────────────────

def resolve_credentials_path(raw: str) -> Path:
    """User-supplied path → absolute file path, or ValueError with a readable reason."""
    text = (raw or "").strip().strip('"').strip("'")
    if not text:
        raise ValueError("Enter the path to your credentials file.")
    path = Path(os.path.expandvars(os.path.expanduser(text))).resolve()
    if path.suffix.lower() not in CREDENTIAL_SUFFIXES:
        raise ValueError(f"Use a {', '.join(CREDENTIAL_SUFFIXES)} file — got '{path.name}'.")
    if not path.is_file():
        raise ValueError(f"File not found: {path}")
    if path.stat().st_size > _MAX_FILE_BYTES:
        raise ValueError(f"{path.name} is larger than 1 MB — is this the right file?")
    return path


def _file_sections(path: Path) -> dict[str, dict]:
    """{section name (lower-case): {key (lower-case): value}} — DEFAULT folded into every section."""
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"{path.name} must contain a JSON object.")
        sections = {k.lower(): {kk.lower(): vv for kk, vv in v.items()}
                    for k, v in data.items() if isinstance(v, dict)}
        top = {k.lower(): v for k, v in data.items() if not isinstance(v, (dict, list))}
        if top:
            sections.setdefault("default", top)
        return sections
    parser = configparser.ConfigParser(interpolation=None)    # tokens often contain '%'
    parser.read_string(path.read_text(encoding="utf-8"), source=str(path))
    sections = {name.lower(): {k.lower(): v for k, v in parser.items(name)} for name in parser.sections()}
    if parser.defaults():
        sections.setdefault("default", {k.lower(): v for k, v in parser.defaults().items()})
    return sections


def read_credentials_file(raw_path: str, section: str = "") -> tuple[JiraSettings, tuple, tuple]:
    """(settings, fields found, sections available). Raises ValueError for unusable files."""
    path = resolve_credentials_path(raw_path)
    try:
        sections = _file_sections(path)
    except (configparser.Error, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"Could not parse {path.name}: {exc}") from None
    if section and section.lower() not in sections:
        raise ValueError(f"Section [{section}] not found in {path.name}.")
    # Auto: [jira] wins for Jira keys, [zephyr] next (Amplify QEA layout), then DEFAULT and the rest.
    order = [section.lower()] if section else (
        [s for s in ("jira", "zephyr", "default") if s in sections]
        + [s for s in sections if s not in ("jira", "zephyr", "default")])
    values: dict = {}
    for name, aliases in _ALIASES.items():
        for sec in order:
            hit = next((sections[sec][a] for a in aliases if sections[sec].get(a) not in (None, "")), None)
            if hit is not None:
                values[name] = hit
                break
    if not any(values.get(k) for k in ("url", "token", "zephyr_access_key")):
        raise ValueError(f"No Jira or Zephyr credentials found in {path.name} — expected keys like "
                         "jira_url, username, api_token (see the example).")
    return _from_mapping(values), tuple(values), tuple(sections)


# ── Load / save ───────────────────────────────────────────────────────────────

def _apply_env(settings: JiraSettings) -> tuple[JiraSettings, tuple]:
    present = {name: os.environ[var] for name, var in _ENV.items() if os.environ.get(var) is not None}
    if not present:
        return settings, ()
    merged = {**asdict(settings), **present}
    return _from_mapping(merged), tuple(_ENV[n] for n in present)


def load_connection() -> Connection:
    saved = _saved()
    source = "file" if saved.get("source") == "file" else "app"
    path, section = str(saved.get("file_path", "")), str(saved.get("file_section", ""))
    error, found, sections = "", (), ()
    if source == "file":
        try:
            base, found, sections = read_credentials_file(path, section)
        except (ValueError, OSError) as exc:
            base, error = JiraSettings(), str(exc)
    else:
        base = saved_app_settings()
    settings, overrides = _apply_env(base)
    return Connection(settings, source, path, section, error, found, sections, overrides)


def load_jira_settings() -> JiraSettings:
    return load_connection().settings


def saved_app_settings() -> JiraSettings:
    """Values typed into the app (never the credentials file's) — used to pre-fill the form."""
    saved = _saved()
    return _from_mapping({**{"verify_ssl": True}, **saved.get("app", saved)})


def with_env(settings: JiraSettings) -> JiraSettings:
    """Apply environment overrides — what the app would actually use with these settings."""
    return _apply_env(settings)[0]


def _write(payload: dict) -> None:
    path = connection_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Create owner-only before writing so a token is never world-readable.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    os.chmod(path, 0o600)


def save_jira_settings(settings: JiraSettings) -> None:
    """Use values entered in the app (switches the source to "app")."""
    clean = replace(settings, url=settings.url.strip().rstrip("/"), username=settings.username.strip(),
                    token=settings.token.strip())
    previous = _saved()
    _write({"source": "app", "app": asdict(clean),
            "file_path": previous.get("file_path", ""), "file_section": previous.get("file_section", "")})


def save_credentials_file(raw_path: str, section: str = "") -> None:
    """Use an external credentials file (switches the source to "file"); only the path is stored."""
    resolve_credentials_path(raw_path)                     # fail fast on a bad path
    previous = _saved()
    _write({"source": "file", "file_path": raw_path.strip(), "file_section": section,
            "app": previous.get("app", {k: v for k, v in previous.items()
                                        if k not in ("source", "file_path", "file_section")})})
