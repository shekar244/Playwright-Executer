"""JWT and authentication utilities."""
import jwt
import hashlib
from datetime import datetime, timedelta
from functools import wraps
from flask import request, jsonify, g
from amplify.config import settings
from amplify.models.user import User

def generate_jwt(user_id: int, exp_hours: int = None) -> str:
    """Generate a JWT token for a user."""
    exp_hours = exp_hours or settings.JWT_EXPIRATION_HOURS
    payload = {
        "user_id": user_id,
        "exp": datetime.utcnow() + timedelta(hours=exp_hours),
        "iat": datetime.utcnow()
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)

def verify_jwt(token: str) -> int:
    """Verify JWT and return user_id. Returns None if invalid."""
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        return payload.get("user_id")
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None

def require_auth(f):
    """Decorator to protect routes with JWT authentication."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        token = None

        if "Authorization" in request.headers:
            auth_header = request.headers["Authorization"]
            try:
                token = auth_header.split(" ")[1]
            except IndexError:
                return jsonify({"error": "Invalid authorization header"}), 401

        if not token:
            token = request.cookies.get("access_token")

        if not token:
            return jsonify({"error": "Missing authentication token"}), 401

        user_id = verify_jwt(token)
        if not user_id:
            return jsonify({"error": "Invalid or expired token"}), 401

        user = User.get_by_id(user_id)
        if not user:
            return jsonify({"error": "User not found"}), 401

        g.user = user
        return f(*args, **kwargs)

    return decorated_function
