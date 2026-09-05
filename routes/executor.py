"""
Blueprint: Executor routes
  /api/run  /api/stop  /api/status  /api/stream
  /api/repos  /api/tests  /api/tests/names
  /api/features/run  /api/browse-folder  /readme
"""
from __future__ import annotations

import os
import queue
import shutil
import subprocess
import sys
from pathlib import Path

from flask import Blueprint, Response, jsonify, request

from routes import state
from routes.history import record_run_history
from ui_launcher.command_builder import CommandBuilder
from ui_launcher.config_reader import ConfigReader
from ui_launcher.runner import TestRunner
from ui_launcher.test_discovery import TestDiscovery

bp = Blueprint("executor", __name__)

_ROOT = Path(__file__).parent.parent   # Playwright-Executer root


def _generate_allure3_report(repo: str, cfg: dict) -> str:
    """
    Generate an Allure 3 single-file HTML report after a test run.
    Returns the absolute path of the generated index.html, or '' on failure.
    """
    allure_bin = shutil.which("allure")
    if not allure_bin:
        return ""

    results_rel = cfg.get("allure_results_dir", "allure/results")
    report_rel  = cfg.get("report_individual_dir", "allure/reports")
    results_dir = Path(repo) / results_rel
    report_dir  = Path(repo) / report_rel

    if not results_dir.is_dir():
        return ""

    report_dir.mkdir(parents=True, exist_ok=True)
    report_name = f"{Path(repo).name} — Test Report"

    try:
        subprocess.run(
            [
                allure_bin, "awesome",
                str(results_dir),
                "--output", str(report_dir),
                "--single-file",
                "--report-name", report_name,
            ],
            cwd=repo,
            timeout=120,
            capture_output=True,
        )
        # Allure 3 --single-file writes index.html
        idx = report_dir / "index.html"
        return str(idx) if idx.exists() else ""
    except Exception:
        return ""


def _display_cmd(cmd: list[str], repo: str) -> str:
    repo_path = str(Path(repo).resolve())
    parts = []
    for token in cmd:
        t = str(token)
        if t.endswith(("python", "python3", "python.exe", "python3.exe")):
            parts.append("python")
            continue
        resolved = str(Path(t).resolve()) if (Path(t).exists() or t.startswith("/") or ":\\" in t) else t
        if resolved.startswith(repo_path + os.sep):
            parts.append(resolved[len(repo_path) + 1:].replace("\\", "/"))
        elif resolved.startswith(repo_path + "/"):
            parts.append(resolved[len(repo_path) + 1:])
        else:
            parts.append(t)
    return " ".join(f'"{p}"' if " " in p else p for p in parts)


def _looks_like_playwright_repo(path: Path) -> bool:
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


# ── README ─────────────────────────────────────────────────────────────────────

