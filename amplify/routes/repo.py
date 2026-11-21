"""
Blueprint: Repository management routes (clone, pull, branch, venv, pip) — the Repository tab and the Executor.

Repos live per user in <workspace>/repos/<name> (see amplify/repo_store.py for where that is and how access
tokens are kept: saved server-side, never returned to a browser, never written into a repo's .git/config).

  GET  /api/repo/workspace     — where the repos live, every clone with its remote/branch/last commit, saved repos
  POST /api/repo/use           — make one of the user's clones the active repo {name}
  POST /api/repo/clone         — clone a git repo into the user's workspace (or pull it if it is already there)
  POST /api/repo/pull          — pull the active repo (or {name})
  GET  /api/repo/branches      — branches of the active repo (fetched first)
  POST /api/repo/branches      — branches of a remote {url[, token]} before cloning it (token never in a URL)
  POST /api/repo/checkout      — switch the active repo to a branch
  GET  /api/repo/status        — active repo + venv status
  POST /api/repo/token         — set or remove the access token of a saved repo {name, token | clear_token}
  POST /api/repo/remove        — delete a clone from the workspace {name}
  POST /api/repo/venv/create   — create .venv in the active repo
  POST /api/repo/venv/install  — pip install -r requirements.txt
  GET  /api/repo/configs       — saved repos (has_token, never the token)
  POST /api/repo/configs       — save a repo {name, url, branch[, token]} (an empty token keeps the saved one)
  DELETE /api/repo/configs     — delete a saved repo {name}
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from flask import Blueprint, jsonify, request, g

from amplify import repo_store as rs
from amplify.auth_utils import require_auth
from amplify.config import settings
from amplify.models.user import ActivityLog
from amplify.request_body import json_object
from amplify.user_config_reader import UserConfigReader
from amplify.routes import state

bp = Blueprint("repo", __name__)


def _text(body: dict, key: str) -> str:
    value = body.get(key)
    return value.strip() if isinstance(value, str) else ""


def _active_repo() -> str:
    repo = UserConfigReader(g.user.id).load().get("repo_root", "")
    return repo.strip() if isinstance(repo, str) else ""


def _set_active(path: Path | str) -> None:
    reader = UserConfigReader(g.user.id)
    cfg = reader.load()
    cfg["repo_root"] = str(path)
    cfg.setdefault("venv_path", ".venv")
    reader.save(cfg)


def _token_for_repo(path: str | Path) -> str:
    return rs.token_for(g.user.id, rs.remote_url(Path(path)), Path(path).name)


def _location() -> dict:
    return {"path": str(rs.repos_dir(g.user.id)), "scope": "user",
            "storage": f"Amplify's data folder ({settings.WORKSPACES_ROOT.parent}): the amplify-data volume locally, "
                       "the amplify-data-pvc PVC in OpenShift. Each user has their own folder; runner pods never "
                       "see it (a Grid run sends them a snapshot of the committed code)."}


@bp.route("/api/repo/workspace")
@require_auth
def repo_workspace():
    rs.migrate_embedded_credentials(g.user.id)
    active = _active_repo()
    clones = [rs.describe(g.user.id, p, active=str(p) == active) for p in rs.list_clones(g.user.id)]
    cloned_names = {c["name"] for c in clones}
    saved = [dict(rs.public(c), cloned=c["name"] in cloned_names) for c in rs.load_configs(g.user.id)]
    return jsonify({"location": _location(), "active": active, "repos": clones, "saved": saved})


@bp.route("/api/repo/use", methods=["POST"])
@require_auth
def use_repo():
    body, bad = json_object()
    if bad:
        return bad
    path = rs.clone_by_name(g.user.id, body.get("name"))
    if path is None:
        return jsonify({"error": "No such repository in your workspace"}), 404
    _set_active(path)
    ActivityLog.record(g.user.id, "repo_use", f"Active repo: {path.name}", request.remote_addr)
    return jsonify({"ok": True, **rs.describe(g.user.id, path, active=True)})


@bp.route("/api/repo/clone", methods=["POST"])
@require_auth
def clone_repo():
    body, bad = json_object()
    if bad:
        return bad
    url, branch, token, name = _text(body, "url"), _text(body, "branch"), _text(body, "token"), _text(body, "name")
    if not url and name:                                  # a saved repo, by name: its URL and token stay server-side
        saved = next((c for c in rs.load_configs(g.user.id) if c["name"] == name), None)
        if saved is None:
            return jsonify({"error": "No saved repository by that name"}), 404
        url, branch = saved["url"], branch or saved.get("branch", "")
    if not url:
        return jsonify({"error": "Repository URL required"}), 400
    url, embedded = rs.split_credentials(rs.normalize_url(url))
    if not url.lower().startswith(("https://", "http://", "ssh://", "git@")):
        return jsonify({"error": "Use an https:// (or ssh) Git URL"}), 400
    token = token or embedded or rs.token_for(g.user.id, url, name)
    name = name or url.rstrip("/").split("/")[-1].removesuffix(".git")
    repo_dir = rs.repo_path(g.user.id, name)

    if repo_dir.exists() and (repo_dir / ".git").is_dir():
        rs.run_git(["remote", "set-url", "origin", url], repo_dir)       # never a token in .git/config
        code, out = rs.run_git(["pull"], repo_dir, token, url)
        if code != 0:
            return jsonify({"error": f"Pull failed: {out}"}), 500
        if branch:
            code, out = rs.run_git(["checkout", branch], repo_dir)
            if code != 0:
                return jsonify({"error": f"Checkout failed: {out}"}), 500
        rs.upsert(g.user.id, repo_dir.name, url, branch=branch or None, token=token or None)
        _set_active(repo_dir)
        ActivityLog.record(g.user.id, "repo_pull", f"Pulled {repo_dir.name}", request.remote_addr)
        return jsonify({"ok": True, "action": "pulled", "name": repo_dir.name, "path": str(repo_dir), "output": out})
    if repo_dir.exists():
        import shutil
        shutil.rmtree(repo_dir, ignore_errors=True)       # a leftover that is not a git repo

    cmd = ["clone", "--progress"] + (["--branch", branch] if branch else []) + [url, str(repo_dir)]
    code, out = rs.run_git(cmd, rs.repos_dir(g.user.id), token, url, timeout=300)
    if code != 0:
        return jsonify({"error": f"Clone failed: {out}"}), 500
    rs.upsert(g.user.id, repo_dir.name, url, branch=branch, token=token or None)
    _set_active(repo_dir)
    ActivityLog.record(g.user.id, "repo_clone", f"Cloned {repo_dir.name} from {url}", request.remote_addr)
    return jsonify({"ok": True, "action": "cloned", "name": repo_dir.name, "path": str(repo_dir), "output": out}), 201


@bp.route("/api/repo/pull", methods=["POST"])
@require_auth
def pull_repo():
    body = request.get_json(silent=True) if request.data else {}
    name = body.get("name") if isinstance(body, dict) else None
    repo = rs.clone_by_name(g.user.id, name) if name else (Path(_active_repo()) if _active_repo() else None)
    if repo is None or not repo.exists():
        return jsonify({"error": "No repo configured"}), 400
    code, out = rs.run_git(["pull"], repo, _token_for_repo(repo), rs.remote_url(repo))
    if code != 0:
        return jsonify({"error": f"Pull failed: {out}"}), 500
    ActivityLog.record(g.user.id, "repo_pull", repo.name, request.remote_addr)
    return jsonify({"ok": True, "output": out})


def _branch_names(out: str) -> list[str]:
    return sorted({b.removeprefix("origin/") for b in out.splitlines() if b and "HEAD" not in b and b != "origin"})


@bp.route("/api/repo/branches", methods=["GET", "POST"])
@require_auth
def list_branches():
    if request.method == "POST":                          # a remote before cloning; the token travels in the body
        body, bad = json_object()
        if bad:
            return bad
        url = _text(body, "url")
        if not url:
            return jsonify({"error": "url required"}), 400
        url, embedded = rs.split_credentials(rs.normalize_url(url))
        token = _text(body, "token") or embedded or rs.token_for(g.user.id, url)
        code, out = rs.run_git(["ls-remote", "--heads", url], "/tmp", token, url, timeout=30)
        if code != 0:
            return jsonify({"error": f"Could not list branches: {out}"}), 500
        return jsonify({"branches": sorted(l.split("refs/heads/")[-1] for l in out.splitlines() if "refs/heads/" in l),
                        "current": ""})
    if request.args.get("url"):
        return jsonify({"error": "Send the URL (and token) in a POST body, not in the address"}), 400
    repo = _active_repo()
    if not repo or not Path(repo).exists():
        return jsonify({"branches": [], "current": ""})
    rs.run_git(["fetch", "--prune"], repo, _token_for_repo(repo), rs.remote_url(Path(repo)), timeout=60)
    code, out = rs.run_git(["branch", "-a", "--format=%(refname:short)"], repo)
    if code != 0:
        return jsonify({"error": out}), 500
    code2, current = rs.run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo)
    return jsonify({"branches": _branch_names(out), "current": current.strip() if code2 == 0 else ""})


@bp.route("/api/repo/checkout", methods=["POST"])
@require_auth
def checkout_branch():
    body = request.json or {}
    branch = (body.get("branch") or "").strip() if isinstance(body.get("branch"), str) else ""
    if not branch or branch.startswith("-"):
        return jsonify({"error": "Branch name required"}), 400
    repo = _active_repo()
    if not repo or not Path(repo).exists():
        return jsonify({"error": "No repo configured"}), 400
    code, out = rs.run_git(["checkout", branch, "--"], repo)
    if code != 0:
        code, out = rs.run_git(["checkout", "-b", branch, f"origin/{branch}", "--"], repo)
    if code != 0:
        return jsonify({"error": f"Checkout failed: {out}"}), 500
    saved = next((c for c in rs.load_configs(g.user.id) if c["name"] == Path(repo).name), None)
    if saved:
        rs.upsert(g.user.id, saved["name"], saved["url"], branch=branch)
    ActivityLog.record(g.user.id, "repo_checkout", f"Switched to {branch}", request.remote_addr)
    return jsonify({"ok": True, "branch": branch, "output": out})


@bp.route("/api/repo/status")
@require_auth
def repo_status():
    rs.migrate_embedded_credentials(g.user.id)
    cfg = UserConfigReader(g.user.id).load()
    repo = _active_repo()
    result = {"has_repo": False, "repo_path": "", "repo_name": "", "branch": "", "has_venv": False,
              "venv_path": "", "python": "", "requirements": [], "remote": "", "has_token": False,
              "head": {}, "dirty": False, "location": _location()}
    if not repo or not Path(repo).exists():
        return jsonify(result)
    info = rs.describe(g.user.id, Path(repo), active=True)
    result.update(has_repo=True, repo_path=repo, repo_name=Path(repo).name, branch=info["branch"],
                  remote=info["remote"], has_token=info["has_token"], head=info["head"], dirty=info["dirty"])
    venv_dir = Path(repo) / cfg.get("venv_path", ".venv")
    if venv_dir.exists():
        result["has_venv"] = True
        result["venv_path"] = str(venv_dir)
        python = venv_dir / "bin" / "python"
        if not python.exists():
            python = venv_dir / "Scripts" / "python.exe"
        result["python"] = str(python) if python.exists() else ""
    result["requirements"] = [n for n in ["requirements.txt", "requirements/base.txt", "requirements/dev.txt",
                                          "requirements-dev.txt"] if (Path(repo) / n).exists()]
    return jsonify(result)


@bp.route("/api/repo/token", methods=["POST"])
@require_auth
def set_repo_token():
    body, bad = json_object()
    if bad:
        return bad
    name, token, clear = _text(body, "name"), _text(body, "token"), body.get("clear_token") is True
    saved = next((c for c in rs.load_configs(g.user.id) if c["name"] == name), None)
    if saved is None:
        clone = rs.clone_by_name(g.user.id, name)
        if clone is None:
            return jsonify({"error": "No such repository"}), 404
        saved = {"name": name, "url": rs.split_credentials(rs.remote_url(clone))[0]}
    if not token and not clear:
        return jsonify({"error": "Give a token, or clear_token: true"}), 400
    entry = rs.upsert(g.user.id, name, saved["url"], token=token or None, clear_token=clear)
    ActivityLog.record(g.user.id, "repo_token", f"{'Removed' if clear else 'Set'} the access token of {name}",
                       request.remote_addr)
    return jsonify({"ok": True, **rs.public(entry)})


@bp.route("/api/repo/remove", methods=["POST"])
@require_auth
def remove_repo():
    body, bad = json_object()
    if bad:
        return bad
    name = _text(body, "name")
    path = rs.clone_by_name(g.user.id, name)
    if path is None:
        return jsonify({"error": "No such repository in your workspace"}), 404
    was_active = str(path) == _active_repo()
    rs.remove_clone(g.user.id, name)
    if was_active:
        _set_active("")
    ActivityLog.record(g.user.id, "repo_remove", f"Removed the clone of {name}", request.remote_addr)
    return jsonify({"ok": True, "was_active": was_active})


@bp.route("/api/repo/venv/create", methods=["POST"])
@require_auth
def create_venv():
    cfg = UserConfigReader(g.user.id).load()
    repo = cfg.get("repo_root", "").strip()
    if not repo or not Path(repo).exists():
        return jsonify({"error": "No repo configured"}), 400

    venv_path = cfg.get("venv_path", ".venv")
    venv_dir = Path(repo) / venv_path

    if venv_dir.exists():
        return jsonify({"ok": True, "message": "Venv already exists", "path": str(venv_dir)})

    try:
        proc = subprocess.run(
            [sys.executable, "-m", "venv", str(venv_dir)],
            capture_output=True, text=True, timeout=120
        )
        if proc.returncode != 0:
            return jsonify({"error": f"venv creation failed: {proc.stderr}"}), 500
    except subprocess.TimeoutExpired:
        return jsonify({"error": "venv creation timed out"}), 500

    jfrog_url = cfg.get("jfrog_url", "").strip()
    jfrog_repo = cfg.get("jfrog_repo", "").strip()
    jfrog_email = cfg.get("jfrog_email", "").strip()
    jfrog_token = cfg.get("jfrog_token", "").strip()

    if jfrog_url and jfrog_email and jfrog_token:
        import re
        if not jfrog_url.startswith(("http://", "https://")):
            jfrog_url = "https://" + jfrog_url
        hostname = re.sub(r"^https?://", "", jfrog_url)
        index_url = f"https://{jfrog_email}:{jfrog_token}@{hostname}/artifactory/api/pypi/{jfrog_repo or 'pypi-remote'}/simple"
        pip_content = f"[global]\nindex-url = {index_url}\n"
        pip_ini = venv_dir / "pip.ini"
        pip_ini.write_text(pip_content, encoding="utf-8")

    ActivityLog.record(g.user.id, "venv_create", f"Created venv at {venv_path}", request.remote_addr)
    return jsonify({"ok": True, "path": str(venv_dir)}), 201


@bp.route("/api/repo/venv/install", methods=["POST"])
@require_auth
def install_deps():
    """Stream pip install output via SSE."""
    cfg = UserConfigReader(g.user.id).load()
    repo = cfg.get("repo_root", "").strip()
    if not repo or not Path(repo).exists():
        return jsonify({"error": "No repo configured"}), 400

    body = request.json or {}
    req_file = (body.get("requirements_file") or "").strip()
    if not req_file:
        for name in ["requirements.txt", "requirements/base.txt", "requirements/dev.txt"]:
            if (Path(repo) / name).exists():
                req_file = name
                break
    if not req_file:
        return jsonify({"error": "No requirements file found"}), 400

    venv_path = cfg.get("venv_path", ".venv")
    venv_dir = Path(repo) / venv_path
    python = venv_dir / "bin" / "python"
    if not python.exists():
        python = venv_dir / "Scripts" / "python.exe"
    if not python.exists():
        return jsonify({"error": "No venv found — create one first"}), 400

    from amplify.command_builder import venv_env_overrides
    from amplify.routes.executor import _read_pip_ini_flags

    pip_flags = _read_pip_ini_flags(str(python))
    cmd = [str(python), "-u", "-m", "pip", "install", "--isolated"] + pip_flags + ["-r", str(Path(repo) / req_file)]
    display = " ".join(cmd)

    state.broadcast("cmd", display)
    state._is_running = True

    from amplify.runner import TestRunner

    def on_output(line):
        state.broadcast("line", line)

    def on_finish(exit_code, cancelled):
        state._is_running = False
        status = "cancelled" if cancelled else ("passed" if exit_code == 0 else f"failed:{exit_code}")
        state.broadcast("status", status)
        state.broadcast("done", "")

    try:
        state._runner = TestRunner(
            cmd=cmd, cwd=repo, env_overrides=venv_env_overrides(str(python)),
            on_output=on_output, on_finish=on_finish,
        )
        state._runner.start()
    except Exception as exc:
        state._is_running = False
        return jsonify({"error": str(exc)}), 500

    ActivityLog.record(g.user.id, "pip_install", req_file, request.remote_addr)
    return jsonify({"ok": True, "command": display})


@bp.route("/api/repo/configs")
@require_auth
def list_repo_configs():
    rs.migrate_embedded_credentials(g.user.id)
    return jsonify([rs.public(c) for c in rs.load_configs(g.user.id)])


@bp.route("/api/repo/configs", methods=["POST"])
@require_auth
def save_repo_config():
    body, bad = json_object()
    if bad:
        return bad
    name, url, branch, token = _text(body, "name"), _text(body, "url"), _text(body, "branch"), _text(body, "token")
    if not name or not url:
        return jsonify({"error": "name and url required"}), 400
    entry = rs.upsert(g.user.id, name, url, branch=branch, token=token or None)   # an empty token keeps the saved one
    return jsonify({"ok": True, **rs.public(entry)})


@bp.route("/api/repo/configs", methods=["DELETE"])
@require_auth
def delete_repo_config():
    body = request.get_json(silent=True) or {}
    name = _text(body, "name") if isinstance(body, dict) else ""
    if not name:
        return jsonify({"error": "name required"}), 400
    rs.delete_config(g.user.id, name)
    return jsonify({"ok": True})
