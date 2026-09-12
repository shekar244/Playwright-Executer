"""
Blueprint: AI Studio routes
  GET  /studio/data          — allure-results parse + test file listing
  GET  /studio/file          — raw file content (?path=relative)
  POST /studio/save          — overwrite test file + trigger background re-run
  POST /studio/launch-rovo   — spawn edge_launcher.py in background
"""
from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
from pathlib import Path

from flask import Blueprint, jsonify, request

from ui_launcher.config_reader import ConfigReader
from ui_launcher.command_builder import resolve_python

bp = Blueprint("studio", __name__)


def _cfg():
    return ConfigReader().load()


def _allure_results_dir(cfg) -> Path:
    repo = cfg.get("repo_root", "").strip()
    rel  = cfg.get("allure_results_dir", "allure/results").strip()
    if repo:
        p = (Path(repo) / rel) if not Path(rel).is_absolute() else Path(rel)
    else:
        p = Path(rel)
    return p


def _tests_dir(cfg) -> Path:
    repo = cfg.get("repo_root", "").strip()
    if repo:
        return Path(repo) / "tests"
    return Path("tests")


def _parse_allure_results(results_dir: Path) -> dict[str, dict]:
    """Return {fullName: {status, message, trace, file}} from *.json in results_dir."""
    out: dict[str, dict] = {}
    if not results_dir.exists():
        return out
    pattern = str(results_dir / "*-result.json")
    files   = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
    for fpath in files:
        try:
            with open(fpath, encoding="utf-8", errors="replace") as fh:
                data = json.load(fh)
        except Exception:
            continue
        full_name = data.get("fullName", "") or data.get("name", "")
        if not full_name or full_name in out:
            continue
        details = data.get("statusDetails") or {}
        out[full_name] = {
            "status":  data.get("status", "unknown"),
            "message": details.get("message", ""),
            "trace":   details.get("trace", ""),
            "uid":     data.get("uuid", ""),
        }
    return out


def _match_file(full_name: str, tests_dir: Path) -> str | None:
    """Try to match a fullName string back to a .py file under tests_dir."""
    if not tests_dir.exists():
        return None
    # fullName often looks like "tests/test_checkout.py::TestClass::test_name"
    parts = full_name.replace("::", "/").split("/")
    for part in parts:
        if part.endswith(".py"):
            matches = list(tests_dir.rglob(part))
            if matches:
                return str(matches[0])
    # Fallback: try the first segment that starts with "test_"
    for part in parts:
        if part.startswith("test_") and not part.endswith(".py"):
            matches = list(tests_dir.rglob(part + ".py"))
            if matches:
                return str(matches[0])
    return None


@bp.route("/studio/data")
def studio_data():
    cfg         = _cfg()
    results_dir = _allure_results_dir(cfg)
    tests_dir   = _tests_dir(cfg)

    # Collect all test .py files
    test_files: list[dict] = []
    if tests_dir.exists():
        for f in sorted(tests_dir.rglob("test_*.py")):
            test_files.append({
                "path":    str(f),
                "relpath": str(f.relative_to(tests_dir.parent)) if tests_dir.parent in f.parents else f.name,
                "name":    f.name,
            })

    # Parse allure results
    results = _parse_allure_results(results_dir)

    # Map results → files
    for full_name, res in results.items():
        matched = _match_file(full_name, tests_dir)
        if matched:
            res["file"] = matched

    # Annotate test_files with status from latest result
    file_status: dict[str, str] = {}
    file_errors: dict[str, dict] = {}
    for full_name, res in results.items():
        fpath = res.get("file", "")
        if fpath and fpath not in file_status:
            file_status[fpath] = res["status"]
            if res["status"] in ("failed", "broken"):
                file_errors[fpath] = res

    for tf in test_files:
        tf["status"]  = file_status.get(tf["path"], "unknown")
        tf["error"]   = file_errors.get(tf["path"])

    return jsonify({
        "test_files":   test_files,
        "results":      results,
        "results_dir":  str(results_dir),
        "tests_dir":    str(tests_dir),
    })


@bp.route("/studio/file")
def studio_file():
    """
    Accept ?path= as an absolute path.
    Validates the file is under the configured tests_dir for safety.
    """
    cfg       = _cfg()
    tests_dir = _tests_dir(cfg).resolve()
    raw       = request.args.get("path", "").strip()
    if not raw:
        return jsonify({"error": "path param required"}), 400

    candidate = Path(raw).resolve()

    # Security: must be under tests_dir
    try:
        candidate.relative_to(tests_dir)
    except ValueError:
        return jsonify({"error": "Access denied — path outside tests directory"}), 403

    if not candidate.exists():
        return jsonify({"error": f"File not found: {candidate}"}), 404

    try:
        content = candidate.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return jsonify({"error": str(exc)}), 500

    return jsonify({"path": str(candidate), "content": content})


@bp.route("/studio/save", methods=["POST"])
def studio_save():
    cfg       = _cfg()
    tests_dir = _tests_dir(cfg)
    repo      = cfg.get("repo_root", "").strip()
    body      = request.json or {}
    rel       = body.get("path", "").strip()
    content   = body.get("content", "")

    if not rel:
        return jsonify({"error": "path required"}), 400

    candidate = Path(rel).resolve()
    try:
        candidate.relative_to(tests_dir.resolve())
    except ValueError:
        return jsonify({"error": "Access denied — path outside tests directory"}), 403

    try:
        candidate.write_text(content, encoding="utf-8")
    except OSError as exc:
        return jsonify({"error": str(exc)}), 500

    # Trigger background re-run
    results_dir = _allure_results_dir(cfg)
    results_dir.mkdir(parents=True, exist_ok=True)

    python  = resolve_python(repo, cfg.get("venv_path", "")) if repo else sys.executable
    cmd     = [python, "-m", "pytest", str(candidate),
               f"--alluredir={results_dir}", "-v", "--tb=short"]
    cwd     = repo or str(candidate.parent)
    try:
        subprocess.Popen(
            cmd, cwd=cwd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        return jsonify({"ok": True, "saved": True,
                        "rerun_warning": f"Could not start re-run: {exc}"})

    return jsonify({"ok": True, "saved": True, "rerun": True, "cmd": " ".join(cmd)})


@bp.route("/studio/launch-rovo", methods=["POST"])
def studio_launch_rovo():
    """Open Rovo + Studio URLs in the running Edge instance."""
    launcher = Path(__file__).parent.parent / "edge_launcher.py"
    if not launcher.exists():
        return jsonify({"error": "edge_launcher.py not found — check repo root"}), 404

    cfg      = _cfg()
    jira_url = cfg.get("jira_url", "").strip().rstrip("/")
    warnings = []
    if not jira_url:
        warnings.append("jira_url not configured — Rovo tab will be skipped. Set it in Config → Test Management.")

    try:
        result = subprocess.run(
            [sys.executable, str(launcher)],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "unknown error").strip()
            return jsonify({"error": err, "warnings": warnings}), 500
    except subprocess.TimeoutExpired:
        pass  # Normal — launcher opens URLs and exits; timeout just means it's still running
    except OSError as exc:
        return jsonify({"error": str(exc)}), 500

    return jsonify({"ok": True, "warnings": warnings})
