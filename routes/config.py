"""
Blueprint: Config routes
  GET  /api/config
  POST /api/config/features
  POST /api/config/tools
  POST /api/config/ui-tabs
  POST /api/config/repo-root
"""
from flask import Blueprint, jsonify, request
from ui_launcher.config_reader import ConfigReader

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
