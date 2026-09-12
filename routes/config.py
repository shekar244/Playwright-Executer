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
from pathlib import Path

from flask import Blueprint, jsonify, request
from ui_launcher.config_reader import ConfigReader, TOOL_CONFIG_FILE

bp = Blueprint("config", __name__)


@bp.route("/api/config")
def get_config():
    return jsonify(ConfigReader().load())


@bp.route("/api/config/features", methods=["POST"])
def save_features():
    body = request.json or {}
    features = body.get("features")
    if not isinstance(features, list):
        return jsonify({"error": "features must be an array"}), 400
    reader = ConfigReader()
    cfg = reader.load()
    cfg["features"] = features
    try:
        reader.save(cfg)
    except OSError as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True})


@bp.route("/api/config/tools", methods=["POST"])
def save_tools():
    body = request.json or {}
    tools = body.get("extra_options")
    if not isinstance(tools, list):
        return jsonify({"error": "extra_options must be an array"}), 400
    reader = ConfigReader()
    cfg = reader.load()
    cfg["extra_options"] = tools
    try:
        reader.save(cfg)
    except OSError as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True})


@bp.route("/api/config/repo-root", methods=["POST"])
def save_repo_root():
    body = request.json or {}
    repo = body.get("repo_root", "").strip()
    if not repo:
        return jsonify({"error": "repo_root required"}), 400
    from pathlib import Path
    # Resolve to absolute + normalise separators so Windows and Mac both store cleanly
    try:
        resolved = str(Path(repo).resolve())
    except Exception:
        resolved = repo
    reader = ConfigReader()
    cfg = reader.load()
    cfg["repo_root"] = resolved
    try:
        reader.save(cfg)
        # Always persist repo_root to the tool bootstrap config as well so the
        # resolution chain can find the repo-level config on the next startup.
        reader.save_tool_config({"repo_root": resolved})
    except OSError as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True, "repo_root": resolved})


@bp.route("/api/config/ui-tabs", methods=["POST"])
def save_ui_tabs():
    body = request.json or {}
    tabs = body.get("ui_tabs")
    if not isinstance(tabs, dict):
        return jsonify({"error": "ui_tabs must be an object"}), 400
    reader = ConfigReader()
    cfg = reader.load()
    cfg["ui_tabs"] = tabs
    try:
        reader.save(cfg)
    except OSError as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True})


@bp.route("/api/config/info")
def config_info():
    """Return metadata about the active config file (path, source)."""
    cfg = ConfigReader().load()
    return jsonify({
        "active_config_path":  cfg.get("_active_config_path",  str(TOOL_CONFIG_FILE)),
        "active_config_source": cfg.get("_active_config_source", "tool"),
        "tool_config_path":    str(TOOL_CONFIG_FILE),
        "repo_root":           cfg.get("repo_root", ""),
        "config_override_path": cfg.get("config_override_path", ""),
    })


@bp.route("/api/config/override", methods=["POST"])
def set_config_override():
    """
    Set or clear config_override_path in the tool-level bootstrap config.
    Body: { "path": "/abs/path/to/config.json" }  — pass "" to clear.
    """
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
        ConfigReader().save_tool_config({"config_override_path": resolved})
    except OSError as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True, "config_override_path": resolved})


@bp.route("/api/config/save-to-repo", methods=["POST"])
def save_config_to_repo():
    """Copy the current active config into {repo_root}/config.json."""
    reader = ConfigReader()
    cfg = reader.load()
    repo = cfg.get("repo_root", "").strip()
    if not repo:
        return jsonify({"error": "No repo_root configured"}), 400
    dest = Path(repo) / "config.json"
    try:
        import json
        to_save = {k: v for k, v in cfg.items() if not k.startswith("_")}
        dest.write_text(json.dumps(to_save, indent=2), encoding="utf-8")
    except OSError as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True, "saved_to": str(dest)})


@bp.route("/api/config/allure", methods=["POST"])
def save_allure_config():
    """Save Allure report generation settings."""
    body = request.json or {}
    allowed = {
        "allure_format", "allure2_bin", "allure3_bin",
        "allure_results_dir", "allure_history_dir",
        "report_consolidated_dir", "report_pertest_dir", "generate_pertest_reports",
    }
    reader = ConfigReader()
    cfg = reader.load()
    for key in allowed:
        if key in body:
            cfg[key] = body[key]
    try:
        reader.save(cfg)
    except OSError as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True})


