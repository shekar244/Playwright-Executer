"""
Playwright Test Executor — Web Server
Serves the browser UI and streams test output via Server-Sent Events.
"""

import base64
import csv
import datetime
import hashlib
import hmac
import io
import json
import os
import queue
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory

_ROOT = Path(__file__).parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from ui_launcher.config_reader import ConfigReader
from ui_launcher.command_builder import CommandBuilder
from ui_launcher.report_resolver import ReportResolver
from ui_launcher.runner import TestRunner
from ui_launcher.test_discovery import TestDiscovery

app = Flask(__name__, static_folder="static")

HISTORY_FILE = _ROOT / "report_history.json"
ZEPHYR_CONFIG_KEY = "zephyr"

# ── Global run state (single-user local tool) ──────────────────────────────────
_runner: TestRunner | None = None
_is_running = False
_output_queues: list[queue.Queue] = []
_run_lock = threading.Lock()


def _display_cmd(cmd: list[str], repo: str) -> str:
    """Return a human-readable command with absolute paths replaced by relative ones."""
    repo_path = str(Path(repo).resolve())
    parts = []
    for token in cmd:
        t = str(token)
        # Python interpreter → just "python"
        if t.endswith(("python", "python3", "python.exe", "python3.exe")):
            parts.append("python")
            continue
        # Strip repo root prefix from any path token
        resolved = str(Path(t).resolve()) if (Path(t).exists() or t.startswith("/") or ":\\" in t) else t
        if resolved.startswith(repo_path + os.sep):
            parts.append(resolved[len(repo_path) + 1:].replace("\\", "/"))
        elif resolved.startswith(repo_path + "/"):
            parts.append(resolved[len(repo_path) + 1:])
        else:
            parts.append(t)
    # Quote any token that contains spaces so the display is copy-pasteable
    return " ".join(f'"{p}"' if " " in p else p for p in parts)


