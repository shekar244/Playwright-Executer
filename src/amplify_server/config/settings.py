"""Application settings and configuration."""
import os
import secrets
import time
from pathlib import Path

# Determine if running in container or locally
_IN_CONTAINER = os.environ.get("CONTAINER", "0") == "1"
_DATA_ROOT = Path("/app/data") if _IN_CONTAINER else Path.home() / ".amplify-qea"

# Database
DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{_DATA_ROOT}/amplify_server.db")
DATABASE_PATH = _DATA_ROOT / "amplify_server.db"
try:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
except (OSError, PermissionError):
    pass

# JWT
def _jwt_secret() -> str:
    """JWT_SECRET_KEY, or — when it isn't set — a random key generated once and kept beside the database.

    There is deliberately no built-in default: a well-known key would let anyone forge sessions and
    Jira Hub sign-in links. QEA and Jira Hub on the same host or volume find the same file and so
    agree on the key; services deployed apart (separate volumes) must be given the same JWT_SECRET_KEY.
    """
    configured = os.environ.get("JWT_SECRET_KEY", "").strip()
    if configured:
        return configured
    path = _DATA_ROOT / ".jwt_secret"
    key = secrets.token_urlsafe(48)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)   # exclusive: two apps starting
    except FileExistsError:                                               # together agree on one key
        for _ in range(40):
            try:
                existing = path.read_text(encoding="utf-8").strip()
            except OSError:
                break
            if existing:
                return existing
            time.sleep(0.05)                                              # the other app is still writing it
        return key
    except OSError:
        return key                                                        # unwritable volume: this process only
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(key)
    return key


JWT_SECRET_KEY = _jwt_secret()
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_HOURS = 24
JWT_REFRESH_EXPIRATION_DAYS = 7

# Workspaces
WORKSPACES_ROOT = _DATA_ROOT / "workspaces"
try:
    WORKSPACES_ROOT.mkdir(parents=True, exist_ok=True)
except (OSError, PermissionError):
    pass

# App
PORT = int(os.environ.get("PORT", 8080))
DEBUG = os.environ.get("DEBUG", "0") == "1"
