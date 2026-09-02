"""
Blueprint: Dashboard / Report routes
  /api/dashboard
  /api/report  /api/report/open
  /api/report/history  /api/report/history/clear
"""
from __future__ import annotations

import os
from pathlib import Path

from flask import Blueprint, jsonify, request

from routes.history import (
    load_history, save_history,
    parse_allure_results_full, parse_smart_reporter, parse_allure_history_trend,
)
from ui_launcher.config_reader import ConfigReader
from ui_launcher.report_resolver import ReportResolver

bp = Blueprint("dashboard", __name__)


@bp.route("/api/dashboard")
def get_dashboard():
    repo = request.args.get("repo", "").strip()
    cfg  = ConfigReader().load()
    tests, summaries, allure_trend = [], [], []

    if repo:
        results_dir = str(Path(repo) / cfg.get("allure_results_dir", "allure/results"))
        allure_tests = parse_allure_results_full(results_dir)

        report_dir = str(Path(repo) / cfg.get("report_individual_dir", "allure/reports"))
        smart = parse_smart_reporter(report_dir)

        tests = allure_tests if len(allure_tests) >= len(smart["results"]) else smart["results"]
        if not tests:
            tests = allure_tests or smart["results"]
        summaries = smart["summaries"]
        allure_trend = parse_allure_history_trend(repo, cfg)

    return jsonify({
        "tests":      tests,
        "summaries":  summaries,
        "trend":      allure_trend,
        "run_history": load_history(),
    })


@bp.route("/api/report")
def get_report():
    repo = request.args.get("repo", "").strip()
    if not repo:
        return jsonify({"individual": None, "consolidated": None})
    cfg = ConfigReader().load()
    resolver = ReportResolver(repo, cfg.get("report_paths", []))
    individual   = resolver.find_latest_in_dir(cfg.get("report_individual_dir", "allure/reports"))
    consolidated = resolver.find_latest_in_dir(cfg.get("report_consolidated_dir", ""))
    return jsonify({"individual": individual, "consolidated": consolidated})


@bp.route("/api/report/open", methods=["POST"])
def open_report():
    import webbrowser
    body = request.json or {}
    path = body.get("path", "").strip()
    if not path or not os.path.exists(path):
        return jsonify({"error": "Report file not found"}), 404
    webbrowser.open(f"file://{path}")
    return jsonify({"ok": True})


@bp.route("/api/report/history")
def get_report_history():
    return jsonify({"records": load_history()})


@bp.route("/api/report/history/clear", methods=["POST"])
def clear_report_history():
    save_history([])
    return jsonify({"ok": True})