@bp.route("/readme")
def readme():
    readme_path = _ROOT / "README.md"
    try:
        md = readme_path.read_text(encoding="utf-8")
    except OSError:
        return "<p>README.md not found.</p>", 404
    source_label = "Playwright-Executer"
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
        if "|" in line and line.strip().startswith("|"):
            if not in_table:
                out.append('<table>'); in_table = True
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if all(re.match(r"^[-:]+$", c) for c in cells):
                continue
            tag = "th" if not any(r.strip().startswith("<th") for r in out[-3:]) else "td"
            out.append("<tr>" + "".join(f"<{tag}>{html_lib.escape(c)}</{tag}>" for c in cells) + "</tr>")
            continue
        elif in_table:
            out.append("</table>"); in_table = False
        line = html_lib.escape(line)
        m = re.match(r"^(#{1,4})\s+(.*)", line)
        if m:
            lvl = len(m.group(1))
            out.append(f"<h{lvl}>{m.group(2)}</h{lvl}>"); continue
        if re.match(r"^---+$", line.strip()):
            out.append("<hr>"); continue
        line = re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", line)
        line = re.sub(r"`(.*?)`", r"<code>\1</code>", line)
        line = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', line)
        if re.match(r"^[-*]\s", line):
            out.append(f"<li>{line[2:]}</li>"); continue
        if re.match(r"^\d+\.\s", line):
            _li_text = re.sub(r"^\d+\.\s", "", line)
            out.append(f"<li>{_li_text}</li>"); continue
        out.append(f"<p>{line}</p>" if line.strip() else "")
    if in_table: out.append("</table>")
    body = "\n".join(out)
    page_title = f"{source_label} — README"
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
  a{{color:#89b4fa}} li{{margin:3px 0}} p:empty{{display:none}}
</style></head><body>{body}</body></html>"""


# ── Repos ──────────────────────────────────────────────────────────────────────

@bp.route("/api/repos")
def get_repos():
    home = Path.home()
    scan_dirs = [
        home / "Documents" / "GitHub",
        home / "GitHub",
        home / "Projects",
        home / "Desktop",
        home / "Documents",
        _ROOT.parent,
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
                    repos.append({"name": item.name, "path": str(item.resolve())})
        except PermissionError:
            pass
    return jsonify({"repos": repos})


# ── Test discovery ─────────────────────────────────────────────────────────────

@bp.route("/api/tests")
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


@bp.route("/api/tests/names")
def get_test_names():
    import ast
    repo = request.args.get("repo", "").strip()
    file_rel = request.args.get("file", "").strip()
    if not repo or not file_rel:
        return jsonify({"names": []})
    repo_path = Path(repo)
    candidate = repo_path / file_rel
    if not candidate.exists():
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


# ── Run / Stop / Status ────────────────────────────────────────────────────────

@bp.route("/api/run", methods=["POST"])
def run_tests():
    with state._run_lock:
        if state._is_running:
            return jsonify({"error": "Already running"}), 409

        body = request.json or {}
        repo = body.get("repo", "").strip()
        if not repo or not os.path.isdir(repo):
            return jsonify({"error": "Repo path not found"}), 400

        try:
            cfg = ConfigReader().load()
            disc = TestDiscovery(repo)
            test_tree = disc.discover()

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

        state.broadcast("cmd", _display_cmd(cmd, repo))
        state._is_running = True

        def on_output(line):
            state.broadcast("line", line)

        def on_finish(exit_code, cancelled):
            state._is_running = False
            if cancelled:
                run_status = "cancelled"
            elif exit_code == 0:
                run_status = "passed"
            else:
                run_status = f"failed:{exit_code}"
            state.broadcast("status", run_status)
            # Generate Allure 3 single-file HTML report
            report_path = _generate_allure3_report(repo, cfg)
            if report_path:
                state.broadcast("line", f"[Report] Allure 3 report → {report_path}")
            record_run_history(repo, run_status, cfg)
            state.broadcast("done", "")

        state._runner = TestRunner(
            cmd=cmd, cwd=repo, env_overrides={},
            on_output=on_output, on_finish=on_finish,
        )
        state._runner.start()

    return jsonify({"ok": True, "cmd": _display_cmd(cmd, repo)})


@bp.route("/api/stop", methods=["POST"])
def stop_tests():
    if state._runner and state._is_running:
        state._runner.stop()
        return jsonify({"ok": True})
    return jsonify({"error": "Not running"}), 400


@bp.route("/api/status")
def get_status():
    return jsonify({"running": state._is_running})


# ── SSE stream ─────────────────────────────────────────────────────────────────

@bp.route("/api/stream")
def stream():
    q: queue.Queue = queue.Queue(maxsize=500)
    state._output_queues.append(q)

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
            if q in state._output_queues:
                state._output_queues.remove(q)

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── Features run ───────────────────────────────────────────────────────────────

@bp.route("/api/features/run", methods=["POST"])
def run_feature():
    with state._run_lock:
        if state._is_running:
            return jsonify({"error": "Already running"}), 409

        body = request.json or {}
        feat = body.get("feature", {})
        runtime = feat.get("runtime", "python")
        script  = feat.get("script", "").strip()
        cwd     = feat.get("cwd", "").strip()
        args    = feat.get("args", "").strip()

        if not script:
            return jsonify({"error": "No script specified"}), 400

        if not cwd:
            cwd = str(Path(script).parent) if Path(script).exists() else str(_ROOT)
        if not os.path.isdir(cwd):
            cwd = str(_ROOT)

        if runtime == "python":
            cmd = [sys.executable, script]
        elif runtime == "node":
            cmd = ["node", script]
        else:
            cmd = [script]

        if args:
            import shlex
            cmd.extend(shlex.split(args))

        display = " ".join(cmd)
        state.broadcast("cmd", display)
        state._is_running = True

        def on_output(line):
            state.broadcast("line", line)

        def on_finish(exit_code, cancelled):
            state._is_running = False
            if cancelled:
                state.broadcast("status", "cancelled")
            elif exit_code == 0:
                state.broadcast("status", "passed")
            else:
                state.broadcast("status", f"failed:{exit_code}")
            state.broadcast("done", "")

        state._runner = TestRunner(
            cmd=cmd, cwd=cwd, env_overrides={},
            on_output=on_output, on_finish=on_finish,
        )
        state._runner.start()

    return jsonify({"ok": True, "cmd": display})


# ── Browse folder ──────────────────────────────────────────────────────────────

@bp.route("/api/browse-folder", methods=["POST"])
def browse_folder():
    import platform, subprocess
    body = request.json or {}
    start_path = body.get("start", "").strip() or str(Path.home())
    if not Path(start_path).is_dir():
        start_path = str(Path.home())
    system = platform.system()

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
            return jsonify({"cancelled": True})
        except subprocess.TimeoutExpired:
            return jsonify({"cancelled": True})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        chosen = filedialog.askdirectory(title="Select Playwright repo root", initialdir=start_path)
        root.destroy()
        if not chosen:
            return jsonify({"cancelled": True})
        return jsonify({"path": chosen})
    except Exception as exc:
        return jsonify({"error": f"Could not open folder picker: {exc}"}), 500
