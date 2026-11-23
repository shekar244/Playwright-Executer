"""Signed, per-user links for viewing a run's HTML report inside the dashboard.

The dashboard never accepts a file path from the browser. It names a run (and optionally a test); the
server looks the report up in that user's own run history and returns a link containing a signed token:

    /rv/<token>/<file>      → the report, served from the directory the token names

The token carries the user id, the report's directory and its entry file, expires after an hour, and is
signed with JWT_SECRET_KEY under its own audience (so it is neither a session nor a sign-in token). The
route that serves it still requires the logged-in session to be that same user.
"""
from __future__ import annotations

import time
from pathlib import Path

import jwt

from amplify_server.config import settings

AUDIENCE = "amplify-report-view"
TTL_SECONDS = 3600
REPORT_EXTENSIONS = {".html", ".htm"}
# Served with grid reports: the page gets an opaque origin, so its scripts cannot use Amplify's cookies or reach
# its pages. Grid reports are built from whatever the tests (from any repo) wrote — names, steps, HTML attachments.
SANDBOX_CSP = "sandbox allow-scripts allow-popups allow-downloads; frame-ancestors 'self'"
DEFAULT_CSP = "frame-ancestors 'self'"
# What a report may load besides its pages: scripts, styles, data, images, fonts, attachments.
ASSET_EXTENSIONS = REPORT_EXTENSIONS | {
    ".js", ".mjs", ".css", ".json", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp", ".woff", ".woff2",
    ".ttf", ".eot", ".txt", ".log", ".xml", ".csv", ".map", ".webm", ".mp4", ".zip", ".attach"}


def resolve_report_file(raw_path: str) -> tuple[Path | None, str]:
    """(an existing .html file, the #anchor that was on the path) — or (None, "") when there is no such report."""
    path, _, frag = (raw_path or "").partition("#")
    candidate = Path(path)
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return None, ""
    if not resolved.is_file() or resolved.suffix.lower() not in REPORT_EXTENSIONS:
        return None, ""
    return resolved, f"#{frag}" if frag else ""


def mint(user_id: int, report_file: Path, sandbox: bool = False) -> str:
    now = int(time.time())
    return jwt.encode({"uid": int(user_id), "dir": str(report_file.parent), "entry": report_file.name,
                       "sbx": bool(sandbox), "aud": AUDIENCE, "iat": now, "exp": now + TTL_SECONDS},
                      settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def verify(token: str) -> dict | None:
    try:
        claims = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM],
                            audience=AUDIENCE, options={"require": ["exp", "aud", "uid", "dir", "entry"]})
        return {"uid": int(claims["uid"]), "dir": str(claims["dir"]), "entry": str(claims["entry"]),
                "sandbox": bool(claims.get("sbx"))}
    except (jwt.InvalidTokenError, ValueError, TypeError):
        return None


def servable(directory: str, relative: str) -> Path | None:
    """The file to send for `relative` inside `directory`, or None. Blocks anything that is not a plain
    web asset, and anything that resolves (through `..` or a symlink) outside the report directory."""
    root = Path(directory)
    try:
        root = root.resolve(strict=True)
        target = (root / relative).resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return None
    if root != target and root not in target.parents:
        return None
    if not target.is_file() or target.suffix.lower() not in ASSET_EXTENSIONS:
        return None
    return target
