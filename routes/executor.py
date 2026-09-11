"""
Blueprint: Executor routes
  /api/run  /api/stop  /api/status  /api/stream
  /api/repos  /api/tests  /api/tests/names
  /api/features/run  /api/browse-folder  /readme
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from flask import Blueprint, Response, jsonify, request

from routes import state
from routes.history import record_run_history
from ui_launcher.command_builder import CommandBuilder, resolve_python, venv_env_overrides
from ui_launcher.config_reader import ConfigReader
from ui_launcher.runner import TestRunner
from ui_launcher.test_discovery import TestDiscovery

bp = Blueprint("executor", __name__)

_ROOT = Path(__file__).parent.parent   # Amplify-QEA root


def _read_pip_ini_flags(python_path: str) -> list[str]:
    """
    Read pip.ini from the venv and return explicit CLI flags for settings like
    index-url, extra-index-url, trusted-host, and proxy.

    pip's configparser rejects ini files whose first section header is not bare
    (e.g. "npx [global]" fails because of the "npx " prefix).  We strip any
    leading non-bracket text from section-header lines before parsing so the
    JFrog / corporate proxy settings are still honoured.
    """
    import configparser
    venv_root = Path(python_path).parent.parent
    candidates = [
        venv_root / "pip.ini",           # Windows venv location
        venv_root / "pip.conf",          # Linux/macOS venv location
    ]
    for ini_path in candidates:
        if not ini_path.exists():
            continue
        try:
            raw = ini_path.read_text(encoding="utf-8", errors="replace")
            # Strip anything before the first '[' on section-header lines
            fixed_lines = []
            for line in raw.splitlines():
                stripped = line.lstrip()
                bracket_pos = line.find("[")
                if bracket_pos > 0 and stripped and not stripped.startswith("#"):
                    line = line[bracket_pos:]   # remove prefix like "npx "
                fixed_lines.append(line)
            fixed = "\n".join(fixed_lines)
            parser = configparser.ConfigParser()
            parser.read_string(fixed)
            section = "global" if parser.has_section("global") else (
                parser.sections()[0] if parser.sections() else None
            )
            if not section:
                continue
            flags: list[str] = []
            for key in ("index-url", "extra-index-url", "trusted-host", "proxy"):
                val = parser.get(section, key, fallback=None)
                if val:
                    flags.extend([f"--{key}", val.strip()])
            return flags
        except Exception:
            pass
    return []


# ── Allure binary discovery ────────────────────────────────────────────────────

_NPX_SENTINEL = "npx"   # returned when Allure 3 should be invoked via npx


def _find_allure_bin(configured: str, want_v2: bool) -> str:
    """
    Return the path to the Allure binary for the requested format.

    Special value for Allure 3:  set allure3_bin = "npx"
      When the sentinel "npx" is returned, callers must build the command as
      ["npx", "allure", <subcommand>, ...] instead of [bin, <subcommand>, ...].
      This covers installations done via  npm install -g allure-commandline  or
      any setup where the allure executable is only reachable through npx.

    Resolution order (no version check — trust what is configured):
      1. Explicit path from config (allure2_bin / allure3_bin):
           • "npx" keyword → verify npx is on PATH, return sentinel "npx"
           • File path     → used as-is if it exists
      2. PATH lookup via shutil.which (finds .cmd/.bat/.exe on Windows).
      3. Well-known install locations per platform.
    """
    import platform

    if configured:
        # Support "npx" (or "npx allure") as a keyword for Allure 3 npm installs
        if configured.strip().lower().startswith("npx"):
            if shutil.which("npx"):
                return _NPX_SENTINEL
            return ""
        p = Path(configured)
        if p.exists():
            return str(p)

    # PATH lookup — most reliable when no path is configured.
    found = shutil.which("allure")
    if found and Path(found).exists():
        return found

    # Platform-specific well-known fallback locations
    if platform.system() == "Windows":
        candidates = [
            Path.home() / "scoop" / "apps" / "allure" / "current" / "bin" / "allure.bat",
            Path("C:/ProgramData/chocolatey/bin/allure.cmd"),
            Path("C:/ProgramData/chocolatey/bin/allure.exe"),
            # npm global install on Windows → %APPDATA%\npm\allure.cmd
            Path.home() / "AppData" / "Roaming" / "npm" / "allure.cmd",
        ]
    else:
        candidates = [
            Path("/opt/homebrew/bin/allure"),
            Path("/usr/local/bin/allure"),
            Path("/usr/bin/allure"),
        ]

    for c in candidates:
        if c.exists():
            return str(c)

    return ""


def _allure3_base(bin3: str, subcommand: str) -> list[str]:
    """
    Build the opening tokens of an Allure 3 command.

    When bin3 == _NPX_SENTINEL ("npx"), Allure 3 is accessed via npx
    (e.g. npm install -g allure-commandline):
      npx allure awesome ...
    Otherwise use the binary path directly:
      /path/to/allure awesome ...
    """
    if bin3 == _NPX_SENTINEL:
        return ["npx", "allure", subcommand]
    return [bin3, subcommand]


# ── Per-test file collection ───────────────────────────────────────────────────

def _files_for_uid(uid: str, results_dir: Path) -> list[Path]:
    """Return all result, attachment, and container files needed for one test UID."""
    result_file = results_dir / f"{uid}-result.json"
    if not result_file.exists():
        return []

    files: list[Path] = [result_file]
    seen: set[str] = {result_file.name}

    def _add(p: Path) -> None:
        if p.exists() and p.name not in seen:
            files.append(p)
            seen.add(p.name)

    def _collect_attachments(node: dict) -> None:
        for att in node.get("attachments", []):
            src = att.get("source", "")
            if src:
                _add(results_dir / src)
        for step in node.get("steps", []):
            _collect_attachments(step)

    try:
        data = json.loads(result_file.read_text(encoding="utf-8"))
        _collect_attachments(data)
    except Exception:
        pass

    for cf in results_dir.glob("*-container.json"):
        try:
            cdata = json.loads(cf.read_text(encoding="utf-8"))
            if uid in cdata.get("children", []):
                _add(cf)
                for stage in cdata.get("befores", []) + cdata.get("afters", []):
                    _collect_attachments(stage)
        except Exception:
            pass

    return files


# ── Single-file generators ─────────────────────────────────────────────────────

def _allure2_single_file(
    bin2: str, files: list[Path], dest: Path,
    history_dir: "Path | None" = None,
) -> bool:
    """
    Generate an Allure 2 --single-file report from an explicit file list.
    When history_dir is provided, the shared Allure 2 history JSON files are
    injected so the History tab shows this test's performance over previous runs.
    Individual reports never write history back — only consolidated does.
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_in = Path(tmp) / "in"
        tmp_in.mkdir()
        for f in files:
            shutil.copy2(str(f), str(tmp_in / f.name))
        # Inject Allure 2 history JSON files — only .json, not allure3-history.jsonl
        if history_dir and history_dir.is_dir():
            hist_target = tmp_in / "history"
            hist_target.mkdir(exist_ok=True)
            for hf in history_dir.iterdir():
                if hf.is_file() and hf.suffix == ".json":
                    shutil.copy2(str(hf), str(hist_target / hf.name))
        tmp_out = Path(tmp) / "out"
        try:
            # cwd=tmp keeps any Allure 2 side-effect directories (e.g.
            # allure-history-storage) inside the temp dir so they are
            # automatically cleaned up and never appear in the repo.
            subprocess.run(
                [bin2, "generate", str(tmp_in), "--single-file", "--clean",
                 "-o", str(tmp_out)],
                capture_output=True, timeout=90,
                cwd=tmp,
            )
        except Exception:
            return False
        candidate = tmp_out / "complete.html"
        if not candidate.exists():
            htmls = list(tmp_out.glob("*.html"))
            candidate = htmls[0] if htmls else None
        if candidate and candidate.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(candidate), str(dest))
            return True
    return False


