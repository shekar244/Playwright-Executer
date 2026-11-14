"""Application settings and configuration."""
import os
from pathlib import Path

# Determine if running in container or locally
_IN_CONTAINER = os.environ.get("CONTAINER", "0") == "1"
_DATA_ROOT = Path("/app/data") if _IN_CONTAINER else Path.home() / ".amplify-qea"

# Database
DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{_DATA_ROOT}/amplify.db")
DATABASE_PATH = _DATA_ROOT / "amplify.db"
try:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
except (OSError, PermissionError):
    pass

# JWT
JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "amplify-jwt-secret-change-in-prod")
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
