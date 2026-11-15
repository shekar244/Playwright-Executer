"""
Blueprint: Config routes
  GET  /api/config
  GET  /api/config/info         — active config path + source
  POST /api/config/features
  POST /api/config/tools
  POST /api/config/ui-tabs
  POST /api/config/repo-root
  POST /api/config/override     — set/clear config_override_path
  POST /api/config/save-to-repo — copy active config into {repo}/config.json
"""
import json
from pathlib import Path

from flask import Blueprint, jsonify, request, g
from amplify.config_reader import ConfigReader, TOOL_CONFIG_FILE
from amplify.user_config_reader import UserConfigReader
from amplify.auth_utils import require_auth

bp = Blueprint("config", __name__)

@bp.route("/api/config")
@require_auth
def get_config():
    return jsonify(UserConfigReader(g.user.id).load())


@bp.route("/api/config/features", methods=["POST"])
@require_auth
def save_features():
    body = request.json or {}
    features = body.get("features")
    if not isinstance(features, list):
        return jsonify({"error": "features must be an array"}), 400
    reader = UserConfigReader(g.user.id)
    cfg = reader.load()
    cfg["features"] = features
    try:
        reader.save(cfg)
    except OSError as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True})


@bp.route("/api/config/tools", methods=["POST"])
@require_auth
def save_tools():
    body = request.json or {}
    tools = body.get("extra_options")
    if not isinstance(tools, list):
        return jsonify({"error": "extra_options must be an array"}), 400
    reader = UserConfigReader(g.user.id)
    cfg = reader.load()
    cfg["extra_options"] = tools
    try:
        reader.save(cfg)
    except OSError as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True})


@bp.route("/api/config/repo-root", methods=["POST"])
@require_auth
def save_repo_root():
    body = request.json or {}
    repo = body.get("repo_root", "").strip()
    if not repo:
        return jsonify({"error": "repo_root required"}), 400
    from pathlib import Path
    try:
        resolved = str(Path(repo).resolve())
    except Exception:
        resolved = repo
    reader = UserConfigReader(g.user.id)
    cfg = reader.load()
    cfg["repo_root"] = resolved
    try:
        reader.save(cfg)
    except OSError as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True, "repo_root": resolved})


@bp.route("/api/config/ui-tabs", methods=["POST"])
@require_auth
def save_ui_tabs():
    body = request.json or {}
    tabs = body.get("ui_tabs")
    if not isinstance(tabs, dict):
        return jsonify({"error": "ui_tabs must be an object"}), 400
    reader = UserConfigReader(g.user.id)
    cfg = reader.load()
    cfg["ui_tabs"] = tabs
    try:
        reader.save(cfg)
    except OSError as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True})


@bp.route("/api/config/info")
@require_auth
def config_info():
    """Return metadata about the active config file (path, source)."""
    cfg = UserConfigReader(g.user.id).load()
    return jsonify({
        "active_config_path":  cfg.get("_active_config_path",  ""),
        "active_config_source": cfg.get("_active_config_source", "user"),
        "repo_root":           cfg.get("repo_root", ""),
        "config_override_path": cfg.get("config_override_path", ""),
    })


@bp.route("/api/config/override", methods=["POST"])
@require_auth
def set_config_override():
    """Set or clear config_override_path in user's workspace."""
    body = request.json or {}
    raw_path = body.get("path", "").strip()
    if raw_path:
        p = Path(raw_path)
        if not p.exists():
            return jsonify({"error": f"File not found: {raw_path}"}), 400
        if not p.suffix == ".json":
            return jsonify({"error": "Override must be a .json file"}), 400
        resolved = str(p.resolve())
    else:
        resolved = ""
    try:
        reader = UserConfigReader(g.user.id)
        cfg = reader.load()
        cfg["config_override_path"] = resolved
        reader.save(cfg)
    except OSError as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True, "config_override_path": resolved})


@bp.route("/api/config/save-to-repo", methods=["POST"])
@require_auth
def save_config_to_repo():
    """Copy the current active config into {repo_root}/config.json."""
    reader = UserConfigReader(g.user.id)
    cfg = reader.load()
    repo = cfg.get("repo_root", "").strip()
    if not repo:
        return jsonify({"error": "No repo_root configured"}), 400
    dest = Path(repo) / "config.json"
    try:
        to_save = {k: v for k, v in cfg.items() if not k.startswith("_")}
        dest.write_text(json.dumps(to_save, indent=2), encoding="utf-8")
    except OSError as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True, "saved_to": str(dest)})