def _allure3_single_file(
    bin3: str, files: list[Path], dest: Path, name: str,
    history_jsonl: "Path | None" = None,
) -> bool:
    """
    Generate an Allure 3 awesome --single-file report from an explicit file list.
    When history_jsonl is provided, the shared JSONL is passed via --history-path
    so the History tab shows this test's trend across previous runs.
    Individual reports never write history back — only consolidated does.
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_in = Path(tmp) / "in"
        tmp_in.mkdir()
        for f in files:
            shutil.copy2(str(f), str(tmp_in / f.name))
        tmp_out = Path(tmp) / "out"
        cmd = _allure3_base(bin3, "awesome") + [str(tmp_in),
               "--output", str(tmp_out),
               "--single-file",
               "--report-name", name]
        # Use a temp COPY of the shared JSONL so the History tab is populated
        # in the individual report but the real JSONL is never modified —
        # Allure 3 appends to --history-path on every generate, so passing the
        # real file here would corrupt it with single-test entries.
        if history_jsonl and history_jsonl.exists():
            tmp_jsonl = Path(tmp) / "history-readonly.jsonl"
            shutil.copy2(str(history_jsonl), str(tmp_jsonl))
            cmd += ["--history-path", str(tmp_jsonl)]
        try:
            subprocess.run(cmd, capture_output=True, timeout=90)
        except Exception:
            return False
        candidate = tmp_out / "index.html"
        if candidate.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(candidate), str(dest))
            return True
    return False


# ── Consolidated report generators (with history) ─────────────────────────────

def _allure2_consolidated(
    bin2: str, results_dir: Path, out_dir: Path,
    history_dir: Path, timestamp: str,
) -> str:
    """
    Generate an Allure 2 consolidated report for the full run.

    Output: {out_dir}/{timestamp}/index.html  (multi-file SPA, served via /allure/)

    Why NOT --single-file: Allure 2 --single-file only outputs complete.html and
    never writes a history/ folder, so history cannot be captured from that pass.
    The full report correctly writes {out}/history/ which we merge back into the
    shared history_dir for trend accumulation.

    History flow:
      1. Inject {history_dir}/*.json → temp_results/history/   (previous runs)
      2. allure generate -o {timestamp_dir}                     (full report)
      3. Merge {timestamp_dir}/history/*.json → {history_dir}/  (update history)
    """
    # Each run gets its own timestamped subdirectory so reports accumulate.
    run_dir = out_dir / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)
    history_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_results = Path(tmp) / "results"
        if results_dir.is_dir():
            shutil.copytree(str(results_dir), str(tmp_results))
        else:
            tmp_results.mkdir()

        # Step 1 — inject Allure 2 history JSON files into the temp results.
        # Priority: use history_dir (our persistent store). If it is empty on
        # the first run ever, fall back to allure/results/history/ which the
        # project conftest restores from allure/reports/html/history/.
        tmp_hist = tmp_results / "history"
        tmp_hist.mkdir(exist_ok=True)

        def _copy_json_files(src: Path, dst: Path) -> int:
            count = 0
            if src.is_dir():
                for hf in src.iterdir():
                    if hf.is_file() and hf.suffix == ".json":
                        shutil.copy2(str(hf), str(dst / hf.name))
                        count += 1
            return count

        copied = _copy_json_files(history_dir, tmp_hist)
        if copied == 0:
            # Bootstrap from the standard Allure 2 results/history/ location
            # (populated by the project conftest from its own dashboard history).
            _copy_json_files(results_dir / "history", tmp_hist)

        # Step 2 — full report generate; cwd=tmp contains Allure 2 side-effect
        # dirs (allure-history-storage etc.) so they never appear in the repo.
        r = None
        try:
            r = subprocess.run(
                [bin2, "generate", str(tmp_results), "--clean", "-o", str(run_dir)],
                capture_output=True, timeout=180,
                cwd=tmp,
            )
        except Exception as exc:
            return f"ERROR:{exc}"  # caller broadcasts as warning

        idx = run_dir / "index.html"
        if not idx.exists():
            stderr = (r.stderr or b"").decode("utf-8", errors="replace")[:300] if r else ""
            return f"ERROR:{stderr or 'index.html not found after allure generate'}"

        # Step 3 — merge updated history back into shared history_dir
        new_hist = run_dir / "history"
        _copy_json_files(new_hist, history_dir)

        # Step 4 — also generate a standalone single-file alongside the full report.
        # The single-file (complete.html) is self-contained and shareable via file://
        # while the full SPA above is served via the Flask /allure/ server with history.
        standalone_dest = out_dir / f"consolidated-{timestamp}.html"
        tmp_single = Path(tmp) / "single"
        try:
            subprocess.run(
                [bin2, "generate", str(tmp_results), "--single-file", "--clean",
                 "-o", str(tmp_single)],
                capture_output=True, timeout=180,
                cwd=tmp,
            )
            candidate = tmp_single / "complete.html"
            if not candidate.exists():
                htmls = sorted(tmp_single.glob("*.html"),
                               key=lambda f: f.stat().st_size, reverse=True)
                candidate = htmls[0] if htmls else None
            if candidate and candidate.exists():
                shutil.copy2(str(candidate), str(standalone_dest))
        except Exception:
            pass  # standalone is best-effort; full report is the authoritative one

    return str(idx) if idx.exists() else ""


def _allure3_consolidated(
    bin3: str, results_dir: Path, out_dir: Path,
    history_jsonl: Path, name: str, timestamp: str,
) -> str:
    """
    Generate an Allure 3 consolidated report — full SPA + standalone single-file.

    Why two passes:
      --single-file reads --history-path to embed history in the HTML but does
      NOT write back to the JSONL.  Only the full (non-single-file) generate
      updates the JSONL.  So we run the full generate first to update the JSONL,
      then the --single-file generate to produce a shareable standalone HTML.

    History flow:
      Pass 1: allure awesome {results} --output {run_dir} --history-path {jsonl}
                → JSONL updated with current run  ← this is what was missing
      Pass 2: allure awesome {results} --output {tmp} --single-file --history-path {jsonl}
                → reads the now-updated JSONL, embeds full history in HTML

    Output:
      {out_dir}/{timestamp}/index.html          ← full SPA, served via /allure/
      {out_dir}/consolidated-{timestamp}.html  ← standalone, shareable via file://
    """
    run_dir = out_dir / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)
    history_jsonl.parent.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"consolidated-{timestamp}.html"

    with tempfile.TemporaryDirectory() as tmp:
        # Pass 1 — full report; this is the step that writes to the JSONL
        tmp_full = Path(tmp) / "full"
        base_cmd = _allure3_base(bin3, "awesome") + [str(results_dir),
                   "--name", name,
                   "--history-path", str(history_jsonl)]
        try:
            subprocess.run(
                base_cmd + ["--output", str(tmp_full)],
                capture_output=True, timeout=180,
            )
        except Exception:
            return ""

        idx = tmp_full / "index.html"
        if not idx.exists():
            return ""

        # Copy full report to timestamped run_dir for Flask /allure/ serving
        if run_dir.exists():
            shutil.rmtree(str(run_dir))
        shutil.copytree(str(tmp_full), str(run_dir))

        # Pass 2 — single-file using the now-updated JSONL (best-effort)
        tmp_single = Path(tmp) / "single"
        try:
            subprocess.run(
                base_cmd + ["--output", str(tmp_single), "--single-file"],
                capture_output=True, timeout=180,
            )
            candidate = tmp_single / "index.html"
            if candidate.exists():
                shutil.copy2(str(candidate), str(dest))
        except Exception:
            pass

    idx_dest = run_dir / "index.html"
    return str(idx_dest) if idx_dest.exists() else ""


# ── Pre-run cleanup ───────────────────────────────────────────────────────────

def _clean_allure_results(repo: str, cfg: dict) -> None:
    """
    Remove stale Allure result files from the results directory before each run
    so the generated report contains only the current run's tests.

    The history/ subfolder is intentionally preserved — it stores Allure 2
    trend JSON files that carry historical data into the next report.

    Repos that already clean results via pytest_configure (conftest.py) are
    unaffected: this becomes a no-op when the dir is already empty.
    """
    results_rel = cfg.get("allure_results_dir", "allure/results")
    results_dir = Path(repo) / results_rel
    if not results_dir.is_dir():
        return
    for item in results_dir.iterdir():
        if item.name == "history":
            continue  # keep Allure 2 history trend files
        try:
            if item.is_file():
                item.unlink()
            elif item.is_dir():
                shutil.rmtree(str(item))
        except Exception:
            pass


# ── Main orchestrator ──────────────────────────────────────────────────────────

def _generate_reports(repo: str, cfg: dict) -> dict:
    """
    Generate Allure reports after a test run.

    Rules:
      - 1 test  → one consolidated report (no per-test split needed)
      - >1 tests → consolidated report + individual per-test single-file reports
                   (generated in parallel, max 4 workers)

    Format is controlled by cfg["allure_format"]:
      "allure2" → Allure 2 only (with history)
      "allure3" → Allure 3 only (with history.jsonl)
      "both"    → both formats; run_report prefers allure3

    Returns:
      {
        "run_report": "/abs/path/to/consolidated/index.html",
        "pertest":    { "{uid}": "/abs/path/to/individual/{uid}-allure3.html", ... }
      }
    """
    import datetime as _dt
    results_dir  = Path(repo) / cfg.get("allure_results_dir",  "allure/results")
    fmt          = cfg.get("allure_format", "allure2")
    report_name  = f"{Path(repo).name} — Test Report"
    timestamp    = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    warnings: list[str] = []

    bin2 = (_find_allure_bin(cfg.get("allure2_bin", ""), want_v2=True)
            if fmt in ("allure2", "both") else "")
    bin3 = (_find_allure_bin(cfg.get("allure3_bin", ""), want_v2=False)
            if fmt in ("allure3", "both") else "")

    if fmt in ("allure2", "both") and not bin2:
        warnings.append(
            "Allure 2 binary not found — set allure2_bin in Config → Allure "
            "(e.g. /opt/homebrew/bin/allure  or  C:\\scoop\\apps\\allure\\current\\bin\\allure.bat)"
        )
    if fmt in ("allure3", "both") and not bin3:
        warnings.append(
            "Allure 3 binary not found — set allure3_bin in Config → Allure "
            "(e.g. allure  or  /usr/local/bin/allure3)"
        )

    if not bin2 and not bin3:
        return {"run_report": "", "pertest": {}, "warnings": warnings}

    uid_files: dict[str, Path] = {}
    if results_dir.is_dir():
        for f in results_dir.glob("*-result.json"):
            uid = f.stem[:-7] if f.stem.endswith("-result") else f.stem
            uid_files[uid] = f

    if not uid_files:
        return {"run_report": "", "pertest": {}}

    test_count       = len(uid_files)
    consolidated_dir = Path(repo) / cfg.get("report_consolidated_dir", "allure/reports/consolidated")
    pertest_dir      = Path(repo) / cfg.get("report_pertest_dir",       "allure/reports/individual")

    # Shared history directory — single source of truth for all report types.
    # Created upfront so generators can always reference it, even on the first run.
    history_dir   = Path(repo) / cfg.get("allure_history_dir", "allure/allure-history")
    history_dir.mkdir(parents=True, exist_ok=True)
    history_jsonl = history_dir / "allure3-history.jsonl"

    run_report = ""
    pertest: dict[str, str] = {}

    # ── Consolidated report ────────────────────────────────────────────────────
    # Consolidated runs first so it updates history_dir before individual reports
    # read from it. Individual reports inject the same history data so the
    # History tab in every report shows how that test performed in previous runs.

    if fmt in ("allure2", "both") and bin2:
        a2_dir = consolidated_dir if fmt == "allure2" else Path(str(consolidated_dir) + "-allure2")
        path = _allure2_consolidated(bin2, results_dir, a2_dir, history_dir, timestamp)
        if path and not path.startswith("ERROR:"):
            if not run_report:
                run_report = path
        else:
            detail = path[6:] if path.startswith("ERROR:") else "index.html not generated"
            warnings.append(f"Allure 2 consolidated failed — {detail}")

    if fmt in ("allure3", "both") and bin3:
        path = _allure3_consolidated(
            bin3, results_dir, consolidated_dir, history_jsonl, report_name, timestamp
        )
        if path:
            run_report = run_report or path
        else:
            warnings.append("Allure 3 consolidated report generation failed — check binary path and results dir")

    # ── Per-test individual single-file reports (only when >1 test) ────────────
    # history_dir is always passed — by now consolidated has updated it with the
    # current run's history, so individual reports include up-to-date trend data.
    if test_count > 1 and cfg.get("generate_pertest_reports", True):
        # Build uid → sanitised method name for meaningful filenames
        uid_methods: dict[str, str] = {}
        for uid, result_file in uid_files.items():
            try:
                data = json.loads(result_file.read_text(encoding="utf-8"))
                full = data.get("fullName", "")
                method = (full.split("#")[-1] if "#" in full
                          else full.split("::")[-1] if "::" in full
                          else data.get("name", uid))
                safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in method)[:80]
                uid_methods[uid] = safe or uid[:16]
            except Exception:
                uid_methods[uid] = uid[:16]

        def _gen_one(uid: str) -> tuple[str, str]:
            files = _files_for_uid(uid, results_dir)
            if not files:
                return uid, ""
            method     = uid_methods.get(uid, uid[:16])
            basename   = f"{method}-{timestamp}"
            short_name = f"{method} — {report_name}"
            best = ""
            if fmt in ("allure2", "both") and bin2:
                dest2 = pertest_dir / f"{basename}-allure2.html"
                if _allure2_single_file(bin2, files, dest2, history_dir=history_dir):
                    best = str(dest2)
            if fmt in ("allure3", "both") and bin3:
                dest3 = pertest_dir / f"{basename}-allure3.html"
                if _allure3_single_file(bin3, files, dest3, short_name,
                                        history_jsonl=history_jsonl):
                    best = best or str(dest3)
            return uid, best

        workers = min(4, test_count)
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            for uid, path in ex.map(_gen_one, uid_files.keys()):
                if path:
                    pertest[uid] = path

        if not pertest and (bin2 or bin3):
            warnings.append("No individual per-test reports generated — result files may be missing attachments")

    return {"run_report": run_report, "pertest": pertest, "warnings": warnings}


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
    source_label = "Amplify-QEA"
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

            builder = CommandBuilder(repo, venv_path=cfg.get("venv_path", ""))
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

        # Clean stale Allure result files before the run so each report only
        # contains results from the current run.  The history/ subfolder is
        # preserved — it carries Allure 2 trend JSON files for the next report.
        # Repos that already clean results via pytest_configure are unaffected
        # (cleaning an already-clean dir is a no-op).
        _clean_allure_results(repo, cfg)

        # Create history dir upfront (before subprocess) so it exists from
        # the very first run and Allure can reference it immediately.
        history_dir = Path(repo) / cfg.get("allure_history_dir", "allure/allure-history")
        history_dir.mkdir(parents=True, exist_ok=True)

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
            report_info = _generate_reports(repo, cfg)
            for w in report_info.get("warnings", []):
                state.broadcast("line", f"[Report] ⚠  {w}")
            if report_info.get("run_report"):
                state.broadcast("line", f"[Report] ✓  Consolidated → {report_info['run_report']}")
            n_pertest = len(report_info.get("pertest", {}))
            if n_pertest:
                state.broadcast("line", f"[Report] ✓  {n_pertest} individual test report(s) generated")
            record_run_history(repo, run_status, cfg, report_info=report_info)
            state.broadcast("done", "")

        python = cmd[0] if cmd else sys.executable
        state._runner = TestRunner(
            cmd=cmd, cwd=repo, env_overrides=venv_env_overrides(python),
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
    q: queue.Queue = queue.Queue(maxsize=600)

    # Snapshot the ring buffer BEFORE registering the queue so we don't
    # double-deliver events that arrive in the tiny window between the two.
    replay = list(state._recent_events) if state._is_running else []
    state._output_queues.append(q)

    def generate():
        try:
            yield "event: connected\ndata: \"ok\"\n\n"
            # Replay buffered run output to reconnecting clients.
            for buffered in replay:
                yield buffered
            while True:
                try:
                    msg = q.get(timeout=3)
                    yield msg
                except queue.Empty:
                    # Short heartbeat keeps the TCP connection alive and forces
                    # Windows' TCP stack to flush buffered SSE data immediately.
                    yield ": heartbeat\n\n"
        finally:
            if q in state._output_queues:
                state._output_queues.remove(q)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control":      "no-cache",
            "X-Accel-Buffering":  "no",        # disable nginx/proxy buffering
            "Connection":         "keep-alive", # keep socket open between events
            "Transfer-Encoding":  "chunked",    # each yield is sent as a chunk immediately
        },
    )


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

        cfg       = ConfigReader().load()
        repo_root = cfg.get("repo_root", "").strip() or str(_ROOT)
        py        = resolve_python(repo_root, cfg.get("venv_path", ""))

        # Resolve node-ecosystem binaries (npx, npm, node) cross-platform.
        # shutil.which handles .cmd / .exe on Windows via PATHEXT automatically.
        def _node_bin(name: str) -> str:
            found = shutil.which(name)
            return found or name  # fall back to bare name; let OS resolve it

        import shlex as _shlex

        script_path = Path(script)

        # Detect inline command: has spaces OR is not an existing file path.
        is_inline = " " in script or not script_path.exists()

        if runtime == "npx":
            bin_ = _node_bin("npx")
            if is_inline:
                parts = _shlex.split(script)
                # Don't double-prefix if user already typed "npx ..."
                cmd = parts if parts and parts[0].lower() in ("npx",) else [bin_] + parts
            else:
                cmd = [bin_, script]

        elif runtime == "npm":
            bin_ = _node_bin("npm")
            if is_inline:
                parts = _shlex.split(script)
                cmd = parts if parts and parts[0].lower() in ("npm",) else [bin_] + parts
            else:
                cmd = [bin_, "run", script]

        elif runtime == "node":
            bin_ = _node_bin("node")
            if is_inline:
                parts = _shlex.split(script)
                first = parts[0].lower() if parts else ""
                # Don't prepend "node" if user typed node/npm/npx themselves
                cmd = parts if first in ("node", "npm", "npx") else [bin_] + parts
            else:
                cmd = [bin_, script]

        elif runtime == "python":
            if is_inline:
                parts = _shlex.split(script)
                first = parts[0].lower() if parts else ""
                venv_bin = Path(py).parent  # e.g. venv/bin/

                if first in ("python", "python3"):
                    # Replace generic python/python3 with the resolved venv Python
                    cmd = [py] + parts[1:]

                elif first in ("pip", "pip3"):
                    # Resolve pip from the same venv bin directory as python
                    pip_bin = str(venv_bin / first)
                    if not Path(pip_bin).exists():
                        pip_bin = str(venv_bin / "pip")
                    if not Path(pip_bin).exists():
                        # Fallback: run as `python -m pip`
                        pip_bin = None
                    if pip_bin:
                        cmd = [pip_bin] + parts[1:]
                    else:
                        cmd = [py, "-m", "pip"] + parts[1:]

                else:
                    # Unknown first token (e.g. a script name) — prepend venv python
                    cmd = [py] + parts
            else:
                cmd = [py, script]

        elif runtime == "powershell":
            # PowerShell: works on Windows (powershell.exe / pwsh) and macOS/Linux (pwsh)
            ps = shutil.which("pwsh") or shutil.which("powershell") or "powershell"
            if is_inline:
                cmd = [ps, "-Command", script]
            else:
                cmd = [ps, "-File", script]

        elif runtime == "cmd":
            # Windows Command Prompt — cmd /C runs a command string or batch file
            cmd = ["cmd", "/C", script]

        else:
            # shell / bash
            sh = shutil.which("bash") or shutil.which("sh") or "sh"
            if is_inline:
                cmd = [sh, "-c", script]
            else:
                cmd = [sh, script]

        if args:
            cmd.extend(_shlex.split(args))

        if not cwd:
            cwd = repo_root if os.path.isdir(repo_root) else (
                str(script_path.parent) if script_path.exists() else str(_ROOT)
            )
        if not os.path.isdir(cwd):
            cwd = str(_ROOT)

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


# ── Venv status / install ─────────────────────────────────────────────────────

@bp.route("/api/venv/status")
def venv_status():
    repo = request.args.get("repo", "").strip()
    cfg  = ConfigReader().load()
    if not repo:
        repo = cfg.get("repo_root", "").strip()
    venv_path_cfg = cfg.get("venv_path", "").strip()

    python = resolve_python(repo, venv_path_cfg) if repo else sys.executable
    is_venv = python != sys.executable

    venv_root = ""
    if is_venv:
        venv_root = str(Path(python).parent.parent)

    req_files: list[str] = []
    if repo:
        for name in ["requirements.txt", "requirements/base.txt",
                     "requirements/dev.txt", "requirements-dev.txt"]:
            rp = Path(repo) / name
            if rp.exists():
                req_files.append(str(rp))

    return jsonify({
        "venv_found":       is_venv,
        "python":           python,
        "venv_root":        venv_root,
        "venv_path_config": venv_path_cfg,
        "requirements":     req_files,
        "repo":             repo,
    })


@bp.route("/api/venv/install", methods=["POST"])
def venv_install():
    with state._run_lock:
        if state._is_running:
            return jsonify({"error": "A run is already in progress"}), 409

        body = request.json or {}
        repo = body.get("repo", "").strip()
        req  = body.get("requirements_file", "").strip()
        cfg  = ConfigReader().load()
        if not repo:
            repo = cfg.get("repo_root", "").strip()

        python = resolve_python(repo, cfg.get("venv_path", "")) if repo else sys.executable

        if not req:
            for name in ["requirements.txt", "requirements/base.txt", "requirements/dev.txt"]:
                rp = Path(repo) / name if repo else Path(name)
                if rp.exists():
                    req = str(rp)
                    break

        if not req:
            return jsonify({"error": "No requirements.txt found in repo root"}), 400

        # Build pip command; extract settings from pip.ini manually so we can
        # pass them as explicit flags — this works even when the ini has a bad
        # section header like "npx [global]" instead of "[global]".
        import platform as _plat
        pip_flags = _read_pip_ini_flags(python)

        if _plat.system() == "Windows":
            pip_exe = str(Path(python).parent / "pip.exe")
            base = [pip_exe] if Path(pip_exe).exists() else [python, "-m", "pip"]
        else:
            base = [python, "-m", "pip"]

        cmd = base + ["install", "--isolated"] + pip_flags + ["-r", req]
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

        try:
            state._runner = TestRunner(
                cmd=cmd, cwd=repo or str(_ROOT), env_overrides=venv_env_overrides(python),
                on_output=on_output, on_finish=on_finish,
            )
            state._runner.start()
        except Exception as ex:
            state._is_running = False
            return jsonify({"error": str(ex)}), 500

    return jsonify({"ok": True, "cmd": display})


@bp.route("/api/config/venv-path", methods=["POST"])
def save_venv_path():
    body = request.json or {}
    venv = body.get("venv_path", "").strip()
    reader = ConfigReader()
    cfg = reader.load()
    cfg["venv_path"] = venv
    try:
        reader.save(cfg)
    except OSError as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True, "venv_path": venv})


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
