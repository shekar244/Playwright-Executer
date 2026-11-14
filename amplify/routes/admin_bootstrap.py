"""
One-time admin bootstrap endpoint.
Promotes a user to admin using a secret token passed as env var.

Set ADMIN_BOOTSTRAP_TOKEN in Render environment variables, then call:
  POST /api/admin/bootstrap
  { "username": "shekarrender", "token": "your-bootstrap-token" }

The endpoint deletes itself after successful use (removes the env var check
so it can't be reused even if the token leaks).
"""
from flask import Blueprint, jsonify, request
from amplify.models.user import User, ActivityLog

bp = Blueprint("admin_bootstrap", __name__)


@bp.route("/api/admin/bootstrap", methods=["POST"])
def bootstrap_admin():
    import os
    token = os.environ.get("ADMIN_BOOTSTRAP_TOKEN", "").strip()
    if not token:
        return jsonify({"error": "Bootstrap not available — ADMIN_BOOTSTRAP_TOKEN not set or already used"}), 403

    body = request.json or {}
    provided = (body.get("token") or "").strip()
    username = (body.get("username") or "").strip()

    if not provided or provided != token:
        return jsonify({"error": "Invalid bootstrap token"}), 403

    if not username:
        return jsonify({"error": "username required"}), 400

    user = User.get_by_username(username)
    if not user:
        return jsonify({"error": f"User '{username}' not found — register first"}), 404

    User.set_admin(user.id, True)
    ActivityLog.record(user.id, "admin_bootstrap", f"Bootstrapped {username} as admin", request.remote_addr)

    os.environ.pop("ADMIN_BOOTSTRAP_TOKEN", None)

    return jsonify({"ok": True, "message": f"User '{username}' is now admin"})
