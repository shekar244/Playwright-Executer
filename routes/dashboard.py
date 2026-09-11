"""
Blueprint: Dashboard / Report routes
  /api/dashboard
  /api/report  /api/report/open
  /api/report/history  /api/report/history/clear
  /allure/  /allure/<path>   ← HTTP-serves the active Allure SPA directory
"""
from __future__ import annotations

import webbrowser
from pathlib import Path

from flask import Blueprint, jsonify, request, send_from_directory

from routes.history import (
    load_history, save_history,
    parse_allure_results_full, parse_allure_history_trend,
)
from ui_launcher.config_reader import ConfigReader
from ui_launcher.report_resolver import ReportResolver

bp = Blueprint("dashboard", __name__)

# Report directory currently mounted at /allure/ — updated on each open.
_active_report_dir: str = ""


# ── Built-in Allure SPA HTTP server ──────────────────────────────────────────

@bp.route("/allure/")
@bp.route("/allure/<path:filepath>")
def serve_allure_spa(filepath: str = "index.html") -> object:
    """
    Serve the active Allure SPA over HTTP so the report's JavaScript can load
    its relative data/ JSON files.  Allure 3 (and full Allure 2) reports are
    SPAs that require HTTP — file:// blocks local XHR/fetch.
    """
    if not _active_report_dir or not Path(_active_report_dir).is_dir():
        return "No Allure report loaded. Open a report from the Dashboard first.", 404
    try:
        return send_from_directory(_active_report_dir, filepath)
    except Exception:
        return "File not found in report directory.", 404


def _resolve_report_path(path: str, rel_path: str) -> tuple[str, str]:
    """
    Return (abs_path_without_anchor, anchor) for the first candidate that exists.
    abs_path is empty string when nothing is found.
    """
    anchor = ""
    if "#" in path:
        path, frag = path.split("#", 1)
        anchor = "#" + frag

    cfg = ConfigReader().load()
    repo_root = cfg.get("repo_root", "").strip()

    candidates: list[str] = []
    if path:
        candidates.append(path)
    if repo_root and rel_path:
        candidates.append(str(Path(repo_root) / rel_path.split("#")[0]))
    if rel_path:
        candidates.append(str(Path(__file__).parent.parent / rel_path.split("#")[0]))

    for c in candidates:
        if c and Path(c).exists():
            return c, anchor
    return "", anchor


# ── Report open logic ─────────────────────────────────────────────────────────

@bp.route("/api/report/open", methods=["POST"])
def open_report():
    """
    Open an Allure report in the default browser.

    Decision:
      • filename == index.html  →  Allure SPA (2 or 3).  Mount the parent
        directory at /allure/ and open http://amplify-qea:7777/allure/{anchor}.
        SPA reports must be served over HTTP; file:// blocks the JS data loads.

      • any other filename      →  self-contained single-file report
        (Allure 2 complete.html, allure3 --single-file index.html copied as
        {uid}-allure3.html, Allure 2 per-run {timestamp}.html, etc.).
        Open directly as file:// — these embed all assets and need no server.
    """
    global _active_report_dir

    body     = request.json or {}
    path     = body.get("path",    "").strip()
    rel_path = body.get("relpath", "").strip()

    if not path and not rel_path:
        return jsonify({"error": "No path provided"}), 400

    abs_path, anchor = _resolve_report_path(path, rel_path)
    if not abs_path:
        tried = " | ".join(x for x in [path, rel_path] if x)
        return jsonify({"error": f"Report not found. Tried: {tried}"}), 404

    p = Path(abs_path)

    if p.name.lower() == "index.html":
        # SPA — serve via built-in HTTP server
        _active_report_dir = str(p.parent)
        url = f"http://amplify-qea:7777/allure/{anchor}"
        webbrowser.open(url)
        return jsonify({"ok": True, "url": url, "mode": "http", "dir": str(p.parent)})
    else:
        # Single-file — open directly
        url = p.as_uri() + anchor
        webbrowser.open(url)
        return jsonify({"ok": True, "url": url, "mode": "file"})


# ── Dashboard ─────────────────────────────────────────────────────────────────

@bp.route("/api/dashboard")
def get_dashboard():
    repo = request.args.get("repo", "").strip()
    cfg  = ConfigReader().load()
    tests, allure_trend = [], []
    report_path = ""

    if repo:
        results_dir = str(Path(repo) / cfg.get("allure_results_dir", "allure/results"))
        tests = parse_allure_results_full(results_dir)
        allure_trend = parse_allure_history_trend(repo, cfg)

        try:
            resolver = ReportResolver(repo, cfg.get("report_paths", []))
            report_path = (
                resolver.find_latest_in_dir(cfg.get("report_consolidated_dir", "allure/reports/consolidated"))
                or resolver.find_latest_in_dir(cfg.get("report_individual_dir", "allure/reports"))
                or ""
            )
        except Exception:
            report_path = ""

        # Build uid → individual-report-path map from the latest history record.
        # record_run_history stores per-test individual paths after each run;
        # using them here ensures the current-run view shows both "📄 Test" and
        # "📊 Run" buttons, not just the consolidated link.
        uid_to_individual: dict = {}
        history_records = load_history(repo)
        if history_records:
            for ht in (history_records[0].get("tests") or []):
                uid = ht.get("uid", "")
                ind  = ht.get("report_path", "")
                cons = ht.get("consolidated_path", "")
                if uid and ind and ind != cons:
                    uid_to_individual[uid] = ind

        for t in tests:
            uid = t.get("uid", "")
            # consolidated_path = the suite-level report for this run
            if not t.get("consolidated_path"):
                t["consolidated_path"] = report_path
            # report_path = individual per-test file when available, else consolidated
            if uid and uid in uid_to_individual:
                t["report_path"] = uid_to_individual[uid]
            elif not t.get("report_path"):
                t["report_path"] = report_path

    return jsonify({
        "tests":       tests,
        "summaries":   [],
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


@bp.route("/api/report/history")
def get_report_history():
    repo = request.args.get("repo", "").strip()
    return jsonify({"records": load_history(repo)})


@bp.route("/api/report/history/clear", methods=["POST"])
def clear_report_history():
    repo = (request.json or {}).get("repo", "").strip()
    save_history([], repo)
    return jsonify({"ok": True})
