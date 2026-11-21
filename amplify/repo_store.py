"""Git repositories in a user's workspace, and the access tokens (PATs) that reach their remotes.

Where they live: <WORKSPACES_ROOT>/<user id>/repos/<name> — per user, inside Amplify's data folder (/app/data: the
amplify-data volume locally, the amplify-data-pvc PVC in OpenShift). The repo a user works with is `repo_root` in
their config; the Executor runs it and the Grid snapshots its committed state for the runner pods.

Tokens: saved per repo in <workspace>/repo_configs.json (mode 0600) and never sent back to a browser (`has_token`
only). They are never written into a repo's .git/config either — tests run inside that folder and could read it.
A git command that talks to the remote gets the token for that one call: an inline credential helper reads it from
the AMPLIFY_GIT_TOKEN environment variable of that git process. Clones made before this kept the token in the remote
URL; migrate_embedded_credentials() moves it into the saved settings and cleans the URL.

Certificate check: off for a saved repo with ssl_verify: false (its git server presents a certificate this pod cannot
verify — a corporate CA that cannot be installed in the image). Remote git commands for that repo then run with
`-c http.sslVerify=false`: the connection is still encrypted but no longer authenticated, so anyone able to intercept
it could read the PAT. Prefer giving git the CA instead: GIT_SSL_CAINFO=<a CA file, e.g. a mounted ConfigMap> on the
deployment (git reads it itself; no install needed). GIT_SSL_NO_VERIFY=true there turns the check off for every repo.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.parse
from pathlib import Path

from amplify.storage import get_user_workspace_dir

_USERINFO = re.compile(r"^(https?://)([^/@]*)@(.+)$", re.IGNORECASE)
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
GIT_TIMEOUT = 120


# ── where things are ─────────────────────────────────────────────────────────

def repos_dir(user_id: int) -> Path:
    d = get_user_workspace_dir(user_id) / "repos"
    d.mkdir(parents=True, exist_ok=True)
    return d


def repo_path(user_id: int, name: str) -> Path:
    safe = name.replace("/", "_").replace("\\", "_").replace("..", "_").strip("._")
    return repos_dir(user_id) / safe


def clone_by_name(user_id: int, name) -> Path | None:
    """The clone called `name` in this user's workspace, or None (unknown, or a name that is not a plain name)."""
    if not isinstance(name, str) or not _NAME.match(name):
        return None
    path = repos_dir(user_id) / name
    return path if (path / ".git").is_dir() and path.resolve().parent == repos_dir(user_id).resolve() else None


def list_clones(user_id: int) -> list[Path]:
    return sorted((p for p in repos_dir(user_id).iterdir() if (p / ".git").is_dir()), key=lambda p: p.name.lower())


def _configs_path(user_id: int) -> Path:
    return get_user_workspace_dir(user_id) / "repo_configs.json"


# ── URLs and tokens ──────────────────────────────────────────────────────────

def normalize_url(url: str) -> str:
    url = url.strip().rstrip("/")
    return url if url.endswith(".git") else url + ".git"


def split_credentials(url: str) -> tuple[str, str | None]:
    """(the URL without user:password@, the token that was in it or None)."""
    m = _USERINFO.match(url or "")
    if not m:
        return url, None
    scheme, userinfo, rest = m.groups()
    user, _, password = userinfo.partition(":")
    token = urllib.parse.unquote(password or "") or None
    return f"{scheme}{rest}", token


def _same_remote(a: str, b: str) -> bool:
    key = lambda u: split_credentials(normalize_url(u))[0].lower()
    return bool(a and b) and key(a) == key(b)


def git_username(url: str) -> str:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if "gitlab" in host:
        return "oauth2"
    if "bitbucket" in host:
        return "x-token-auth"
    return "x-access-token"                       # GitHub (and GitHub Enterprise) take any user with a PAT


def credential_args(url: str) -> list[str]:
    """git options that answer its credential request from AMPLIFY_GIT_TOKEN (and drop any other helper)."""
    user = git_username(url)                      # a fixed word from the list above, never user input
    helper = f'!f() {{ test "$1" = get || exit 0; echo username={user}; echo "password=$AMPLIFY_GIT_TOKEN"; }}; f'
    return ["-c", "credential.helper=", "-c", f"credential.helper={helper}"]


def git_env(token: str | None, url: str = "", ssl_verify: bool = True) -> dict:
    """Environment for a git command Amplify does not build itself (Config → Git): the same token hand-off and
    certificate choice as run_git, given through GIT_CONFIG_COUNT/KEY/VALUE (git 2.31+) instead of -c options."""
    pairs = [] if ssl_verify else [("http.sslVerify", "false")]
    env = {}
    if token:
        args = credential_args(url)                       # ["-c", "credential.helper=", "-c", "credential.helper=…"]
        pairs += [tuple(a.split("=", 1)) for a in args[1::2]]
        env["AMPLIFY_GIT_TOKEN"] = token
    if pairs:
        env["GIT_CONFIG_COUNT"] = str(len(pairs))
        for i, (key, value) in enumerate(pairs):
            env[f"GIT_CONFIG_KEY_{i}"], env[f"GIT_CONFIG_VALUE_{i}"] = key, value
    return env


