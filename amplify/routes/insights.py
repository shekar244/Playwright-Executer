"""
Blueprint: Jira Insights launcher
  GET  /api/insights/status  — side-car state (running / healthy / port / public URL)
  POST /api/insights/start   — launch the Streamlit app if it isn't up yet
  POST /api/insights/stop    — stop the Streamlit process started by this server

The Streamlit app (jira_insights/app.py) runs beside Flask on INSIGHTS_PORT
and is embedded in the Jira Insights tab with an <iframe>.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, g

from amplify.jira_insights.launcher import process
from amplify.auth_utils import require_auth

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
