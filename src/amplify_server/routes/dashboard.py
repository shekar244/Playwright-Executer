"""
Blueprint: Dashboard / Report routes
  /api/dashboard                       — single-repo view (legacy)
  /api/dashboard/overview              — Grid + local runs consolidated, filtered by env / browser / pod / time
  /api/dashboard/run/<source>/<id>     — one run: chunks, pods, per-test results (the drill-down)
  /api/dashboard/report-link  (POST)   — signed link to view a run's HTML report in the dashboard:
                                         a local run's own report, or a Grid run's Allure report (source "grid")
  /rv/<token>/<file>                   — serves that report (signed, per-user, short-lived)
  /api/report  /api/report/open
  /api/report/history  /api/report/history/clear
  /allure/  /allure/<path>   ← HTTP-serves the active Allure SPA directory
"""
from __future__ import annotations

import re
import time
import webbrowser
from pathlib import Path
from urllib.parse import quote

from flask import Blueprint, jsonify, request, send_file, send_from_directory, g

from amplify_server import consolidated, grid_data, grid_reports, report_viewer
from amplify_server import payload_coerce as pc

from amplify_server.routes.history import (
    load_history, save_history,
    parse_allure_results_full, parse_allure_history_trend,
)
from amplify_server.user_config_reader import UserConfigReader
from amplify_server.auth_utils import require_auth
from amplify_server.report_resolver import ReportResolver
from amplify_server.request_body import json_object

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

    cfg = UserConfigReader(g.user.id).load()
    repo_root = cfg.get("repo_root", "").strip()

    candidates: list[str] = []
    if path:
        candidates.append(path)
    if repo_root and rel_path:
        candidates.append(str(Path(repo_root) / rel_path.split("#")[0]))
    if rel_path:
        candidates.append(str(Path(__file__).parent.parent.parent / rel_path.split("#")[0]))

    for c in candidates:
        if c and Path(c).exists():
            return c, anchor
    return "", anchor


# ── Report open logic ─────────────────────────────────────────────────────────

@bp.route("/api/report/open", methods=["POST"])
@require_auth
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
@require_auth
def get_dashboard():
    repo = request.args.get("repo", "").strip()
    cfg  = UserConfigReader(g.user.id).load()
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
        history_records = load_history(repo, user_id=g.user.id)
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
        "run_history": load_history(repo, user_id=g.user.id),
        "report_path": report_path,
    })


# ── Consolidated view (Grid + local runs) ─────────────────────────────────────

_RUN_ID = re.compile(r"^[A-Za-z0-9._-]+$")


def _filters_or_400():
    try:
        return consolidated.parse_filters(request.args, int(time.time() * 1000)), None
    except ValueError as exc:
        return None, (jsonify({"error": str(exc)}), 400)


@bp.route("/api/dashboard/overview")
@require_auth
def dashboard_overview():
    filters, bad = _filters_or_400()
    if bad:
        return bad
    if filters.source == consolidated.LOCAL:
        grid_list, grid_status = [], {"skipped": True}
    else:
        grid_list, grid_status = grid_data.list_runs()
    return jsonify(consolidated.build_overview(
        grid_list=grid_list, fetch_grid_details=grid_data.fetch_details,
        local_records=load_history(user_id=g.user.id), filters=filters, grid_status=grid_status))


@bp.route("/api/dashboard/run/<source>/<run_id>")
@require_auth
def dashboard_run(source, run_id):
    filters, bad = _filters_or_400()
    if bad:
        return bad
    if source not in (consolidated.GRID, consolidated.LOCAL) or not _RUN_ID.match(run_id):
        return jsonify({"error": "Unknown run"}), 404
    if source == consolidated.GRID:
        detail, error = grid_data.get_run(run_id)
        if detail is None:
            return jsonify({"error": error or "Run not found"}), (502 if "unreachable" in (error or "") else 404)
        run = consolidated.normalize_grid_run(detail)
    else:
        record = next((r for r in load_history(user_id=g.user.id) if consolidated.local_run_id(r) == run_id), None)
        if record is None:
            return jsonify({"error": "Run not found"}), 404
        run = consolidated.normalize_local_record(record)
    return jsonify(consolidated.build_run_detail(run, filters))


