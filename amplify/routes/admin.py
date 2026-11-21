"""
Blueprint: Admin routes
  GET    /api/admin/users               — list all users
  GET    /api/admin/users/<id>          — single user detail + activity
  POST   /api/admin/users/<id>/reset    — reset password (the user must then choose their own)
  POST   /api/admin/users/<id>/toggle   — enable / disable user
  POST   /api/admin/users/<id>/promote  — grant / revoke admin
  DELETE /api/admin/users/<id>          — delete user and workspace
  GET    /api/admin/metrics             — usage metrics and stats
  GET    /api/admin/activity            — recent activity log
  GET    /api/admin/workspace/<id>      — workspace disk usage for a user
  GET    /api/admin/grid                — Grid coordinator URL, whether a token is set (never the token)
  POST   /api/admin/grid                — set the coordinator URL / API token; replies with a connection check
  GET    /api/admin/grid/pytest-args    — the extra pytest arguments Grid runs pass to the runner pods
  POST   /api/admin/grid/pytest-args    — replace them {"arguments": [...]} (see amplify/grid_args.py)
  GET    /api/admin/ui-tabs             — tab catalog + current visibility + landing tab
  POST   /api/admin/ui-tabs             — set which tabs every user sees (and, optionally, the landing tab)
  GET    /api/admin/insights-options    — Jira Insights option catalog + current visibility
  POST   /api/admin/insights-options    — set which Insights options every user sees
"""
import shutil
from pathlib import Path
from flask import Blueprint, jsonify, request, g
from amplify.auth_utils import require_admin
from amplify.models.user import User, ActivityLog
from amplify.routes.grid import probe as grid_probe
from amplify.server_secrets import (
    INSIGHTS_OPTION_CATALOG, UI_TAB_CATALOG,
    grid_config_summary, load_default_tab, load_grid_environments, load_insights_options, load_ui_tabs,
    save_grid_config, save_insights_options, save_ui_tabs,
)
from amplify.storage import get_user_workspace_dir

bp = Blueprint("admin", __name__)


@bp.route("/api/admin/users")
@require_admin
def list_users():
    users = User.get_all()
    result = []
    for u in users:
        ws = get_user_workspace_dir(u.id)
        disk = _dir_size(ws)
        d = u.to_dict()
        d["workspace_bytes"] = disk
        result.append(d)
    return jsonify(result)