def run_git(args: list[str], cwd, token: str | None = None, url: str = "", timeout: int = GIT_TIMEOUT,
            ssl_verify: bool = True) -> tuple[int, str]:
    """(exit code, output) of `git <args>`; with a token, git gets it for this call only, from its environment.
    ssl_verify=False skips the server certificate check for this call (see the module docstring)."""
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    prefix: list[str] = [] if ssl_verify else ["-c", "http.sslVerify=false"]
    if token:
        env["AMPLIFY_GIT_TOKEN"] = token
        prefix += credential_args(url)
    try:
        proc = subprocess.run(["git", *prefix, *args], cwd=str(cwd), capture_output=True, text=True,
                              timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return 1, "Git command timed out"
    except FileNotFoundError:
        return 1, "git is not installed"
    out = (proc.stdout + proc.stderr).strip()
    return proc.returncode, out.replace(token, "***") if token else out


def remote_url(path: Path) -> str:
    code, out = run_git(["config", "--get", "remote.origin.url"], path)
    return out.strip() if code == 0 else ""


# ── saved repository settings ────────────────────────────────────────────────

def load_configs(user_id: int) -> list[dict]:
    p = _configs_path(user_id)
    try:
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    except (OSError, ValueError):
        return []
    return [c for c in data if isinstance(c, dict) and isinstance(c.get("name"), str) and isinstance(c.get("url"), str)]


def save_configs(user_id: int, configs: list[dict]) -> None:
    p = _configs_path(user_id)
    fd, tmp = tempfile.mkstemp(prefix=".repo_configs-", dir=str(p.parent))   # 0600 from the start
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(configs, f, indent=2)
    os.replace(tmp, p)


def public(config: dict) -> dict:
    """What a browser may see of a saved repo: everything but the token."""
    return {"name": config.get("name", ""), "url": config.get("url", ""), "branch": config.get("branch", ""),
            "has_token": bool(config.get("token")), "ssl_verify": config.get("ssl_verify") is not False}


def upsert(user_id: int, name: str, url: str, branch: str | None = None, token: str | None = None,
           clear_token: bool = False, ssl_verify: bool | None = None) -> dict:
    """Save a repo's settings. The token is write-only: None keeps the saved one, clear_token removes it.
    ssl_verify None keeps the saved choice (checked, by default)."""
    url, embedded = split_credentials(normalize_url(url))
    token = token or embedded
    configs = load_configs(user_id)
    current = next((c for c in configs if c["name"] == name), None) or \
        next((c for c in configs if _same_remote(c["url"], url)), None) or {}
    entry = {"name": name, "url": url, "branch": current.get("branch", "") if branch is None else branch,
             "token": "" if clear_token else (token or current.get("token", "")),
             "ssl_verify": (current.get("ssl_verify") is not False) if ssl_verify is None else bool(ssl_verify)}
    configs = [c for c in configs if c is not current and c["name"] != name] + [entry]
    save_configs(user_id, configs)
    return entry


def delete_config(user_id: int, name: str) -> None:
    save_configs(user_id, [c for c in load_configs(user_id) if c["name"] != name])


def token_for(user_id: int, url: str = "", name: str = "") -> str:
    for c in load_configs(user_id):
        if (name and c["name"] == name) or _same_remote(c["url"], url):
            if c.get("token"):
                return c["token"]
    return ""


def ssl_verify_for(user_id: int, url: str = "", name: str = "") -> bool:
    """False only when the saved repo says to skip the certificate check."""
    for c in load_configs(user_id):
        if (name and c["name"] == name) or _same_remote(c["url"], url):
            return c.get("ssl_verify") is not False
    return True


def migrate_embedded_credentials(user_id: int) -> list[str]:
    """Move tokens that older clones kept in their remote URL into the saved settings; clean the URL. Idempotent."""
    moved = []
    for path in list_clones(user_id):
        url = remote_url(path)
        clean, token = split_credentials(url)
        if clean == url:
            continue
        if run_git(["remote", "set-url", "origin", clean], path)[0] != 0:
            continue
        branch = run_git(["rev-parse", "--abbrev-ref", "HEAD"], path)[1].strip()
        saved = token_for(user_id, clean, path.name)
        upsert(user_id, path.name, clean, branch=branch, token=None if saved else token)
        moved.append(path.name)
    return moved


# ── what the Repository tab shows ────────────────────────────────────────────

def describe(user_id: int, path: Path, active: bool = False) -> dict:
    url = split_credentials(remote_url(path))[0]
    code, branch = run_git(["rev-parse", "--abbrev-ref", "HEAD"], path)
    code2, head = run_git(["log", "-1", "--format=%H%x1f%s%x1f%an%x1f%cI"], path)
    sha, subject, author, date = (head.split("\x1f") + ["", "", "", ""])[:4] if code2 == 0 and head else ("",) * 4
    dirty = bool(run_git(["status", "--porcelain", "--untracked-files=no"], path)[1].strip())
    saved = next((c for c in load_configs(user_id) if c["name"] == path.name or _same_remote(c["url"], url)), None)
    return {"name": path.name, "path": str(path), "active": active, "remote": url,
            "branch": branch.strip() if code == 0 else "", "dirty": dirty,
            "head": {"sha": sha, "subject": subject, "author": author, "date": date},
            "saved": saved is not None, "has_token": bool(saved and saved.get("token")),
            "ssl_verify": not (saved and saved.get("ssl_verify") is False)}


def remove_clone(user_id: int, name: str) -> bool:
    path = clone_by_name(user_id, name)
    if path is None:
        return False
    shutil.rmtree(path)
    return True