def _grid_report_link(run_id):
    """Link to a Grid run's Allure report. The coordinator builds it; Amplify keeps one folder per run and
    version (see grid_reports), so the viewer token exposes just that report."""
    try:
        cached = grid_reports.fetch_report(run_id)
    except grid_reports.ReportError as exc:
        return jsonify({"error": exc.message}), exc.status
    report_file, _ = report_viewer.resolve_report_file(str(cached))
    if report_file is None:
        return jsonify({"error": "That report is no longer on disk"}), 404
    token = report_viewer.mint(g.user.id, report_file, sandbox=True)      # untrusted content: no same-origin access
    return jsonify({"url": f"/rv/{token}/{quote(report_file.name)}", "title": f"{run_id} · run report",
                    "file": report_file.name})


@bp.route("/api/dashboard/report-link", methods=["POST"])
@require_auth
def dashboard_report_link():
    """A link to view a run's HTML report (or one local test's) without leaving the dashboard.
    The browser names the run and test; the path comes from this user's own history (local) or from the
    coordinator's report for that run (grid) — never from the request."""
    body, bad = json_object()
    if bad:
        return bad
    source, run_id, uid = body.get("source"), body.get("run_id"), body.get("uid")
    if any(v is not None and not isinstance(v, str) for v in (source, run_id, uid)):      # null is the same as absent
        return jsonify({"error": "source, run_id and uid must be text"}), 400
    source, run_id, uid = source or consolidated.LOCAL, run_id or "", uid or ""
    if source not in (consolidated.GRID, consolidated.LOCAL):
        return jsonify({"error": "source must be grid or local"}), 400
    if source == consolidated.GRID:
        return _grid_report_link(run_id)                 # the report covers the whole run, so uid is ignored
    record = next((r for r in load_history(user_id=g.user.id)
                   if isinstance(r, dict) and consolidated.local_run_id(r) == run_id), None)
    if record is None:
        return jsonify({"error": "Run not found"}), 404
    raw, title = pc.text(record.get("report_path"), 4096), f"{pc.text(record.get('repo')) or 'Local run'} · run report"
    if uid:
        test = next((t for t in pc.dicts(record.get("tests")) if t.get("uid") == uid), None)
        if test is None:
            return jsonify({"error": "Test not found in that run"}), 404
        raw, title = pc.text(test.get("report_path"), 4096) or raw, pc.text(test.get("name")) or title
    report_file, anchor = report_viewer.resolve_report_file(raw)
    if report_file is None:
        return jsonify({"error": "That report is no longer on disk"}), 404
    token = report_viewer.mint(g.user.id, report_file)
    return jsonify({"url": f"/rv/{token}/{quote(report_file.name)}{anchor}", "title": title, "file": report_file.name})


@bp.route("/rv/<token>/")
@bp.route("/rv/<token>/<path:filepath>")
@require_auth
def serve_report_view(token: str, filepath: str = ""):
    claims = report_viewer.verify(token)
    if claims is None or claims["uid"] != g.user.id:        # someone else's link is just "not found"
        return "Report not found.", 404
    target = report_viewer.servable(claims["dir"], filepath or claims["entry"])
    if target is None:
        return "Report not found.", 404
    response = send_file(target, max_age=0)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = report_viewer.SANDBOX_CSP if claims["sandbox"] else report_viewer.DEFAULT_CSP
    return response


@bp.route("/api/report")
@require_auth
def get_report():
    repo = request.args.get("repo", "").strip()
    if not repo:
        return jsonify({"individual": None, "consolidated": None})
    cfg = UserConfigReader(g.user.id).load()
    resolver = ReportResolver(repo, cfg.get("report_paths", []))
    individual   = resolver.find_latest_in_dir(cfg.get("report_individual_dir", "allure/reports"))
    consolidated = resolver.find_latest_in_dir(cfg.get("report_consolidated_dir", ""))
    return jsonify({"individual": individual, "consolidated": consolidated})


@bp.route("/api/report/history")
@require_auth
def get_report_history():
    repo = request.args.get("repo", "").strip()
    return jsonify({"records": load_history(repo, user_id=g.user.id)})


@bp.route("/api/report/history/clear", methods=["POST"])
@require_auth
def clear_report_history():
    repo = (request.json or {}).get("repo", "").strip()
    save_history([], repo, user_id=g.user.id)
    return jsonify({"ok": True})
