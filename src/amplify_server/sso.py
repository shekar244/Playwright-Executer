"""Short-lived sign-in hand-off from Amplify QEA to Jira Hub.

Jira Hub is a separate Streamlit app (its own port, and in some deployments its own host),
so the browser's QEA session cookie can't be relied on to reach it. Instead QEA mints a signed
token for the logged-in user and sends the browser to Insights with it:

    /api/insights/open  ->  302  ->  <insights>/?sso=<token>

The token is HS256-signed with the same JWT_SECRET_KEY QEA signs its own sessions with, so both
services must be given the same key. It is built to be hard to misuse:

  * audience "amplify-insights": it is NOT accepted as a QEA session token, and a QEA session
    token is NOT accepted here (both directions are tested);
  * it lives 60 seconds, and each token is single-use within an Insights process, so a URL that
    leaks into history or a log is useless almost immediately.
"""
from __future__ import annotations

import threading
import time
import uuid

import jwt

from amplify_server.config import settings

AUDIENCE = "amplify-insights"
TTL_SECONDS = 60
LEEWAY_SECONDS = 10          # tolerated clock difference between the two services

_used: dict[str, float] = {}     # jti -> moment it can be forgotten
_lock = threading.Lock()


def issue_insights_token(user_id: int, username: str, embedded: bool = False) -> str:
    """`embedded` says the page will be shown inside QEA's iframe. It travels in the signed token
    because Streamlit hides its own ?embed= parameter from the app, so the app can't see it in the URL."""
    now = int(time.time())
    return jwt.encode(
        {"sub": str(user_id), "name": username, "emb": bool(embedded), "aud": AUDIENCE,
         "iat": now, "exp": now + TTL_SECONDS, "jti": uuid.uuid4().hex},
        settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def verify_insights_token(token: str) -> dict | None:
    """{"user_id", "username", "embedded"} for a valid, not-yet-used token; otherwise None."""
    try:
        claims = jwt.decode(
            token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM],
            audience=AUDIENCE, leeway=LEEWAY_SECONDS,
            options={"require": ["exp", "iat", "sub", "jti", "aud"]})
        user_id = int(claims["sub"])
    except (jwt.InvalidTokenError, ValueError, TypeError):
        return None
    if not _claim_once(str(claims["jti"]), float(claims["exp"])):
        return None
    return {"user_id": user_id, "username": str(claims.get("name") or f"user {user_id}"),
            "embedded": bool(claims.get("emb"))}


def _claim_once(jti: str, exp: float) -> bool:
    """True the first time a token id is seen, False on any replay."""
    now = time.time()
    with _lock:
        for stale in [k for k, forget_at in _used.items() if forget_at < now]:
            del _used[stale]
        if jti in _used:
            return False
        _used[jti] = exp + LEEWAY_SECONDS
        return True