@bp.route("/api/config/jfrog/read-pip-ini", methods=["GET"])
def read_jfrog_pip_ini():
    """
    Read and parse the existing pip.ini / pip.conf from the project venv.
    Extracts jfrog_url, jfrog_repo, jfrog_email, and jfrog_token from the
    index-url line so the form can be pre-populated for editing.

    Returns 404 when no pip.ini exists or it has no parseable index-url.
    """
    import re, sys
    from ui_launcher.command_builder import resolve_python

    cfg       = ConfigReader().load()
    repo_root = cfg.get("repo_root", "").strip()
    python    = resolve_python(repo_root, cfg.get("venv_path", "")) if repo_root else sys.executable
    venv_root = Path(python).parent.parent

    pip_ini_path = venv_root / "pip.ini"

    if not pip_ini_path.exists():
        return jsonify({"found": False, "path": str(pip_ini_path)}), 404

    try:
        import configparser
        cp = configparser.ConfigParser(strict=False)
        cp.read(str(pip_ini_path), encoding="utf-8")
        index_url = (
            cp.get("global", "index-url", fallback="")
            or cp.get("global", "index_url", fallback="")
        ).strip()
    except Exception as exc:
        return jsonify({"found": True, "path": str(pip_ini_path), "error": str(exc)}), 200

    if not index_url:
        return jsonify({"found": True, "path": str(pip_ini_path), "index_url": "", "parsed": False})

    # Parse:  https://email:token@hostname/artifactory/api/pypi/repo/simple
    m = re.match(
        r'https?://([^:@]+):([^@]+)@([^/]+)/artifactory/api/pypi/([^/]+)/simple',
        index_url,
    )
    if not m:
        return jsonify({
            "found": True, "path": str(pip_ini_path),
            "index_url": index_url, "parsed": False,
        })

    email, token, hostname, repo = m.groups()
    return jsonify({
        "found":      True,
        "path":       str(pip_ini_path),
        "index_url":  index_url,
        "parsed":     True,
        "jfrog_url":  hostname,
        "jfrog_repo": repo,
        "jfrog_email": email,
        "jfrog_token": token,
    })


@bp.route("/api/config/jfrog", methods=["POST"])
def save_jfrog_config():
    """Save JFrog Artifactory credentials to config."""
    body = request.json or {}
    allowed = {"jfrog_url", "jfrog_repo", "jfrog_email", "jfrog_token"}
    reader = ConfigReader()
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
    from ui_launcher.command_builder import resolve_python

    body = request.json or {}
    cfg = ConfigReader().load()

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

    # Locate venv
    repo_root = cfg.get("repo_root", "").strip()
    python    = resolve_python(repo_root, cfg.get("venv_path", "")) if repo_root else sys.executable
    venv_root = Path(python).parent.parent

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


@bp.route("/api/config/jfrog/preview", methods=["POST"])
def preview_jfrog_pip_ini():
    """Return the pip.ini content that would be generated, without writing it."""
    import re
    body = request.json or {}
    cfg  = ConfigReader().load()

    url   = (body.get("jfrog_url")   or cfg.get("jfrog_url",   "")).strip().rstrip("/")
    repo  = (body.get("jfrog_repo")  or cfg.get("jfrog_repo",  "")).strip()
    email = (body.get("jfrog_email") or cfg.get("jfrog_email", "")).strip()
    token = (body.get("jfrog_token") or cfg.get("jfrog_token", "")).strip()

    if not url:
        return jsonify({"content": "# Artifactory URL is required"})

    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    hostname = re.sub(r"^https?://", "", url)

    masked_token = (token[:4] + "****" + token[-4:]) if len(token) > 8 else "****"
    index_url    = f"https://{email or '<email>'}:{masked_token or '<token>'}@{hostname}/artifactory/api/pypi/{repo or '<repo>'}/simple"

    content = (
        "[global]\n"
        f"index-url = {index_url}\n"
    )
    return jsonify({"content": content})


@bp.route("/api/config/atlassian", methods=["POST"])
def save_atlassian_config():
    """Save Atlassian OAuth app credentials (client_id + client_secret)."""
    body    = request.json or {}
    allowed = {"atlassian_client_id", "atlassian_client_secret", "atlassian_cloud_id"}
    reader  = ConfigReader()
    cfg     = reader.load()
    for key in allowed:
        if key in body:
            cfg[key] = body[key]
    try:
        reader.save(cfg)
    except OSError as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True})


@bp.route("/api/config/pinned-repos", methods=["POST"])
def save_pinned_repos():
    body = request.json or {}
    repos = body.get("pinned_repos")
    if not isinstance(repos, list):
        return jsonify({"error": "pinned_repos must be an array"}), 400
    reader = ConfigReader()
    cfg = reader.load()
    cfg["pinned_repos"] = [str(r) for r in repos if r]
    try:
        reader.save(cfg)
    except OSError as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True})
