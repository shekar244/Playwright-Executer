"""
Blueprint: Repository management routes (clone, pull, branch, venv, pip)
  POST /api/repo/clone         — clone a git repo into user workspace
  POST /api/repo/pull          — pull latest from remote
  GET  /api/repo/branches      — list remote branches
  POST /api/repo/checkout      — switch branch
  GET  /api/repo/status        — repo + venv status
  POST /api/repo/venv/create   — create .venv in cloned repo
  POST /api/repo/venv/install  — pip install -r requirements.txt
  GET  /api/repo/configs       — list saved repo configs
  POST /api/repo/configs       — save a repo config
  DELETE /api/repo/configs     — delete a saved repo config
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from flask import Blueprint, Response, jsonify, request, g

from amplify.auth_utils import require_auth
from amplify.models.user import ActivityLog
from amplify.storage import get_user_workspace_dir
from amplify.user_config_reader import UserConfigReader
from amplify.routes import state

bp = Blueprint("repo", __name__)


def _repos_dir(user_id: int) -> Path:
    d = get_user_workspace_dir(user_id) / "repos"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _repo_path(user_id: int, name: str) -> Path:
    safe = name.replace("/", "_").replace("\\", "_").replace("..", "_").strip("._")
    return _repos_dir(user_id) / safe


def _run_git(args: list[str], cwd: str, timeout: int = 120) -> tuple[int, str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        proc = subprocess.run(
            ["git"] + args, cwd=cwd, capture_output=True, text=True,
            timeout=timeout, env=env
        )
        return proc.returncode, (proc.stdout + proc.stderr).strip()
    except subprocess.TimeoutExpired:
        return 1, "Git command timed out"
    except FileNotFoundError:
        return 1, "git is not installed"


def _normalize_url(url: str) -> str:
    """Ensure URL ends with .git for consistency."""
    url = url.rstrip("/")
    if not url.endswith(".git"):
        url += ".git"
    return url


def _build_clone_url(url: str, token: str = "") -> str:
    if not token or not url.startswith("https://"):
        return url
    import urllib.parse
    safe_token = urllib.parse.quote(token, safe="")
    host = url.split("://", 1)[1]
    if "github.com" in url:
        return f"https://x-access-token:{safe_token}@{host}"
    if "gitlab" in url:
        return f"https://oauth2:{safe_token}@{host}"
    if "bitbucket" in url:
        return f"https://x-token-auth:{safe_token}@{host}"
    return f"https://x-access-token:{safe_token}@{host}"


def _is_valid_git_repo(path: Path) -> bool:
    """Check if directory is a valid git repo (has .git dir)."""
    return (path / ".git").is_dir()


@bp.route("/api/repo/clone", methods=["POST"])
@require_auth
def clone_repo():
    body = request.json or {}
    url = (body.get("url") or "").strip()
    branch = (body.get("branch") or "").strip()
    token = (body.get("token") or "").strip()
    name = (body.get("name") or "").strip()

    if not url:
        return jsonify({"error": "Repository URL required"}), 400

    url = _normalize_url(url)

    if not name:
        name = url.rstrip("/").split("/")[-1].replace(".git", "")

    repo_dir = _repo_path(g.user.id, name)

    if repo_dir.exists():
        if not _is_valid_git_repo(repo_dir):
            shutil.rmtree(repo_dir, ignore_errors=True)
        else:
            clone_url = _build_clone_url(url, token)
            _run_git(["remote", "set-url", "origin", clone_url], str(repo_dir))
            code, out = _run_git(["pull"], str(repo_dir))
            if code != 0:
                return jsonify({"error": f"Pull failed: {out}"}), 500
            if branch:
                code, out = _run_git(["checkout", branch], str(repo_dir))
                if code != 0:
                    return jsonify({"error": f"Checkout failed: {out}"}), 500
            ActivityLog.record(g.user.id, "repo_pull", f"Pulled {name}", request.remote_addr)
            return jsonify({
                "ok": True, "action": "pulled", "name": name,
                "path": str(repo_dir), "output": out
            })

    clone_url = _build_clone_url(url, token)
    cmd = ["clone", "--progress"]
    if branch:
        cmd += ["--branch", branch]
    cmd += [clone_url, str(repo_dir)]

    code, out = _run_git(cmd, str(_repos_dir(g.user.id)), timeout=300)
    if code != 0:
        return jsonify({"error": f"Clone failed: {out}"}), 500

    reader = UserConfigReader(g.user.id)
    cfg = reader.load()
    cfg["repo_root"] = str(repo_dir)
    cfg["venv_path"] = ".venv"
    reader.save(cfg)

    ActivityLog.record(g.user.id, "repo_clone", f"Cloned {name} from {url}", request.remote_addr)
    return jsonify({
        "ok": True, "action": "cloned", "name": name,
        "path": str(repo_dir), "output": out
    }), 201


@bp.route("/api/repo/pull", methods=["POST"])
@require_auth
def pull_repo():
    cfg = UserConfigReader(g.user.id).load()
    repo = cfg.get("repo_root", "").strip()
    if not repo or not Path(repo).exists():
        return jsonify({"error": "No repo configured"}), 400

    code, out = _run_git(["pull"], repo)
    if code != 0:
        return jsonify({"error": f"Pull failed: {out}"}), 500

    ActivityLog.record(g.user.id, "repo_pull", None, request.remote_addr)
    return jsonify({"ok": True, "output": out})


@bp.route("/api/repo/branches")
@require_auth
def list_branches():
    body_url = request.args.get("url", "").strip()
    token = request.args.get("token", "").strip()

    cfg = UserConfigReader(g.user.id).load()
    repo = cfg.get("repo_root", "").strip()

    if repo and Path(repo).exists() and not body_url:
        _run_git(["fetch", "--prune"], repo, timeout=30)
        code, out = _run_git(["branch", "-a", "--format=%(refname:short)"], repo)
        if code != 0:
            return jsonify({"error": out}), 500
        branches = sorted(set(
            b.replace("origin/", "") for b in out.splitlines() if b and "HEAD" not in b
        ))
        code2, current = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo)
        return jsonify({
            "branches": branches,
            "current": current.strip() if code2 == 0 else "",
        })

    if body_url:
        clone_url = _build_clone_url(_normalize_url(body_url), token)
        code, out = _run_git(["ls-remote", "--heads", clone_url], "/tmp", timeout=30)
        if code != 0:
            return jsonify({"error": f"Could not list branches: {out}"}), 500
        branches = sorted(
            line.split("refs/heads/")[-1]
            for line in out.splitlines() if "refs/heads/" in line
        )
        return jsonify({"branches": branches, "current": ""})

    return jsonify({"branches": [], "current": ""})


@bp.route("/api/repo/checkout", methods=["POST"])
@require_auth
def checkout_branch():
    body = request.json or {}
    branch = (body.get("branch") or "").strip()
    if not branch:
        return jsonify({"error": "Branch name required"}), 400

    cfg = UserConfigReader(g.user.id).load()
    repo = cfg.get("repo_root", "").strip()
    if not repo or not Path(repo).exists():
        return jsonify({"error": "No repo configured"}), 400

    code, out = _run_git(["checkout", branch], repo)
    if code != 0:
        code, out = _run_git(["checkout", "-b", branch, f"origin/{branch}"], repo)
    if code != 0:
        return jsonify({"error": f"Checkout failed: {out}"}), 500

    ActivityLog.record(g.user.id, "repo_checkout", f"Switched to {branch}", request.remote_addr)
    return jsonify({"ok": True, "branch": branch, "output": out})


@bp.route("/api/repo/status")
@require_auth
def repo_status():
    cfg = UserConfigReader(g.user.id).load()
    repo = cfg.get("repo_root", "").strip()

    result = {
        "has_repo": False, "repo_path": "", "repo_name": "",
        "branch": "", "has_venv": False, "venv_path": "",
        "python": "", "requirements": [],
    }

    if not repo or not Path(repo).exists():
        return jsonify(result)

    result["has_repo"] = True
    result["repo_path"] = repo
    result["repo_name"] = Path(repo).name

    code, branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo)
    if code == 0:
        result["branch"] = branch.strip()

    venv_path = cfg.get("venv_path", ".venv")
    venv_dir = Path(repo) / venv_path
    if venv_dir.exists():
        result["has_venv"] = True
        result["venv_path"] = str(venv_dir)
        python = venv_dir / "bin" / "python"
        if not python.exists():
            python = venv_dir / "Scripts" / "python.exe"
        result["python"] = str(python) if python.exists() else ""

    for name in ["requirements.txt", "requirements/base.txt",
                 "requirements/dev.txt", "requirements-dev.txt"]:
        rp = Path(repo) / name
        if rp.exists():
            result["requirements"].append(name)

    return jsonify(result)


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
    import json
    cfg_path = get_user_workspace_dir(g.user.id) / "repo_configs.json"
    if not cfg_path.exists():
        return jsonify([])
    try:
        return jsonify(json.loads(cfg_path.read_text(encoding="utf-8")))
    except Exception:
        return jsonify([])


@bp.route("/api/repo/configs", methods=["POST"])
@require_auth
def save_repo_config():
    import json
    body = request.json or {}
    name = (body.get("name") or "").strip()
    url = (body.get("url") or "").strip()
    branch = (body.get("branch") or "").strip()
    token = (body.get("token") or "").strip()

    if not name or not url:
        return jsonify({"error": "name and url required"}), 400

    cfg_path = get_user_workspace_dir(g.user.id) / "repo_configs.json"
    configs = []
    if cfg_path.exists():
        try:
            configs = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            configs = []

    configs = [c for c in configs if c.get("name") != name]
    configs.append({"name": name, "url": url, "branch": branch, "token": token})
    cfg_path.write_text(json.dumps(configs, indent=2), encoding="utf-8")
    return jsonify({"ok": True})


@bp.route("/api/repo/configs", methods=["DELETE"])
@require_auth
def delete_repo_config():
    import json
    body = request.json or {}
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400

    cfg_path = get_user_workspace_dir(g.user.id) / "repo_configs.json"
    if not cfg_path.exists():
        return jsonify({"ok": True})

    try:
        configs = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        configs = []

    configs = [c for c in configs if c.get("name") != name]
    cfg_path.write_text(json.dumps(configs, indent=2), encoding="utf-8")
    return jsonify({"ok": True})