@bp.route("/api/admin/users/<int:user_id>")
@require_admin
def get_user(user_id):
    user = User.get_by_id(user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404
    activity = ActivityLog.for_user(user_id, limit=50)
    ws = get_user_workspace_dir(user_id)
    d = user.to_dict()
    d["workspace_bytes"] = _dir_size(ws)
    d["workspace_path"] = str(ws)
    d["activity"] = activity
    return jsonify(d)


@bp.route("/api/admin/users/<int:user_id>/reset", methods=["POST"])
@require_admin
def reset_password(user_id):
    user = User.get_by_id(user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404
    body = request.json or {}
    new_pw = body.get("password", "").strip() if isinstance(body.get("password", ""), str) else ""
    problem = User.password_problem(new_pw)
    if problem:
        return jsonify({"error": problem}), 400
    User.reset_password(user_id, new_pw, must_change=True)       # a password an admin chose is temporary
    ActivityLog.record(g.user.id, "admin_reset_password",
                       f"Reset password for {user.username} (id={user_id}); they must choose a new one at next sign-in",
                       request.remote_addr)
    return jsonify({"ok": True, "must_change_password": True})


@bp.route("/api/admin/users/<int:user_id>/toggle", methods=["POST"])
@require_admin
def toggle_user(user_id):
    user = User.get_by_id(user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404
    if user_id == g.user.id:
        return jsonify({"error": "Cannot disable your own account"}), 400
    new_state = not user.is_active
    User.set_active(user_id, new_state)
    action = "enabled" if new_state else "disabled"
    ActivityLog.record(g.user.id, f"admin_{action}_user",
                       f"{action.title()} user {user.username} (id={user_id})",
                       request.remote_addr)
    return jsonify({"ok": True, "is_active": new_state})


@bp.route("/api/admin/users/<int:user_id>/promote", methods=["POST"])
@require_admin
def promote_user(user_id):
    user = User.get_by_id(user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404
    new_state = not user.is_admin
    User.set_admin(user_id, new_state)
    action = "promoted" if new_state else "demoted"
    ActivityLog.record(g.user.id, f"admin_{action}_user",
                       f"{action.title()} user {user.username} (id={user_id})",
                       request.remote_addr)
    return jsonify({"ok": True, "is_admin": new_state})


@bp.route("/api/admin/users/<int:user_id>", methods=["DELETE"])
@require_admin
def delete_user(user_id):
    user = User.get_by_id(user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404
    if user_id == g.user.id:
        return jsonify({"error": "Cannot delete your own account"}), 400
    ws = get_user_workspace_dir(user_id)
    if ws.exists():
        shutil.rmtree(ws, ignore_errors=True)
    User.delete(user_id)
    ActivityLog.record(g.user.id, "admin_deleted_user",
                       f"Deleted user {user.username} (id={user_id}) and workspace",
                       request.remote_addr)
    return jsonify({"ok": True})


@bp.route("/api/admin/metrics")
@require_admin
def get_metrics():
    metrics = ActivityLog.metrics()
    metrics["total_users"] = User.count()
    users = User.get_all()
    metrics["active_accounts"] = sum(1 for u in users if u.is_active)
    metrics["admin_count"] = sum(1 for u in users if u.is_admin)
    total_disk = sum(_dir_size(get_user_workspace_dir(u.id)) for u in users)
    metrics["total_workspace_bytes"] = total_disk
    return jsonify(metrics)


@bp.route("/api/admin/activity")
@require_admin
def get_activity():
    limit = request.args.get("limit", 100, type=int)
    return jsonify(ActivityLog.recent(limit=min(limit, 500)))


@bp.route("/api/admin/server-config", methods=["GET", "POST"])
@require_admin
def server_config():
    """View or update server-level secrets (Jira/Zephyr credentials)."""
    from amplify.server_secrets import load_zephyr_config, save_zephyr_config
    if request.method == "POST":
        body = request.json or {}
        updates = body.get("zephyr", body)
        if not isinstance(updates, dict):
            return jsonify({"error": "zephyr must be an object"}), 400
        try:
            save_zephyr_config(updates)
        except OSError as exc:
            return jsonify({"error": str(exc)}), 500
        ActivityLog.record(g.user.id, "admin_update_secrets",
                           "Updated server Jira/Zephyr config", request.remote_addr)
        return jsonify({"ok": True})

    cfg = load_zephyr_config()
    for key in ("api_token", "access_key", "secret_key"):
        val = cfg.get(key, "")
        if val and len(val) > 6:
            cfg[key] = val[:3] + "•" * (len(val) - 6) + val[-3:]
    return jsonify({"zephyr": cfg})


@bp.route("/api/admin/grid", methods=["GET", "POST"])
@require_admin
def grid_coordinator():
    """Where the Grid tab sends runs (server-wide). The token is write-only: it is never returned."""
    if request.method == "POST":
        body = request.json or {}
        url, token, clear = body.get("coordinator_url"), body.get("api_token"), body.get("clear_token", False)
        environments = body.get("environments")
        default_environment = body.get("default_environment")
        if (url is not None and not isinstance(url, str)) or (token is not None and not isinstance(token, str)) \
                or not isinstance(clear, bool):
            return jsonify({"error": "coordinator_url and api_token must be text; clear_token true or false"}), 400
        if environments is not None and not isinstance(environments, list):
            return jsonify({"error": "environments must be a list"}), 400
        if default_environment is not None and not isinstance(default_environment, str):
            return jsonify({"error": "default_environment must be text"}), 400
        try:
            save_grid_config(url, token, clear, environments, default_environment)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except OSError as exc:
            return jsonify({"error": str(exc)}), 500
        summary = grid_config_summary()
        summary.update(load_grid_environments())
        token_note = "removed" if clear else ("replaced" if (token or "").strip() else "unchanged")
        ActivityLog.record(g.user.id, "admin_update_grid",
                           f"Grid coordinator: {summary['coordinator_url'] or 'not set'}; token {token_note}",
                           request.remote_addr)
        return jsonify({"ok": True, **summary, "status": grid_probe()})
    summary = grid_config_summary()
    summary.update(load_grid_environments())
    return jsonify(summary)


@bp.route("/api/admin/grid/pytest-args", methods=["GET", "POST"])
@require_admin
def grid_pytest_args():
    """Which pytest options a Grid run passes to the pods, and the values users may pick (server-wide)."""
    from amplify import grid_args
    if request.method == "POST":
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"error": "Send a JSON object {\"arguments\": [...]}"}), 400
        try:
            saved = grid_args.save_definitions(body.get("arguments"))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except OSError as exc:
            return jsonify({"error": str(exc)}), 500
        ActivityLog.record(g.user.id, "admin_update_grid_pytest_args",
                           "Grid pytest arguments: " + (", ".join(d["flag"] for d in saved) or "none"),
                           request.remote_addr)
        return jsonify({"ok": True, "arguments": saved, "reserved": sorted(grid_args.RESERVED)})
    return jsonify({"arguments": grid_args.load_definitions(), "reserved": sorted(grid_args.RESERVED)})


def _flags_endpoint(*, body_key, load, save, catalog, action):
    """GET → current flags + catalog; POST → validate, save, audit-log."""
    if request.method == "POST":
        flags = (request.json or {}).get(body_key)
        if not isinstance(flags, dict):
            return jsonify({"error": f"{body_key} must be an object"}), 400
        try:
            saved = save(flags)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except OSError as exc:
            return jsonify({"error": str(exc)}), 500
        hidden = [k for k, v in saved.items() if not v]
        ActivityLog.record(g.user.id, action, "Hidden: " + (", ".join(hidden) or "none"),
                           request.remote_addr)
        return jsonify({"ok": True, body_key: saved})
    return jsonify({body_key: load(), "catalog": catalog})


@bp.route("/api/admin/ui-tabs", methods=["GET", "POST"])
@require_admin
def ui_tabs():
    """Tab visibility and the landing tab are server-wide: they apply to every user, admins included."""
    if request.method == "POST":
        body = request.json or {}
        flags = body.get("ui_tabs")
        if not isinstance(flags, dict):
            return jsonify({"error": "ui_tabs must be an object"}), 400
        try:
            saved = save_ui_tabs(flags, body.get("default_tab"))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except OSError as exc:
            return jsonify({"error": str(exc)}), 500
        landing = load_default_tab()
        hidden = [k for k, v in saved.items() if not v]
        ActivityLog.record(g.user.id, "admin_update_ui_tabs",
                           f"Hidden: {', '.join(hidden) or 'none'}; landing tab: {landing}", request.remote_addr)
        return jsonify({"ok": True, "ui_tabs": saved, "default_tab": landing})
    return jsonify({"ui_tabs": load_ui_tabs(), "default_tab": load_default_tab(), "catalog": UI_TAB_CATALOG})


@bp.route("/api/admin/insights-options", methods=["GET", "POST"])
@require_admin
def insights_options():
    """Jira Insights sidebar options and views, server-wide."""
    return _flags_endpoint(body_key="insights_options", load=load_insights_options,
                           save=save_insights_options, catalog=INSIGHTS_OPTION_CATALOG,
                           action="admin_update_insights_options")


def _dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
