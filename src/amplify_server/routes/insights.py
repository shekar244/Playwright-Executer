"""
Blueprint: Jira Hub launcher
  GET  /api/insights/status  — side-car state (running / healthy / port / public URL)
  POST /api/insights/start   — launch the Streamlit app if it isn't up yet
  POST /api/insights/stop    — stop the Streamlit process started by this server
  GET  /api/insights/open    — sign the user in to Insights and redirect there (iframe + New tab)
  GET  /api/insights/options — Insights option visibility, read-only (for a separately deployed Insights)

Two ways to run it (see src/jira_insights/launcher.py):
  * a side-car process of this server, on its own port (local runs, local Docker: -p 8501:8501);
  * its own pod/service (OpenShift, Kubernetes): INSIGHTS_INTERNAL_URL is where QEA checks it, and
    INSIGHTS_PUBLIC_URL where browsers reach it — e.g. /jira-hub on QEA's own host, sent to the
    Insights service by a route/ingress path (Streamlit started with STREAMLIT_SERVER_BASE_URL_PATH=jira-hub).
Either way users get there through /api/insights/open, signed in as their QEA user.
"""
from __future__ import annotations

import os
from functools import wraps
from urllib.parse import urlencode, urlsplit

from flask import Blueprint, g, jsonify, make_response, redirect, request

from amplify_server.auth_utils import require_auth
from jira_insights.launcher import port as insights_port, process
from amplify_server.server_secrets import load_insights_options
from amplify_server.sso import issue_insights_token

bp = Blueprint("insights", __name__)


@bp.route("/api/insights/status")
@require_auth
def insights_status():
    return jsonify(process.status())


@bp.route("/api/insights/start", methods=["POST"])
@require_auth
def insights_start():
    result = process.start()
    return jsonify(result), (500 if result.get("error") else 200)


@bp.route("/api/insights/stop", methods=["POST"])
@require_auth
def insights_stop():
    return jsonify(process.stop())


@bp.route("/api/insights/options")
def insights_options_public():
    """Read-only Insights option visibility, no login required.

    A separately deployed Insights service (own disk, e.g. Render) can't read this
    server's server_config.json, so it fetches the flags here via QEA_API_URL.
    Only on/off booleans are exposed — never credentials.
    """
    return jsonify(load_insights_options())


def _insights_base_url() -> str:
    """Where the browser should reach Insights: INSIGHTS_PUBLIC_URL, else this host on the Insights port."""
    public = os.environ.get("INSIGHTS_PUBLIC_URL", "").rstrip("/")
    if public:
        return public
    host = urlsplit("//" + request.host).hostname or "localhost"
    if ":" in host:                                   # IPv6 literal
        host = f"[{host}]"
    scheme = (request.headers.get("X-Forwarded-Proto") or request.scheme).split(",")[0].strip()
    return f"{'https' if scheme == 'https' else 'http'}://{host}:{insights_port()}"


def _login_page_for_new_tabs(view):
    """A browser tab opened without a QEA session lands on the login page, not a raw JSON error.
    (Only for a top-level page load: the embedded iframe and API callers keep the 401.)"""
    @wraps(view)
    def wrapper(*args, **kwargs):
        response = make_response(view(*args, **kwargs))
        if response.status_code == 401 and request.headers.get("Sec-Fetch-Dest") == "document":
            return redirect("/login")
        return response
    return wrapper


@bp.route("/api/insights/open")
@_login_page_for_new_tabs
@require_auth
def insights_open():
    """Send the browser to Jira Hub already signed in as the current user.

    The embedded view and the "New tab" button both come through here, so every load gets a
    fresh single-use token (reload and theme changes included) and nothing about the user
    is ever taken from a URL the user could edit.
    """
    theme = "dark" if request.args.get("theme") == "dark" else "light"
    embedded = request.args.get("embed") == "1"
    params = {"sso": issue_insights_token(g.user.id, g.user.username, embedded=embedded), "theme": theme}
    if embedded:
        params.update(embed="true", embed_options=f"{theme}_theme")
    response = redirect(f"{_insights_base_url()}/?{urlencode(params)}", code=302)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response
