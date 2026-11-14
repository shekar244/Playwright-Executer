"""
Blueprint: Admin routes
  GET    /api/admin/users               — list all users
  GET    /api/admin/users/<id>          — single user detail + activity
  POST   /api/admin/users/<id>/reset    — reset password
  POST   /api/admin/users/<id>/toggle   — enable / disable user
  POST   /api/admin/users/<id>/promote  — grant / revoke admin
  DELETE /api/admin/users/<id>          — delete user and workspace
  GET    /api/admin/metrics             — usage metrics and stats
  GET    /api/admin/activity            — recent activity log
  GET    /api/admin/workspace/<id>      — workspace disk usage for a user
"""
import shutil
from pathlib import Path
from flask import Blueprint, jsonify, request, g
from amplify.auth_utils import require_admin
from amplify.models.user import User, ActivityLog
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
    new_pw = body.get("password", "").strip()
    if not new_pw or len(new_pw) < 6:
        return jsonify({"error": "Password must be at least 6 characters"}), 400
    User.reset_password(user_id, new_pw)
    ActivityLog.record(g.user.id, "admin_reset_password",
                       f"Reset password for {user.username} (id={user_id})",
                       request.remote_addr)
    return jsonify({"ok": True})


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


def _dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