def _parse_allure_results(results_dir: str) -> dict:
    """Parse allure *-result.json files and return aggregated status counts."""
    path = Path(results_dir)
    if not path.is_dir():
        return {"passed": 0, "failed": 0, "broken": 0, "skipped": 0, "total": 0}
    counts: dict = {"passed": 0, "failed": 0, "broken": 0, "skipped": 0}
    for f in path.glob("*-result.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            s = data.get("status", "unknown").lower()
            if s in counts:
                counts[s] += 1
            else:
                counts["failed"] += 1
        except Exception:
            pass
    counts["total"] = sum(counts.values())
    return counts


def _load_history() -> list:
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def _save_history(records: list) -> None:
    HISTORY_FILE.write_text(json.dumps(records, indent=2), encoding="utf-8")


def _record_run_history(repo: str, status: str, cfg: dict) -> None:
    results_rel = cfg.get("allure_results_dir", "allure/results")
    results_dir = str(Path(repo) / results_rel) if repo else ""
    stats = _parse_allure_results(results_dir) if results_dir else {"passed": 0, "failed": 0, "broken": 0, "skipped": 0, "total": 0}
    record = {
        "ts": int(time.time() * 1000),
        "date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "repo": Path(repo).name if repo else "unknown",
        "status": status,
        "stats": stats,
    }
    records = _load_history()
    records.insert(0, record)
    _save_history(records[:200])


def _broadcast(event: str, data: str) -> None:
    msg = f"event: {event}\ndata: {json.dumps(data)}\n\n"
    for q in list(_output_queues):
        try:
            q.put_nowait(msg)
        except queue.Full:
            pass


# ── Static / index ─────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/readme")
def readme():
    """Render the Playwright-Executer's own README.md as a styled HTML page."""
    readme_path = _ROOT / "README.md"
    try:
        md = readme_path.read_text(encoding="utf-8")
    except OSError:
        return "<p>README.md not found.</p>", 404
    source_label = "Playwright-Executer"

    # Minimal Markdown → HTML (headings, code blocks, tables, bold, lists)
    import re, html as html_lib

    lines = md.split("\n")
    out, in_code, in_table = [], False, False

    for line in lines:
        if line.startswith("```"):
            if in_code:
                out.append("</code></pre>"); in_code = False
            else:
                out.append("<pre><code>"); in_code = True
            continue
        if in_code:
            out.append(html_lib.escape(line)); continue

        # Tables
        if "|" in line and line.strip().startswith("|"):
            if not in_table:
                out.append('<table>'); in_table = True
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if all(re.match(r"^[-:]+$", c) for c in cells):
                continue  # skip separator row
            tag = "th" if not any(r.strip().startswith("<th") for r in out[-3:]) else "td"
            out.append("<tr>" + "".join(f"<{tag}>{html_lib.escape(c)}</{tag}>" for c in cells) + "</tr>")
            continue
        elif in_table:
            out.append("</table>"); in_table = False

        line = html_lib.escape(line)
        # Headings
        m = re.match(r"^(#{1,4})\s+(.*)", line)
        if m:
            lvl = len(m.group(1))
            out.append(f"<h{lvl}>{m.group(2)}</h{lvl}>"); continue
        # Horizontal rule
        if re.match(r"^---+$", line.strip()):
            out.append("<hr>"); continue
        # Inline: bold, code, links
        line = re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", line)
        line = re.sub(r"`(.*?)`", r"<code>\1</code>", line)
        line = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', line)
        # Lists
        if re.match(r"^[-*]\s", line):
            out.append(f"<li>{line[2:]}</li>"); continue
        if re.match(r"^\d+\.\s", line):
            _li_text = re.sub(r"^\d+\.\s", "", line)
            out.append(f"<li>{_li_text}</li>"); continue
        out.append(f"<p>{line}</p>" if line.strip() else "")

    if in_table: out.append("</table>")
    body = "\n".join(out)

    page_title = f"{source_label} — README" if source_label else "README"
    return f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{page_title}</title>
<style>
  body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
        max-width:860px;margin:40px auto;padding:0 24px;
        background:#1e1e2e;color:#cdd6f4;line-height:1.7;font-size:14px}}
  h1{{color:#89b4fa;border-bottom:1px solid #45475a;padding-bottom:8px}}
  h2{{color:#89b4fa;margin-top:36px;border-bottom:1px solid #313244;padding-bottom:4px}}
  h3,h4{{color:#cba6f7}}
  code{{background:#313244;padding:2px 6px;border-radius:4px;font-size:12px;font-family:Menlo,monospace}}
  pre{{background:#11111b;border:1px solid #313244;border-radius:6px;padding:16px;overflow-x:auto}}
  pre code{{background:none;padding:0;font-size:12px}}
  table{{border-collapse:collapse;width:100%;margin:12px 0}}
  th,td{{border:1px solid #45475a;padding:8px 12px;text-align:left}}
  th{{background:#313244;color:#cba6f7}}
  hr{{border:none;border-top:1px solid #45475a;margin:28px 0}}
  a{{color:#89b4fa}}
  li{{margin:3px 0}}
  p:empty{{display:none}}
</style></head><body>{body}</body></html>"""


@app.route("/static/<path:path>")
def static_files(path):
    return send_from_directory("static", path)


# ── Config ─────────────────────────────────────────────────────────────────────

@app.route("/api/config")
def get_config():
    cfg = ConfigReader().load()
    return jsonify(cfg)


@app.route("/api/config/features", methods=["POST"])
def save_features():
    """Persist the features list back to config.json."""
    body = request.json or {}
    features = body.get("features")
    if not isinstance(features, list):
        return jsonify({"error": "features must be an array"}), 400
    reader = ConfigReader()
    cfg = reader.load()
    cfg["features"] = features
    try:
        reader.save(cfg)
    except OSError as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True})


@app.route("/api/features/run", methods=["POST"])
def run_feature():
    """Run a saved feature (Python / Node.js / shell script)."""
    global _runner, _is_running

    with _run_lock:
        if _is_running:
            return jsonify({"error": "Already running"}), 409

        body = request.json or {}
        feat = body.get("feature", {})

        runtime = feat.get("runtime", "python")
        script  = feat.get("script", "").strip()
        cwd     = feat.get("cwd", "").strip()
        args    = feat.get("args", "").strip()

        if not script:
            return jsonify({"error": "No script specified"}), 400

        # Resolve working directory
        if not cwd:
            cwd = str(Path(script).parent) if Path(script).exists() else str(_ROOT)
        if not os.path.isdir(cwd):
            cwd = str(_ROOT)

        # Build command
        if runtime == "python":
            cmd = [sys.executable, script]
        elif runtime == "node":
            cmd = ["node", script]
        else:  # shell
            cmd = [script]

        if args:
            import shlex
            cmd.extend(shlex.split(args))

        display = " ".join(cmd)
        _broadcast("cmd", display)
        _is_running = True

        def on_output(line):
            _broadcast("line", line)

        def on_finish(exit_code, cancelled):
            global _is_running
            _is_running = False
            if cancelled:
                _broadcast("status", "cancelled")
            elif exit_code == 0:
                _broadcast("status", "passed")
            else:
                _broadcast("status", f"failed:{exit_code}")
            _broadcast("done", "")

        _runner = TestRunner(
            cmd=cmd,
            cwd=cwd,
            env_overrides={},
            on_output=on_output,
            on_finish=on_finish,
        )
        _runner.start()

    return jsonify({"ok": True, "cmd": display})


@app.route("/api/config/tools", methods=["POST"])
def save_tools():
    """Persist the extra_options list back to config.json."""
    body = request.json or {}
    tools = body.get("extra_options")
    if not isinstance(tools, list):
        return jsonify({"error": "extra_options must be an array"}), 400

    reader = ConfigReader()
    cfg = reader.load()
    cfg["extra_options"] = tools
    try:
        reader.save(cfg)
    except OSError as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True})


# ── Repo scanner ───────────────────────────────────────────────────────────────

def _looks_like_playwright_repo(path: Path) -> bool:
    """Heuristic: has tests/ dir + at least one config file."""
    if not (path / "tests").is_dir():
        return False
    config_markers = [
        path / "pytest.ini",
        path / "pyproject.toml",
        path / "setup.cfg",
        path / "requirements.txt",
        path / "config" / "pytest.ini",
    ]
    return any(m.exists() for m in config_markers)


@app.route("/api/repos")
def get_repos():
    """Scan common local directories for Playwright Python repos."""
    home = Path.home()
    scan_dirs = [
        home / "Documents" / "GitHub",
        home / "GitHub",
        home / "Projects",
        home / "Desktop",
        home / "Documents",
        _ROOT.parent,               # sibling of Playwright-Executer
    ]

    seen: set[str] = set()
    repos: list[dict] = []

    for scan_dir in scan_dirs:
        if not scan_dir.is_dir():
            continue
        try:
            for item in sorted(scan_dir.iterdir()):
                key = str(item.resolve())
                if item.is_dir() and key not in seen and _looks_like_playwright_repo(item):
                    seen.add(key)
                    repos.append({"name": item.name, "path": str(item)})
        except PermissionError:
            pass

    return jsonify({"repos": repos})


# ── Test discovery ─────────────────────────────────────────────────────────────

@app.route("/api/tests")
def get_tests():
    repo = request.args.get("repo", "").strip()
    if not repo or not os.path.isdir(repo):
        return jsonify({"error": "Repo path not found"}), 400
    try:
        disc = TestDiscovery(repo)
        tree = disc.discover()
        markers = disc.discover_markers()
        return jsonify({
            "tree": {suite: [str(p) for p in files] for suite, files in tree.items()},
            "markers": markers,
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── Test name discovery ────────────────────────────────────────────────────────

@app.route("/api/tests/names")
def get_test_names():
    """Parse test function names from a specific test file."""
    import ast
    repo = request.args.get("repo", "").strip()
    file_rel = request.args.get("file", "").strip()
    if not repo or not file_rel:
        return jsonify({"names": []})

    # Resolve the file — file_rel may be just a basename or a relative path
    repo_path = Path(repo)
    candidate = repo_path / file_rel
    if not candidate.exists():
        # Try matching by basename across the repo's tests dir
        for match in (repo_path / "tests").rglob(file_rel):
            candidate = match
            break

    if not candidate.exists() or not candidate.is_file():
        return jsonify({"names": []})

    names: list[str] = []
    try:
        tree = ast.parse(candidate.read_text(encoding="utf-8", errors="replace"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("test_"):
                    names.append(node.name)
    except SyntaxError:
        pass

    return jsonify({"names": names})


# ── Run / Stop ─────────────────────────────────────────────────────────────────

@app.route("/api/run", methods=["POST"])
def run_tests():
    global _runner, _is_running

    with _run_lock:
        if _is_running:
            return jsonify({"error": "Already running"}), 409

        body = request.json or {}
        repo = body.get("repo", "").strip()
        if not repo or not os.path.isdir(repo):
            return jsonify({"error": "Repo path not found"}), 400

        try:
            cfg = ConfigReader().load()
            disc = TestDiscovery(repo)
            test_tree = disc.discover()

            # Build extra_flags from config-defined extra_options + UI selections
            extra_option_values: dict = body.get("extra_option_values", {})
            extra_flags: list[str] = []
            for opt in cfg.get("extra_options", []):
                flag = opt.get("flag", "")
                opt_type = opt.get("type", "checkbox")
                val = extra_option_values.get(flag)
                if opt_type == "checkbox":
                    if val:
                        extra_flags.append(flag)
                elif opt_type in ("dropdown", "text"):
                    v = str(val).strip() if val is not None else ""
                    if v and v.lower() not in ("none", "(none)"):
                        # Wrap in quotes if the value contains spaces
                        extra_flags.extend([flag, f'"{v}"' if " " in v else v])

            builder = CommandBuilder(repo)
            cmd = builder.build(
                suite=body.get("suite", "All Tests"),
                file_sel=body.get("file_sel", "All in Suite"),
                k_filter=body.get("k_filter", "").strip(),
                browser=body.get("browser", "chromium"),
                marker=body.get("marker") or None,
                workers=int(body.get("workers", 1)),
                verbose=bool(body.get("verbose", False)),
                headed=bool(body.get("headed", True)),
                extra=body.get("extra", "").strip(),
                test_tree=test_tree,
                extra_flags=extra_flags,
                allure_results_dir=cfg.get("allure_results_dir", "allure/results"),
            )

        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

        _broadcast("cmd", _display_cmd(cmd, repo))
        _is_running = True

        def on_output(line):
            _broadcast("line", line)

        def on_finish(exit_code, cancelled):
            global _is_running
            _is_running = False
            if cancelled:
                run_status = "cancelled"
            elif exit_code == 0:
                run_status = "passed"
            else:
                run_status = f"failed:{exit_code}"
            _broadcast("status", run_status)
            _record_run_history(repo, run_status, cfg)
            _broadcast("done", "")

        _runner = TestRunner(
            cmd=cmd,
            cwd=repo,
            env_overrides={},
            on_output=on_output,
            on_finish=on_finish,
        )
        _runner.start()

    return jsonify({"ok": True, "cmd": _display_cmd(cmd, repo)})


@app.route("/api/stop", methods=["POST"])
def stop_tests():
    global _runner
    if _runner and _is_running:
        _runner.stop()
        return jsonify({"ok": True})
    return jsonify({"error": "Not running"}), 400


@app.route("/api/status")
def get_status():
    return jsonify({"running": _is_running})


# ── SSE stream ─────────────────────────────────────────────────────────────────

@app.route("/api/stream")
def stream():
    q: queue.Queue = queue.Queue(maxsize=500)
    _output_queues.append(q)

    def generate():
        try:
            yield "event: connected\ndata: \"ok\"\n\n"
            while True:
                try:
                    msg = q.get(timeout=25)
                    yield msg
                except queue.Empty:
                    yield ": heartbeat\n\n"
        finally:
            if q in _output_queues:
                _output_queues.remove(q)

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── Report ─────────────────────────────────────────────────────────────────────

@app.route("/api/report")
def get_report():
    repo = request.args.get("repo", "").strip()
    if not repo:
        return jsonify({"individual": None, "consolidated": None})
    cfg = ConfigReader().load()
    resolver = ReportResolver(repo, cfg.get("report_paths", []))

    individual   = resolver.find_latest_in_dir(cfg.get("report_individual_dir", "allure/reports"))
    consolidated = resolver.find_latest_in_dir(cfg.get("report_consolidated_dir", ""))

    return jsonify({"individual": individual, "consolidated": consolidated})


@app.route("/api/report/open", methods=["POST"])
def open_report():
    """Open the report file using the OS default browser — avoids file:// cross-origin block."""
    import webbrowser
    body = request.json or {}
    path = body.get("path", "").strip()
    if not path or not os.path.exists(path):
        return jsonify({"error": "Report file not found"}), 404
    webbrowser.open(f"file://{path}")
    return jsonify({"ok": True})


# ── Report History ─────────────────────────────────────────────────────────────

def _parse_allure_results_full(results_dir: str) -> list:
    """Parse every *-result.json in the allure results dir."""
    path = Path(results_dir)
    if not path.is_dir():
        return []
    tests = []
    for f in path.glob("*-result.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            labels = {l.get("name", ""): l.get("value", "")
                      for l in data.get("labels", []) if l.get("name")}
            status = data.get("status", "unknown").lower()
            start  = data.get("start") or 0
            stop   = data.get("stop")  or 0
            tests.append({
                "name":      data.get("name", f.stem),
                "fullName":  data.get("fullName", ""),
                "status":    status,
                "start":     start,
                "stop":      stop,
                "duration":  max(0, stop - start),
                "suite":     labels.get("suite") or labels.get("parentSuite") or labels.get("feature") or "",
                "historyId": data.get("historyId", ""),
                "source":    "allure",
            })
        except Exception:
            pass
    return sorted(tests, key=lambda x: x["start"], reverse=True)


def _parse_smart_reporter(report_dir: str) -> dict:
    """Parse .smart-reporter-data.json and smart-reporter-history.json."""
    base = Path(report_dir)
    results, summaries = [], []

    # Current run results
    data_file = base / ".smart-reporter-data.json"
    if data_file.exists():
        try:
            raw = json.loads(data_file.read_text(encoding="utf-8"))
            start_ts = raw.get("startTime", 0)
            for r in raw.get("results", []):
                tid = r.get("testId", "")
                # Extract class and method from testId  (::Class::method[param])
                parts = [p for p in tid.split("::") if p]
                suite  = parts[-2] if len(parts) >= 2 else (parts[0] if parts else "")
                name   = parts[-1] if parts else tid
                status = (r.get("status") or r.get("outcome", "unknown")).lower()
                # Map smart-reporter statuses to allure statuses
                if status in ("expected", "passed", "pass"):
                    status = "passed"
                elif status in ("unexpected", "failed", "fail"):
                    status = "failed"
                elif status == "broken":
                    status = "broken"
                elif status in ("skipped", "skip", "xfailed", "xpassed"):
                    status = "skipped"
                dur = int((r.get("duration") or 0) * 1000)
                results.append({
                    "name":     name,
                    "fullName": r.get("title", tid),
                    "status":   status,
                    "start":    start_ts,
                    "stop":     start_ts + dur,
                    "duration": dur,
                    "suite":    suite,
                    "tags":     r.get("tags", []),
                    "source":   "smart-reporter",
                })
        except Exception:
            pass

    # pytest-report.json fallback/supplement
    pr_file = base / "pytest-report.json"
    if not results and pr_file.exists():
        try:
            raw = json.loads(pr_file.read_text(encoding="utf-8"))
            created_ts = int((raw.get("created") or 0) * 1000)
            for t in raw.get("tests", []):
                nid = t.get("nodeid", "")
                parts = [p for p in nid.split("::") if p]
                suite = parts[-2] if len(parts) >= 2 else ""
                name  = parts[-1] if parts else nid
                status = (t.get("outcome") or "unknown").lower()
                if status not in ("passed", "failed", "broken", "skipped"):
                    status = "failed" if status == "error" else "skipped"
                call = t.get("call") or {}
                dur  = int((call.get("duration") or 0) * 1000)
                results.append({
                    "name": name, "fullName": nid, "status": status,
                    "start": created_ts, "stop": created_ts + dur,
                    "duration": dur, "suite": suite,
                    "source": "pytest",
                })
        except Exception:
            pass

    # Historical summaries
    hist_file = base / "smart-reporter-history.json"
    if hist_file.exists():
        try:
            raw = json.loads(hist_file.read_text(encoding="utf-8"))
            summaries = raw.get("summaries", [])
        except Exception:
            pass

    return {"results": results, "summaries": summaries}


def _parse_allure_history_trend(repo: str, cfg: dict) -> list:
    """Read allure history-trend.json from the reports directory if it exists."""
    for rel in [
        cfg.get("report_consolidated_dir", ""),
        "allure/reports/history",
        "allure/reports",
        "allure-report",
    ]:
        if not rel:
            continue
        p = Path(repo) / rel / "history-trend.json"
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
    return []


@app.route("/api/dashboard")
def get_dashboard():
    """Return parsed allure/smart-reporter results + history for the dashboard."""
    repo = request.args.get("repo", "").strip()
    cfg  = ConfigReader().load()

    tests, summaries, allure_trend = [], [], []

    if repo:
        # 1. Try standard allure *-result.json files
        results_dir = str(Path(repo) / cfg.get("allure_results_dir", "allure/results"))
        allure_tests = _parse_allure_results_full(results_dir)

        # 2. Try smart-reporter JSON files in the reports dir
        report_dir = str(Path(repo) / cfg.get("report_individual_dir", "allure/reports"))
        smart = _parse_smart_reporter(report_dir)

        # Merge: prefer allure if more results, else smart-reporter
        tests = allure_tests if len(allure_tests) >= len(smart["results"]) else smart["results"]
        if not tests:
            tests = allure_tests or smart["results"]
        summaries = smart["summaries"]
        allure_trend = _parse_allure_history_trend(repo, cfg)

    return jsonify({
        "tests":      tests,
        "summaries":  summaries,  # smart-reporter run history
        "trend":      allure_trend,
        "run_history": _load_history(),
    })


@app.route("/api/report/history")
def get_report_history():
    return jsonify({"records": _load_history()})


@app.route("/api/report/history/clear", methods=["POST"])
def clear_report_history():
    _save_history([])
    return jsonify({"ok": True})


# ── Zephyr for Jira Cloud Integration ─────────────────────────────────────────
# Uses ZAPI Cloud: https://prod-api.zephyr4jiracloud.com/connect
# Auth: JWT HS256 with Access Key + Secret Key; Jira REST uses Basic Auth.

ZAPI_BASE = "https://prod-api.zephyr4jiracloud.com/connect"
JIRA_REST = "/rest/api/2"

STATUS_MAP = {
    "pass": 1, "passed": 1, "p": 1,
    "fail": 2, "failed": 2, "f": 2,
    "wip": 3, "in progress": 3,
    "blocked": 4,
    "unexecuted": -1, "skip": -1, "skipped": -1,
}


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _zephyr_jwt(access_key: str, secret_key: str, account_id: str,
                method: str, path: str, query_params: dict | None = None) -> str:
    """Generate a Zephyr for Jira Cloud JWT token (HS256, no external library)."""
    params = {k: str(v) for k, v in (query_params or {}).items() if v is not None}
    sorted_qs = "&".join(
        f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(v, safe='')}"
        for k, v in sorted(params.items())
    )
    canonical = f"{method.upper()}&{path}&{sorted_qs}"
    qsh = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    now = int(time.time())
    header  = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64url(json.dumps({
        "sub": account_id, "qsh": qsh, "iss": access_key,
        "iat": now, "exp": now + 3600,
    }, separators=(",", ":")).encode())
    msg = f"{header}.{payload}"
    sig = _b64url(hmac.new(secret_key.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).digest())
    return f"{msg}.{sig}"


def _z_cfg() -> dict:
    return ConfigReader().load().get(ZEPHYR_CONFIG_KEY, {})


def _z_headers(method: str, path: str, params: dict | None = None) -> dict:
    cfg = _z_cfg()
    token = _zephyr_jwt(
        cfg.get("access_key", ""), cfg.get("secret_key", ""),
        cfg.get("account_id", ""), method, path, params,
    )
    return {
        "Authorization": f"JWT {token}",
        "zapiAccessKey": cfg.get("access_key", ""),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _z_call(method: str, path: str, params: dict | None = None, body=None) -> tuple:
    """Authenticated ZAPI call. Returns (response_dict, status_code)."""
    cfg = _z_cfg()
    if not cfg.get("access_key"):
        return {"error": "Zephyr not configured — add Access Key, Secret Key and Account ID"}, 400
    hdrs = _z_headers(method, path, params)
    url = ZAPI_BASE + path
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    data = json.dumps(body).encode() if body is not None else None
    try:
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method.upper())
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
            return (json.loads(raw) if raw else {}), resp.status
    except urllib.error.HTTPError as e:
        try:    err = json.loads(e.read().decode())
        except: err = {"message": str(e)}
        return {"error": err}, e.code
    except Exception as ex:
        return {"error": str(ex)}, 500


def _jira_call(method: str, path: str, params: dict | None = None, body=None) -> tuple:
    """Jira REST API call with Basic Auth (username + API token)."""
    cfg = _z_cfg()
    jira_url = cfg.get("jira_url", "").rstrip("/")
    if not jira_url:
        return {"error": "Jira URL not configured"}, 400
    creds = base64.b64encode(
        f"{cfg.get('username','')}:{cfg.get('api_token','')}".encode()
    ).decode()
    hdrs = {"Authorization": f"Basic {creds}", "Content-Type": "application/json", "Accept": "application/json"}
    url = jira_url + JIRA_REST + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    try:
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method.upper())
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
            return (json.loads(raw) if raw else {}), resp.status
    except urllib.error.HTTPError as e:
        try:    err = json.loads(e.read().decode())
        except: err = {"message": str(e)}
        return {"error": err}, e.code
    except Exception as ex:
        return {"error": str(ex)}, 500


def _multipart(fields: dict, file_field: str, filename: str, file_bytes: bytes, mime: str = "application/octet-stream") -> tuple[bytes, str]:
    """Build a multipart/form-data body. Returns (body_bytes, content_type_header)."""
    boundary = b"----ZephyrBound7MA4YW"
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(b"--" + boundary + b"\r\n"
                     b'Content-Disposition: form-data; name="' + name.encode() + b'"\r\n\r\n'
                     + str(value).encode() + b"\r\n")
    parts.append(b"--" + boundary + b"\r\n"
                 + f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'.encode()
                 + f"Content-Type: {mime}\r\n\r\n".encode()
                 + file_bytes + b"\r\n")
    parts.append(b"--" + boundary + b"--\r\n")
    return b"".join(parts), f"multipart/form-data; boundary={boundary.decode()}"


# ── Config ────────────────────────────────────────────────────────────────────

@app.route("/api/zephyr/config", methods=["GET", "POST"])
def zephyr_config():
    reader = ConfigReader()
    cfg = reader.load()
    if request.method == "POST":
        body = request.json or {}
        cfg[ZEPHYR_CONFIG_KEY] = body
        try:
            reader.save(cfg)
        except OSError as exc:
            return jsonify({"error": str(exc)}), 500
        return jsonify({"ok": True})
    return jsonify(cfg.get(ZEPHYR_CONFIG_KEY, {}))


# ── Jira Projects & Versions ──────────────────────────────────────────────────

@app.route("/api/zephyr/projects")
def zephyr_projects():
    data, code = _jira_call("GET", "/project")
    return jsonify(data), code


@app.route("/api/zephyr/versions")
def zephyr_versions():
    pid = request.args.get("projectKey", "").strip()
    if not pid:
        return jsonify({"error": "projectKey required"}), 400
    data, code = _jira_call("GET", f"/project/{pid}/versions")
    return jsonify(data), code


# ── Cycles ────────────────────────────────────────────────────────────────────

@app.route("/api/zephyr/cycles")
def zephyr_cycles():
    path = "/public/rest/api/1.0/cycles/search"
    params = {k: request.args.get(k) for k in ("projectId", "versionId") if request.args.get(k)}
    data, code = _z_call("GET", path, params)
    return jsonify(data), code


@app.route("/api/zephyr/cycle", methods=["POST"])
def create_cycle():
    body = request.json or {}
    data, code = _z_call("POST", "/public/rest/api/1.0/cycle", body=body)
    return jsonify(data), code


# ── Folders ───────────────────────────────────────────────────────────────────

@app.route("/api/zephyr/folders")
def zephyr_folders():
    path = "/public/rest/api/1.0/folders"
    params = {k: request.args.get(k) for k in ("projectId", "versionId", "cycleId") if request.args.get(k)}
    data, code = _z_call("GET", path, params)
    return jsonify(data), code


@app.route("/api/zephyr/folder", methods=["POST"])
def create_folder():
    data, code = _z_call("POST", "/public/rest/api/1.0/folder", body=request.json or {})
    return jsonify(data), code


# ── Add Tests to Cycle / Folder ───────────────────────────────────────────────

@app.route("/api/zephyr/executions/add", methods=["POST"])
def add_tests_to_cycle():
    body = request.json or {}
    cycle_id  = body.pop("cycleId", "")
    folder_id = body.pop("folderId", None)
    if folder_id:
        path = f"/public/rest/api/1.0/executions/add/folder/{folder_id}"
        body["cycleId"] = cycle_id
    else:
        path = f"/public/rest/api/1.0/executions/add/cycle/{cycle_id}"
    data, code = _z_call("POST", path, body=body)
    return jsonify(data), code


# ── Executions ────────────────────────────────────────────────────────────────

@app.route("/api/zephyr/executions")
def list_executions():
    cycle_id  = request.args.get("cycleId", "")
    folder_id = request.args.get("folderId", "")
    params = {k: request.args.get(k) for k in ("projectId", "versionId") if request.args.get(k)}
    if folder_id:
        path = f"/public/rest/api/1.0/executions/search/folder/{folder_id}"
        params["cycleId"] = cycle_id
    else:
        path = f"/public/rest/api/1.0/executions/search/cycle/{cycle_id}"
    params.update({k: request.args.get(k) for k in ("offset", "size") if request.args.get(k)})
    data, code = _z_call("GET", path, params)
    return jsonify(data), code


@app.route("/api/zephyr/execution/<exec_id>", methods=["PUT"])
def update_execution(exec_id: str):
    data, code = _z_call("PUT", f"/public/rest/api/1.0/execution/{exec_id}", body=request.json or {})
    return jsonify(data), code


@app.route("/api/zephyr/executions/bulk", methods=["POST"])
def bulk_update_executions():
    data, code = _z_call("POST", "/public/rest/api/1.0/executions", body=request.json or {})
    return jsonify(data), code


# ── Step Results ──────────────────────────────────────────────────────────────

@app.route("/api/zephyr/stepresults")
def get_step_results():
    params = {k: request.args.get(k) for k in ("executionId", "issueId") if request.args.get(k)}
    data, code = _z_call("GET", "/public/rest/api/1.0/stepresult/search", params)
    return jsonify(data), code


@app.route("/api/zephyr/stepresult/<sr_id>", methods=["PUT"])
def update_step_result(sr_id: str):
    data, code = _z_call("PUT", f"/public/rest/api/1.0/stepresult/{sr_id}", body=request.json or {})
    return jsonify(data), code


# ── Attachments ───────────────────────────────────────────────────────────────

@app.route("/api/zephyr/attach", methods=["POST"])
def zephyr_attach():
    cfg = _z_cfg()
    if not cfg.get("access_key"):
        return jsonify({"error": "Zephyr not configured"}), 400
    file = request.files.get("file")
    if not file:
        return jsonify({"error": "No file provided"}), 400

    file_bytes = file.read()
    filename   = file.filename or "attachment"
    path       = "/public/rest/api/1.0/attachment"
    params     = {k: request.form.get(k) for k in
                  ("projectId", "issueId", "versionId", "cycleId", "entityId", "entityName")
                  if request.form.get(k)}

    token = _zephyr_jwt(cfg.get("access_key",""), cfg.get("secret_key",""),
                        cfg.get("account_id",""), "POST", path, params)

    body_bytes, ct = _multipart({}, "file", filename, file_bytes)
    url = ZAPI_BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
    hdrs = {"Authorization": f"JWT {token}", "zapiAccessKey": cfg.get("access_key",""), "Content-Type": ct}

    try:
        req = urllib.request.Request(url, data=body_bytes, headers=hdrs, method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            return jsonify(json.loads(resp.read().decode()))
    except urllib.error.HTTPError as e:
        try:    err = json.loads(e.read().decode())
        except: err = {"message": str(e)}
        return jsonify({"error": err}), e.code
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500


# ── Bulk Results Upload ───────────────────────────────────────────────────────

@app.route("/api/zephyr/bulk-results", methods=["POST"])
def bulk_results_upload():
    """Parse a results CSV and bulk-update Zephyr execution statuses."""
    file      = request.files.get("file")
    cycle_id  = request.form.get("cycleId", "")
    folder_id = request.form.get("folderId", "")
    project_id = request.form.get("projectId", "")
    version_id = request.form.get("versionId", "-1")
    attachment = request.files.get("attachment")

    if not file:
        return jsonify({"error": "No CSV file provided"}), 400

    content = file.read().decode("utf-8-sig")
    reader  = csv.DictReader(io.StringIO(content))
    rows    = list(reader)

    # Get all executions from the cycle/folder
    params = {"projectId": project_id, "versionId": version_id, "size": "500"}
    if folder_id:
        path = f"/public/rest/api/1.0/executions/search/folder/{folder_id}"
        params["cycleId"] = cycle_id
    else:
        path = f"/public/rest/api/1.0/executions/search/cycle/{cycle_id}"
    exec_data, _ = _z_call("GET", path, params)
    exec_list = exec_data.get("searchObjectList", [])

    # Build lookup: issueKey → execution
    exec_by_key = {e.get("issueKey", ""): e for e in exec_list}
    exec_by_id  = {str(e.get("issueId", "")): e for e in exec_list}

    results: dict = {"success": [], "errors": [], "not_found": []}

    for row in rows:
        # Flexible column name matching
        issue_key = (row.get("Issue Key") or row.get("Test ID") or row.get("Jira ID")
                     or row.get("issueKey") or row.get("Key") or "").strip()
        status_str = (row.get("Status") or row.get("Result") or row.get("result") or "pass").strip().lower()
        comment    = (row.get("Comment") or row.get("Notes") or row.get("comment") or "").strip()
        status_id  = STATUS_MAP.get(status_str, 1)

        if not issue_key:
            continue

        exec_obj = exec_by_key.get(issue_key) or exec_by_id.get(issue_key)
        if not exec_obj:
            results["not_found"].append(issue_key)
            continue

        exec_id  = exec_obj.get("id") or exec_obj.get("executionId", "")
        issue_id = exec_obj.get("issueId")
        update_body = {
            "status": {"id": status_id},
            "id": exec_id,
            "projectId": int(project_id) if project_id.isdigit() else project_id,
            "issueId": issue_id,
            "cycleId": cycle_id,
            "versionId": int(version_id) if str(version_id).lstrip("-").isdigit() else -1,
            "comment": comment,
            "testStepStatusChangeFlag": True,
            "stepStatus": status_id,
        }
        upd, upd_code = _z_call("PUT", f"/public/rest/api/1.0/execution/{exec_id}", body=update_body)
        if upd_code in (200, 201):
            results["success"].append({"issue": issue_key, "status": status_str, "execId": exec_id})
            # Attach the report file to this execution if provided
            if attachment and exec_id:
                attach_params = {
                    "projectId": project_id, "issueId": str(issue_id),
                    "versionId": version_id, "cycleId": cycle_id,
                    "entityId": exec_id, "entityName": "EXECUTION",
                }
                attachment.seek(0)
                ab = attachment.read()
                cfg = _z_cfg()
                path_att = "/public/rest/api/1.0/attachment"
                tok = _zephyr_jwt(cfg.get("access_key",""), cfg.get("secret_key",""),
                                   cfg.get("account_id",""), "POST", path_att, attach_params)
                bb, ct = _multipart({}, "file", attachment.filename or "report.html", ab)
                url_att = ZAPI_BASE + path_att + "?" + urllib.parse.urlencode(attach_params)
                hdrs_att = {"Authorization": f"JWT {tok}", "zapiAccessKey": cfg.get("access_key",""), "Content-Type": ct}
                try:
                    req = urllib.request.Request(url_att, data=bb, headers=hdrs_att, method="POST")
                    urllib.request.urlopen(req, timeout=30).close()
                except Exception:
                    pass
        else:
            results["errors"].append({"issue": issue_key, "error": str(upd)})

    return jsonify(results)


# ── Native folder picker ───────────────────────────────────────────────────────

@app.route("/api/browse-folder", methods=["POST"])
def browse_folder():
    """Open the native OS folder picker and return the chosen path."""
    import platform
    import subprocess
    body       = request.json or {}
    start_path = body.get("start", "").strip() or str(Path.home())
    if not Path(start_path).is_dir():
        start_path = str(Path.home())
    system = platform.system()

    # ── macOS: osascript only (tkinter crashes on non-main thread via AppKit) ──
    if system == "Darwin":
        script = (
            'set chosen to POSIX path of '
            f'(choose folder with prompt "Select Playwright repo root:" '
            f'default location POSIX file "{start_path}")'
        )
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode == 0 and result.stdout.strip():
                return jsonify({"path": result.stdout.strip().rstrip("/")})
            # returncode 1 = user cancelled — not an error
            return jsonify({"cancelled": True})
        except subprocess.TimeoutExpired:
            return jsonify({"cancelled": True})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    # ── Windows / Linux: tkinter (safe on those platforms from any thread) ──
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        chosen = filedialog.askdirectory(
            title="Select Playwright repo root",
            initialdir=start_path,
        )
        root.destroy()
        if not chosen:
            return jsonify({"cancelled": True})
        return jsonify({"path": chosen})
    except Exception as exc:
        return jsonify({"error": f"Could not open folder picker: {exc}"}), 500


# ── Hostname setup ─────────────────────────────────────────────────────────────

HOSTNAME = "playwright-executor"
PORT     = 7777
APP_URL  = f"http://{HOSTNAME}:{PORT}"


def _ensure_hosts_entry() -> bool:
    """Add '127.0.0.1 playwright-executor' to the system hosts file if missing.
    Returns True if the entry is present after the call."""
    import platform
    hosts_path = (r"C:\Windows\System32\drivers\etc\hosts"
                  if platform.system() == "Windows"
                  else "/etc/hosts")

    try:
        with open(hosts_path, "r", encoding="utf-8") as f:
            content = f.read()
        if HOSTNAME in content:
            return True
    except OSError:
        return False

    entry = f"\n127.0.0.1  {HOSTNAME}\n"

    if platform.system() == "Windows":
        # On Windows the script is typically run as admin via the .bat launcher
        try:
            with open(hosts_path, "a", encoding="utf-8") as f:
                f.write(entry)
            return True
        except PermissionError:
            return False
    else:
        # macOS / Linux — try sudo tee
        result = os.system(
            f'echo "127.0.0.1  {HOSTNAME}" | sudo tee -a {hosts_path} > /dev/null'
        )
        return result == 0


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import webbrowser

    ok = _ensure_hosts_entry()
    url = APP_URL if ok else f"http://localhost:{PORT}"

    print(f"\n  Playwright Test Executor")
    print(f"  Open: {url}\n")
    threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