@bp.route("/api/config/allure", methods=["POST"])
@require_auth
def save_allure_config():
    """Save Allure report generation settings."""
    body = request.json or {}
    allowed = {
        "allure_format", "allure2_bin", "allure3_bin",
        "allure_results_dir", "allure_history_dir",
        "report_consolidated_dir", "report_pertest_dir", "generate_pertest_reports",
    }
    reader = UserConfigReader(g.user.id)
    cfg = reader.load()
    for key in allowed:
        if key in body:
            cfg[key] = body[key]
    try:
        reader.save(cfg)
    except OSError as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True})





@bp.route("/api/config/jfrog/pip-ini-content", methods=["GET"])
def get_pip_ini_content():
    """Return the raw, unmasked contents of the venv pip.ini file."""
    cfg       = UserConfigReader(g.user.id).load()
    repo_root = cfg.get("repo_root", "").strip()
    if not repo_root:
        return jsonify({"found": False, "path": ""})
    venv_path = cfg.get("venv_path", ".venv").strip() or ".venv"
    venv_root = Path(repo_root) / venv_path
    pip_ini_path = venv_root / "pip.ini"

    if not pip_ini_path.exists():
        return jsonify({"found": False, "path": str(pip_ini_path)})

    try:
        content = pip_ini_path.read_text(encoding="utf-8", errors="replace")
        return jsonify({"found": True, "path": str(pip_ini_path), "content": content})
    except OSError as exc:
        return jsonify({"found": False, "path": str(pip_ini_path), "error": str(exc)})


@bp.route("/api/config/jfrog", methods=["POST"])
@require_auth
def save_jfrog_config():
    """Save JFrog Artifactory credentials to config."""
    body = request.json or {}
    allowed = {"jfrog_url", "jfrog_repo", "jfrog_email", "jfrog_token"}
    reader = UserConfigReader(g.user.id)
    cfg = reader.load()
    for key in allowed:
        if key in body:
            cfg[key] = body[key]
    try:
        reader.save(cfg)
    except OSError as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True})


@bp.route("/api/config/jfrog/generate", methods=["POST"])
def generate_jfrog_pip_ini():
    """
    Write pip.ini (Windows) or pip.conf (macOS/Linux) into the project venv
    with JFrog Artifactory as the package index.

    The pip.ini path is:
      Windows:      {venv_root}\\pip.ini
      macOS/Linux:  {venv_root}/pip.conf

    Generated content:
      [global]
      index-url  = https://{email}:{token}@{hostname}/artifactory/api/pypi/{repo}/simple
      trusted-host = {hostname}

      [install]
      trusted-host = {hostname}
    """
    import re, sys
    from amplify.command_builder import resolve_python

    body = request.json or {}
    cfg = UserConfigReader(g.user.id).load()

    url   = (body.get("jfrog_url")   or cfg.get("jfrog_url",   "")).strip().rstrip("/")
    repo  = (body.get("jfrog_repo")  or cfg.get("jfrog_repo",  "")).strip()
    email = (body.get("jfrog_email") or cfg.get("jfrog_email", "")).strip()
    token = (body.get("jfrog_token") or cfg.get("jfrog_token", "")).strip()

    if not url:
        return jsonify({"error": "Artifactory URL is required"}), 400
    if not repo:
        return jsonify({"error": "Repository name is required"}), 400
    if not email or not token:
        return jsonify({"error": "Email and token are required"}), 400

    # Normalise URL — strip scheme for the trusted-host value
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    hostname = re.sub(r"^https?://", "", url)

    index_url = f"https://{email}:{token}@{hostname}/artifactory/api/pypi/{repo}/simple"

    content = (
        "[global]\n"
        f"index-url = {index_url}\n"
    )

    # Locate venv — write pip.ini into the repo's .venv, not system Python
    repo_root = cfg.get("repo_root", "").strip()
    venv_path = cfg.get("venv_path", ".venv").strip() or ".venv"
    if repo_root:
        venv_root = Path(repo_root) / venv_path
    else:
        return jsonify({"error": "No repo configured — clone a repo first"}), 400
    if not venv_root.exists():
        return jsonify({"error": f"Venv not found at {venv_root} — create it first"}), 400

    pip_ini_path = venv_root / "pip.ini"

    try:
        pip_ini_path.write_text(content, encoding="utf-8")
    except OSError as exc:
        return jsonify({"error": f"Could not write {pip_ini_path}: {exc}"}), 500

    return jsonify({
        "ok":      True,
        "path":    str(pip_ini_path),
        "content": content,
    })



@bp.route("/api/config/pinned-repos", methods=["POST"])
@require_auth
def save_pinned_repos():
    body = request.json or {}
    repos = body.get("pinned_repos")
    if not isinstance(repos, list):
        return jsonify({"error": "pinned_repos must be an array"}), 400
    reader = UserConfigReader(g.user.id)
    cfg = reader.load()
    cfg["pinned_repos"] = [str(r) for r in repos if r]
    try:
        reader.save(cfg)
    except OSError as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True})
