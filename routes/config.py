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
