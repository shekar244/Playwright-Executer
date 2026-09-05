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
    report_path = ""

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

        # Suite-level report path
        try:
            resolver = ReportResolver(repo, cfg.get("report_paths", []))
            report_path = resolver.find_latest_in_dir(cfg.get("report_individual_dir", "allure/reports")) or ""
        except Exception:
            report_path = ""

        # Every test in this run shares the same consolidated report path
        if report_path:
            for t in tests:
                if not t.get("report_path"):
                    t["report_path"] = report_path

    return jsonify({
        "tests":       tests,
        "summaries":   summaries,
        "trend":       allure_trend,
        "run_history": load_history(repo),
        "report_path": report_path,
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
    from pathlib import Path as _Path
    from ui_launcher.config_reader import ConfigReader as _CR

    body = request.json or {}
    path     = body.get("path", "").strip()
    rel_path = body.get("relpath", "").strip()
    if not path and not rel_path:
        return jsonify({"error": "No path provided"}), 400

    # Build candidate list — try in order, open the first one that exists.
    candidates: list[str] = []
    if path:
        candidates.append(path)

    # Also resolve relative path against current repo root and the tool root.
    rp = rel_path or path
    if rp:
        try:
            cfg      = _CR().load()
            repo_root = cfg.get("repo_root", "").strip()
            if repo_root:
                candidates.append(str(_Path(repo_root) / rp))
            # Fallback: relative to the tool directory
            candidates.append(str(_Path(__file__).parent.parent / rp))
        except Exception:
            pass

    for c in candidates:
        try:
            p = _Path(c)
            if p.exists():
                # Path.as_uri() produces the correct file:// URL on all platforms:
                #   Windows: C:\dir\index.html  →  file:///C:/dir/index.html
                #   Mac/Linux: /dir/index.html  →  file:///dir/index.html
                webbrowser.open(p.as_uri())
                return jsonify({"ok": True, "resolved": str(p)})
        except Exception:
            continue

    tried = " | ".join(candidates)
    return jsonify({"error": f"Report not found. Tried: {tried}"}), 404


@bp.route("/api/report/history")
def get_report_history():
    repo = request.args.get("repo", "").strip()
    return jsonify({"records": load_history(repo)})


@bp.route("/api/report/history/clear", methods=["POST"])
def clear_report_history():
    repo = (request.json or {}).get("repo", "").strip()
    save_history([], repo)
    return jsonify({"ok": True})
