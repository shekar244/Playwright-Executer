"""Blueprint: Authentication routes
  POST /api/auth/register  — create new user
  POST /api/auth/login     — authenticate user
  POST /api/auth/logout    — clear session
  POST /api/auth/refresh   — refresh JWT token
  GET  /api/auth/me        — get current user info
  POST /api/auth/change-password — change your own password (needs the current one)
"""
import json
from flask import Blueprint, request, jsonify, g, make_response
from amplify_server.models.user import User, ActivityLog
from amplify_server.auth_utils import generate_jwt, require_auth
from amplify_server.storage import initialize_user_workspace

bp = Blueprint("auth", __name__)

@bp.route("/api/auth/register", methods=["POST"])
def register():
    """Register a new user."""
    data = request.json or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    email = (data.get("email") or "").strip() or None

    if not username or len(username) < 3:
        return jsonify({"error": "Username must be at least 3 characters"}), 400

    problem = User.password_problem(password)
    if problem:
        return jsonify({"error": problem}), 400

    is_first_user = User.count() == 0

    try:
        user = User.create(username, password, email, is_admin=is_first_user)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    initialize_user_workspace(user.id)
    ActivityLog.record(user.id, "register",
                       f"New account: {username}" + (" (auto-admin: first user)" if is_first_user else ""),
                       request.remote_addr)

    token = generate_jwt(user.id)

    response = make_response(jsonify({
        "ok": True,
        "user": {"id": user.id, "username": user.username, "email": user.email},
        "access_token": token
    }))
    response.set_cookie("access_token", token, httponly=True, secure=False, samesite="Lax", max_age=86400)

    return response, 201

@bp.route("/api/auth/login", methods=["POST"])
def login():
    """Authenticate user and issue JWT."""
    data = request.json or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    if not username or not password:
        return jsonify({"error": "Username and password required"}), 400

    user = User.verify_credentials(username, password)
    if not user:
        return jsonify({"error": "Invalid credentials"}), 401

    User.update_last_login(user.id)
    ActivityLog.record(user.id, "login", None, request.remote_addr)

    token = generate_jwt(user.id)

    response = make_response(jsonify({
        "ok": True,
        "user": {"id": user.id, "username": user.username, "email": user.email,
                 "is_admin": user.is_admin},
        "must_change_password": user.must_change_password,
        "access_token": token
    }))
    response.set_cookie("access_token", token, httponly=True, secure=False, samesite="Lax", max_age=86400)

    return response, 200

@bp.route("/api/auth/logout", methods=["POST"])
@require_auth
def logout():
    """Clear user session."""
    response = make_response(jsonify({"ok": True}))
    response.delete_cookie("access_token")
    return response, 200

@bp.route("/api/auth/me", methods=["GET"])
@require_auth
def get_me():
    """Get current user information."""
    user = g.user
    return jsonify({
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "is_admin": user.is_admin,
        "must_change_password": user.must_change_password,
    }), 200

@bp.route("/api/auth/change-password", methods=["POST"])
@require_auth
def change_password():
    """Change your own password. Always asks for the current one, so a stolen session can't lock you out;
    it is also the one thing a user whose password an admin reset is allowed to do."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Expected a JSON object"}), 400
    current, new = data.get("current_password"), data.get("new_password")
    if not isinstance(current, str) or not current:
        return jsonify({"error": "Enter your current password"}), 400
    if not isinstance(new, str) or not new:
        return jsonify({"error": "Enter a new password"}), 400

    user = g.user
    if User.verify_credentials(user.username, current) is None:
        return jsonify({"error": "Current password is incorrect"}), 400
    problem = User.password_problem(new)
    if problem:
        return jsonify({"error": problem}), 400
    if new == current:
        return jsonify({"error": "The new password must be different from the current one"}), 400

    required = user.must_change_password
    User.change_password(user.id, new)
    ActivityLog.record(user.id, "change_password",
                       "Password changed" + (" (required after an admin reset)" if required else ""),
                       request.remote_addr)

    token = generate_jwt(user.id)                # carry on signed in
    response = make_response(jsonify({"ok": True, "access_token": token}))
    response.set_cookie("access_token", token, httponly=True, secure=False, samesite="Lax", max_age=86400)
    return response, 200

@bp.route("/api/auth/refresh", methods=["POST"])
@require_auth
def refresh_token():
    """Refresh JWT token."""
    user = g.user
    token = generate_jwt(user.id)

    response = make_response(jsonify({
        "ok": True,
        "access_token": token
    }))
    response.set_cookie("access_token", token, httponly=True, secure=False, samesite="Lax", max_age=86400)

    return response, 200
