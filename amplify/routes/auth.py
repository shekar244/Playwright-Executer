"""Blueprint: Authentication routes
  POST /api/auth/register  — create new user
  POST /api/auth/login     — authenticate user
  POST /api/auth/logout    — clear session
  POST /api/auth/refresh   — refresh JWT token
  GET  /api/auth/me        — get current user info
"""
import json
from flask import Blueprint, request, jsonify, g, make_response
from amplify.models.user import User, ActivityLog
from amplify.auth_utils import generate_jwt, require_auth
from amplify.storage import initialize_user_workspace

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

    if not password or len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters"}), 400

    try:
        user = User.create(username, password, email)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    initialize_user_workspace(user.id)
    ActivityLog.record(user.id, "register", f"New account: {username}", request.remote_addr)

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
    }), 200

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
