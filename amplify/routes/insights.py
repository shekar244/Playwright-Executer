"""
Blueprint: Jira Insights launcher + reverse proxy
  GET  /api/insights/status  — side-car state (running / healthy / port / public URL)
  POST /api/insights/start   — launch the Streamlit app if it isn't up yet
  POST /api/insights/stop    — stop the Streamlit process started by this server
  *    /insights-app/*       — reverse proxy to Streamlit (same port, no second route needed)
"""
from __future__ import annotations

import os
import urllib.request
import urllib.error

from flask import Blueprint, jsonify, request, Response, g

from amplify.jira_insights.launcher import process, port as insights_port
from amplify.auth_utils import require_auth

bp = Blueprint("insights", __name__)


@bp.route("/api/insights/status")
@require_auth
def insights_status():
    status = process.status()
    if status.get("healthy") and not status.get("public_url"):
        status["public_url"] = f"{request.scheme}://{request.host}/insights-app"
    return jsonify(status)


@bp.route("/api/insights/start", methods=["POST"])
@require_auth
def insights_start():
    result = process.start()
    if result.get("healthy") and not result.get("public_url"):
        result["public_url"] = f"{request.scheme}://{request.host}/insights-app"
    return jsonify(result), (500 if result.get("error") else 200)


@bp.route("/api/insights/stop", methods=["POST"])
@require_auth
def insights_stop():
    return jsonify(process.stop())


@bp.route("/insights-app/", defaults={"path": ""})
@bp.route("/insights-app/<path:path>", methods=["GET", "POST", "PUT", "DELETE"])
def insights_proxy(path):
    """Reverse proxy to Streamlit on localhost:{INSIGHTS_PORT}."""
    target = f"http://127.0.0.1:{insights_port()}/{path}"
    qs = request.query_string.decode()
    if qs:
        target += "?" + qs

    headers = {k: v for k, v in request.headers if k.lower() not in ("host", "content-length")}
    data = request.get_data() if request.method in ("POST", "PUT") else None

    try:
        req = urllib.request.Request(target, data=data, headers=headers, method=request.method)
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read()
            resp_headers = dict(resp.getheaders())
            resp_headers.pop("Transfer-Encoding", None)
            resp_headers.pop("Content-Encoding", None)
            return Response(body, status=resp.status, headers=resp_headers)
    except urllib.error.HTTPError as e:
        return Response(e.read(), status=e.code)
    except Exception:
        return Response("Streamlit not reachable", status=502)
