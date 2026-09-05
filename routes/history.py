"""
Run-history persistence and allure result parsing.
"""
from __future__ import annotations

import datetime
import json
import time
from pathlib import Path

from ui_launcher.config_reader import ConfigReader

# Fallback history file when no repo is known (tool root).
_TOOL_HISTORY_FILE = Path(__file__).parent.parent / "report_history.json"


def _history_file(repo: str = "") -> Path:
    """Return the history file path: {repo}/report_history.json or tool fallback."""
    if repo:
        return Path(repo) / "report_history.json"
    return _TOOL_HISTORY_FILE


def load_history(repo: str = "") -> list:
    hf = _history_file(repo)
    if hf.exists():
        try:
            return json.loads(hf.read_text(encoding="utf-8"))
        except Exception:
            pass
    # Migrate: if no repo-level file exists yet, try the old tool-level file
    if repo and _TOOL_HISTORY_FILE.exists():
        try:
            return json.loads(_TOOL_HISTORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def save_history(records: list, repo: str = "") -> None:
    _history_file(repo).write_text(json.dumps(records, indent=2), encoding="utf-8")


def parse_allure_results(results_dir: str) -> dict:
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


def parse_allure_results_full(results_dir: str) -> list:
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
            full_name = data.get("fullName", "")
            if "#" in full_name:
                method = full_name.split("#")[-1]
            elif "::" in full_name:
                method = full_name.split("::")[-1]
            else:
                method = data.get("name", f.stem)
            file_id = (labels.get("subSuite") or labels.get("suite") or
                       labels.get("parentSuite") or "")
            stem = f.stem  # e.g. "abc123-result"
            uid  = stem[:-7] if stem.endswith("-result") else stem
            tests.append({
                "name":      data.get("name", f.stem),
                "fullName":  full_name,
                "method":    method,
                "file":      file_id,
                "status":    status,
                "start":     start,
                "stop":      stop,
                "duration":  max(0, stop - start),
                "suite":     labels.get("feature") or labels.get("suite") or labels.get("parentSuite") or "",
                "historyId": data.get("historyId", ""),
                "uid":       uid,
                "source":    "allure",
            })
        except Exception:
            pass
    return sorted(tests, key=lambda x: x["start"], reverse=True)


def parse_smart_reporter(report_dir: str) -> dict:
    base = Path(report_dir)
    results, summaries = [], []

    data_file = base / ".smart-reporter-data.json"
    if data_file.exists():
        try:
            raw = json.loads(data_file.read_text(encoding="utf-8"))
            start_ts = raw.get("startTime", 0)
            for r in raw.get("results", []):
                tid = r.get("testId", "")
                parts = [p for p in tid.split("::") if p]
                suite  = parts[-2] if len(parts) >= 2 else (parts[0] if parts else "")
                name   = parts[-1] if parts else tid
                status = (r.get("status") or r.get("outcome", "unknown")).lower()
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

    hist_file = base / "smart-reporter-history.json"
    if hist_file.exists():
        try:
            raw = json.loads(hist_file.read_text(encoding="utf-8"))
            summaries = raw.get("summaries", [])
        except Exception:
            pass

    return {"results": results, "summaries": summaries}


def parse_allure_history_trend(repo: str, cfg: dict) -> list:
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


def _find_report_path(repo: str, cfg: dict) -> str:
    """Return the absolute path of the most recent HTML report, or empty string."""
    if not repo:
        return ""
    try:
        from ui_launcher.report_resolver import ReportResolver
        resolver = ReportResolver(repo, cfg.get("report_paths", []))
        path = resolver.find_latest_in_dir(cfg.get("report_individual_dir", "allure/reports"))
        return path or ""
    except Exception:
        return ""


def _report_paths(repo: str, abs_path: str) -> tuple[str, str]:
    """
    Return (absolute_path, relative_path) both using forward slashes.
    relative_path is relative to repo root; empty string if it can't be computed.
    """
    abs_fwd = abs_path.replace("\\", "/")
    rel_fwd = ""
    if repo and abs_path:
        try:
            rel = Path(abs_path).relative_to(Path(repo))
            rel_fwd = str(rel).replace("\\", "/")
        except ValueError:
            pass
    return abs_fwd, rel_fwd


def record_run_history(repo: str, status: str, cfg: dict) -> None:
    results_rel = cfg.get("allure_results_dir", "allure/results")
    results_dir = str(Path(repo) / results_rel) if repo else ""
    stats = parse_allure_results(results_dir) if results_dir else {
        "passed": 0, "failed": 0, "broken": 0, "skipped": 0, "total": 0
    }

    run_abs, run_rel = _report_paths(repo, _find_report_path(repo, cfg))
    tests_detail: list[dict] = []
    if results_dir:
        for t in parse_allure_results_full(results_dir):
            start_ms = t.get("start") or 0
            stop_ms  = t.get("stop")  or 0
            dur_ms   = max(0, stop_ms - start_ms)
            full_name = t.get("fullName", "")
            method_name = full_name.split("::")[-1] if "::" in full_name else t["name"]
            tests_detail.append({
                "suite":        t.get("suite", ""),
                "name":         t["name"],
                "method":       method_name,
                "fullName":     full_name,
                "status":       t["status"],
                "date":         datetime.datetime.fromtimestamp(start_ms / 1000).strftime("%Y-%m-%d") if start_ms else "",
                "time":         datetime.datetime.fromtimestamp(start_ms / 1000).strftime("%H:%M:%S") if start_ms else "",
                "duration_ms":  dur_ms,
                "duration_s":   round(dur_ms / 1000, 2),
                "report_path":  run_abs,   # absolute path (forward slashes)
                "report_relpath": run_rel, # relative to repo root (fallback)
            })

    record = {
        "ts":              int(time.time() * 1000),
        "date":            datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "repo":            Path(repo).name if repo else "unknown",
        "repo_root":       repo,
        "status":          status,
        "stats":           stats,
        "tests":           tests_detail,
        "report_path":     run_abs,
        "report_relpath":  run_rel,
    }
    records = load_history(repo)
    records.insert(0, record)
    save_history(records[:200], repo)
