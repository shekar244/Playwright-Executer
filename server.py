"""
Playwright Test Executor — Web Server
Serves the browser UI and streams test output via Server-Sent Events.
"""

import json
import os
import queue
import sys
import threading
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
            out.append(f"<li>{re.sub(r'^\d+\.\s','',line)}</li>"); continue
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
                elif opt_type == "dropdown":
                    v = str(val).strip() if val is not None else ""
                    if v and v.lower() not in ("none", "(none)"):
                        extra_flags.extend([flag, v])

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
                _broadcast("status", "cancelled")
            elif exit_code == 0:
                _broadcast("status", "passed")
            else:
                _broadcast("status", f"failed:{exit_code}")
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
