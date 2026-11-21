"""Consolidated reporting: Grid runs and local Executor runs in one model, with filters and roll-ups.

Pure logic — no Flask, no network — so every rule here is unit-tested. The routes fetch the raw
data (the coordinator's runs, the user's run history) and hand it to `build_overview` /
`build_run_detail`.

A *run* is one request to run tests. It is made of *chunks*: for the Grid, one chunk is a slice of
tests queued for one browser and executed by one pod; a local Executor run is a single chunk with no
pod. Filters apply to chunks (so "firefox" counts only the firefox chunks of a run, and "pod X" only
what that pod executed); a run stays in the view only while some chunk of it does.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, Mapping
from zoneinfo import ZoneInfo

from amplify import payload_coerce as pc
from amplify.payload_coerce import iso_to_ms  # noqa: F401  (re-exported: grid_data and the tests use it from here)

GRID, LOCAL = "grid", "local"
SOURCES = ("all", GRID, LOCAL)
LOCAL_ENV = "local"
COUNT_KEYS = ("passed", "failed", "broken", "skipped")
GRID_LIST_LIMIT = 200      # the most runs the coordinator will list in one call (it has no offset)
MAX_GRID_RUNS = 100        # newest Grid runs examined per request (each needs one detail call)
MAX_RUN_ROWS = 200         # rows returned in the runs table
MAX_DAYS_FILLED = 120      # the trend fills empty days up to this span
_DAY_MS = 86_400_000


@dataclass
class Filters:
    source: str = "all"                 # all | grid | local
    environment: str = ""
    browser: str = ""
    pod: str = ""
    start_ms: int | None = None         # inclusive, UTC epoch ms
    end_ms: int | None = None           # exclusive
    tz_minutes: int = 0                 # JS getTimezoneOffset(): minutes UTC is ahead of the user's clock
    tz_name: str = ""                   # IANA zone like "America/New_York" (preferred: knows about DST)

    @property
    def zone(self):
        return _zone(self.tz_name, self.tz_minutes)

    def echo(self) -> dict:
        return {"source": self.source, "environment": self.environment, "browser": self.browser,
                "pod": self.pod, "start_ms": self.start_ms, "end_ms": self.end_ms}


# ── Reading the request ───────────────────────────────────────────────────────

def _text(args: Mapping, name: str) -> str:
    return str(args.get(name, "") or "").strip()[:80]


def _zone(name: str, tz_minutes: int):
    """The user's time zone. A named zone follows daylight-saving changes, so a date in July is read with
    July's offset even when today is in November; without one (or if this server lacks the zone database)
    the browser's current offset is used for every date."""
    if name:
        try:
            return ZoneInfo(name)
        except (KeyError, ValueError, OSError):
            pass
    return timezone(timedelta(minutes=-tz_minutes))


_ZONE_NAME = re.compile(r"^[A-Za-z0-9_+\-]+(/[A-Za-z0-9_+\-]+){0,2}$")


def _local_midnight_utc_ms(day: str, zone) -> int:
    local = datetime.strptime(day, "%Y-%m-%d")                      # ValueError on anything else
    return int(local.replace(tzinfo=zone).timestamp() * 1000)


def parse_filters(args: Mapping, now_ms: int) -> Filters:
    """Build Filters from query parameters. Raises ValueError with a message fit for a 400."""
    source = _text(args, "source") or "all"
    if source not in SOURCES:
        raise ValueError(f"source must be one of: {', '.join(SOURCES)}")
    try:
        tz = int(_text(args, "tz") or 0)
    except ValueError:
        raise ValueError("tz must be a number of minutes") from None
    if not -14 * 60 <= tz <= 14 * 60:
        raise ValueError("tz is out of range")

    tz_name = _text(args, "tzname")
    if tz_name and not _ZONE_NAME.match(tz_name):
        raise ValueError("tzname must be a time zone name like Europe/London")

    f = Filters(source=source, environment=_text(args, "env"), browser=_text(args, "browser").lower(),
                pod=_text(args, "pod"), tz_minutes=tz, tz_name=tz_name)

    days = _text(args, "days")
    if days:
        try:
            n = int(days)
        except ValueError:
            raise ValueError("days must be a whole number") from None
        if not 1 <= n <= 3650:
            raise ValueError("days must be between 1 and 3650")
        f.start_ms = now_ms - n * _DAY_MS
    start, end = _text(args, "from"), _text(args, "to")
    try:
        if start:
            f.start_ms = _local_midnight_utc_ms(start, f.zone)
        if end:                                                    # the whole of the "to" day (a DST day isn't 24 h)
            next_day = (datetime.strptime(end, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
            f.end_ms = _local_midnight_utc_ms(next_day, f.zone)
    except (ValueError, OverflowError, OSError):                   # OverflowError: year 1 / 9999 in a far-off zone
        raise ValueError("from and to must be dates like 2026-11-20") from None
    if f.start_ms is not None and f.end_ms is not None and f.start_ms >= f.end_ms:
        raise ValueError("The start of the time frame must be before its end")
    return f


# ── Raw data → runs ───────────────────────────────────────────────────────────

_num = pc.count            # test counts: whole numbers >= 0, anything else 0 (see payload_coerce)
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")     # the coordinator's ids; also the tail of a URL path


def normalize_grid_run(detail: Mapping) -> dict:
    """The coordinator's /api/runs/<id> payload → a run. Every value is read through payload_coerce: the
    per-test results were written by the pods' test code, so any field can be of any type."""
    detail = detail if isinstance(detail, Mapping) else {}
    chunks = []
    for c in pc.dicts(detail.get("chunk_details")):
        results = c.get("results")
        result = results[0] if isinstance(results, list) and results and isinstance(results[0], dict) else {}
        tests = [{"name": pc.text(t.get("nodeid")), "outcome": pc.text(t.get("outcome")) or "unknown",
                  "duration_s": pc.duration(t.get("duration")), "message": pc.message(t.get("message"))}
                 for t in pc.dicts(result.get("test_results"))]
        if not tests:
            # Nothing reported per test. Queued / running: still to come (None). Finished: the pod's pytest
            # died before reporting (collection error, crash) — an error, not "pending"; its logs say why.
            finished = c.get("status") == "completed"
            outcome = ("error" if _num(c.get("failed")) else "unknown") if finished else None
            tests = [{"name": n, "outcome": outcome, "duration_s": None, "message": None}
                     for n in pc.texts(c.get("tests"))]
        chunks.append({
            "chunk_id": pc.text(c.get("chunk_id")), "browser": pc.text(c.get("browser")).lower(),
            "pod": pc.text(c.get("assigned_runner")), "status": pc.text(c.get("status")),
            "passed": _num(c.get("passed")), "failed": _num(c.get("failed")), "broken": 0,
            "skipped": _num(c.get("skipped")), "duration_s": pc.duration(result.get("duration_seconds")),
            "updated_ms": pc.iso_to_ms(c.get("updated_at")), "tests": tests,
            "has_log": c.get("status") == "completed", "has_allure": c.get("has_allure") is True})
    # The run's Allure report is built from its chunks' raw results: it exists once any chunk uploaded some.
    return {"id": pc.text(detail.get("run_id")), "source": GRID, "started_ms": pc.iso_to_ms(detail.get("created_at")),
            "environment": pc.text(detail.get("environment")), "requested_by": pc.text(detail.get("requested_by")),
            "repo": pc.text(detail.get("repo")), "sha": pc.text(detail.get("sha")),
            "has_report": any(c["has_allure"] for c in chunks), "chunks": chunks}


def local_run_id(record: Mapping) -> str:
    return f"local-{pc.epoch_ms(record.get('ts') if isinstance(record, Mapping) else None)}"


def normalize_local_record(record: Mapping) -> dict:
    """One entry of the user's run history → a run (a single chunk, no pod). The history is a file on disk:
    like a coordinator payload, it is read without trusting its shape."""
    record = record if isinstance(record, Mapping) else {}
    ts = pc.epoch_ms(record.get("ts"))
    stats = record.get("stats") if isinstance(record.get("stats"), Mapping) else {}
    tests, total_s = [], 0.0
    for t in pc.dicts(record.get("tests")):
        duration = pc.duration(t.get("duration_s"))
        total_s += duration or 0.0
        name = pc.text(t.get("fullName")) or f"{pc.text(t.get('suite'))}::{pc.text(t.get('method')) or pc.text(t.get('name'))}"
        tests.append({"name": name, "outcome": (pc.text(t.get("status")) or "unknown").lower(), "duration_s": duration,
                      "message": None, "uid": pc.text(t.get("uid")), "has_report": bool(t.get("report_path"))})
    run_id = local_run_id(record)
    return {"id": run_id, "source": LOCAL, "started_ms": ts or None,
            "environment": pc.text(record.get("environment")) or LOCAL_ENV, "requested_by": "",
            "repo": pc.text(record.get("repo")), "run_status": pc.text(record.get("status")),
            "has_report": bool(record.get("report_path")),
            "chunks": [{"chunk_id": run_id, "browser": pc.text(record.get("browser")).lower(), "pod": "",
                        "status": "completed", **{k: _num(stats.get(k)) for k in COUNT_KEYS},
                        "duration_s": round(total_s, 2) if tests else None, "updated_ms": ts or None,
                        "tests": tests, "has_log": False}]}


# ── Filtering ─────────────────────────────────────────────────────────────────

def _in_time(run: Mapping, f: Filters) -> bool:
    started = run.get("started_ms")
    if f.start_ms is None and f.end_ms is None:
        return True
    if started is None:
        return False
    return (f.start_ms is None or started >= f.start_ms) and (f.end_ms is None or started < f.end_ms)


def filter_runs(runs: Iterable[dict], f: Filters, *, with_pod: bool = True) -> list[dict]:
    """Runs matching the filters, each with only the chunks that match (copies; inputs are untouched)."""
    out = []
    for run in runs:
        if f.source != "all" and run["source"] != f.source:
            continue
        if not _in_time(run, f) or (f.environment and run["environment"] != f.environment):
            continue
        chunks = [c for c in run["chunks"]
                  if (not f.browser or c["browser"] == f.browser) and (not (with_pod and f.pod) or c["pod"] == f.pod)]
        if chunks:
            out.append({**run, "chunks": chunks})
    return out


# ── Roll-ups ──────────────────────────────────────────────────────────────────

def _sum_counts(chunks: Iterable[Mapping]) -> dict:
    totals = {k: 0 for k in COUNT_KEYS}
    for c in chunks:
        for k in COUNT_KEYS:
            totals[k] += c[k]
    totals["total"] = sum(totals[k] for k in COUNT_KEYS)
    return totals


def run_status(run: Mapping) -> str:
    """passed | failed | running | queued | cancelled — from the chunks that are in view."""
    chunks, totals = run["chunks"], _sum_counts(run["chunks"])
    if run["source"] == LOCAL:
        raw = run.get("run_status", "").lower()
        if raw.startswith("cancel"):
            return "cancelled"
        return "failed" if raw.startswith("failed") or totals["failed"] or totals["broken"] else "passed"
    states = {c["status"] for c in chunks}
    if "running" in states:
        return "running"
    if "pending" in states:
        return "queued" if states == {"pending"} else "running"
    return "failed" if totals["failed"] or totals["broken"] else "passed"


def _duration_s(run: Mapping) -> float | None:
    chunks = run["chunks"]
    if run["source"] == LOCAL:
        return chunks[0]["duration_s"]
    if run.get("started_ms") is None or any(c["status"] != "completed" for c in chunks):
        return None
    ends = [c["updated_ms"] for c in chunks if c["updated_ms"]]
    return pc.duration(round((max(ends) - run["started_ms"]) / 1000, 1)) if ends else None   # None if it ends before it began


def summarize_run(run: Mapping) -> dict:
    chunks = run["chunks"]
    browsers = sorted({c["browser"] for c in chunks if c["browser"]})
    return {"id": run["id"], "source": run["source"], "started_ms": run["started_ms"],
            "environment": run["environment"], "browsers": browsers,
            "pods": sorted({c["pod"] for c in chunks if c["pod"]}), "status": run_status(run),
            "totals": _sum_counts(chunks), "duration_s": _duration_s(run), "chunks": len(chunks),
            "requested_by": run.get("requested_by", ""), "repo": run.get("repo", ""), "sha": run.get("sha", ""),
            "has_report": bool(run.get("has_report")), "has_logs": any(c["has_log"] for c in chunks)}


def _day(ms: int, zone) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=zone).strftime("%Y-%m-%d")


def _by(runs: Iterable[Mapping], key: Callable[[Mapping, Mapping], str]) -> list[dict]:
    groups: dict[str, dict] = defaultdict(lambda: {k: 0 for k in COUNT_KEYS})
    for run in runs:
        for c in run["chunks"]:
            label = key(run, c) or "unknown"
            for k in COUNT_KEYS:
                groups[label][k] += c[k]
    rows = [{"name": name, **counts, "total": sum(counts.values())} for name, counts in groups.items()]
    return sorted(rows, key=lambda r: (-r["total"], r["name"]))


def trend_by_day(runs: Iterable[Mapping], zone) -> list[dict]:
    days: dict[str, dict] = defaultdict(lambda: {k: 0 for k in COUNT_KEYS})
    for run in runs:
        if run.get("started_ms") is None:
            continue
        bucket = days[_day(run["started_ms"], zone)]
        for k, v in _sum_counts(run["chunks"]).items():
            if k != "total":
                bucket[k] += v
    if not days:
        return []
    first, last = min(days), max(days)
    span = (datetime.strptime(last, "%Y-%m-%d") - datetime.strptime(first, "%Y-%m-%d")).days
    if span <= MAX_DAYS_FILLED:                                     # continuous axis, zero for quiet days
        start = datetime.strptime(first, "%Y-%m-%d")
        for i in range(span + 1):
            days[(start + timedelta(days=i)).strftime("%Y-%m-%d")]
    return [{"date": d, **days[d]} for d in sorted(days)]


def pod_stats(runs: Iterable[Mapping]) -> list[dict]:
    pods: dict[str, dict] = {}
    for run in runs:
        for c in run["chunks"]:
            if not c["pod"]:
                continue
            p = pods.setdefault(c["pod"], {"pod": c["pod"], "browser": c["browser"], "chunks": 0, "tests": 0,
                                           "passed": 0, "failed": 0, "skipped": 0, "runs": set(), "last_ms": None})
            p["chunks"] += 1
            p["tests"] += c["passed"] + c["failed"] + c["broken"] + c["skipped"]
            p["passed"] += c["passed"]
            p["failed"] += c["failed"] + c["broken"]
            p["skipped"] += c["skipped"]
            p["runs"].add(run["id"])
            if c["updated_ms"] and (p["last_ms"] is None or c["updated_ms"] > p["last_ms"]):
                p["last_ms"] = c["updated_ms"]
    rows = [{**p, "runs": len(p["runs"])} for p in pods.values()]
    return sorted(rows, key=lambda r: (-(r["last_ms"] or 0), r["pod"]))


def kpis(runs: list[dict]) -> dict:
    chunks = [c for r in runs for c in r["chunks"]]
    totals = _sum_counts(chunks)
    durations = [d for c in chunks for t in c["tests"] if (d := pc.duration(t.get("duration_s"))) is not None]
    statuses = [run_status(r) for r in runs]
    return {"runs": len(runs), "running": sum(s in ("running", "queued") for s in statuses),
            "failed_runs": statuses.count("failed"), **totals,
            "pass_rate": round(100 * totals["passed"] / totals["total"], 1) if totals["total"] else None,
            "avg_test_s": round(sum(durations) / len(durations), 2) if durations else None,
            "pods": len({c["pod"] for c in chunks if c["pod"]})}


# ── The two payloads the dashboard uses ───────────────────────────────────────

def build_overview(*, grid_list: list[dict], fetch_grid_details: Callable[[list[str]], dict[str, dict]],
                   local_records: list[dict], filters: Filters, grid_status: dict) -> dict:
    """Everything the dashboard shows for one set of filters.

    grid_list           the coordinator's run list (cheap: environment, browsers, created_at per run)
    fetch_grid_details  run ids → {run id: /api/runs/<id> payload}; called once, only for runs that can
                        match the filters (so a narrow filter costs few coordinator calls)
    """
    f = filters
    grid_list = [r for r in grid_list if isinstance(r, Mapping)] if isinstance(grid_list, list) else []
    local_records = [r for r in local_records if isinstance(r, Mapping)] if isinstance(local_records, list) else []
    local_runs = [normalize_local_record(r) for r in local_records]

    def listed(run: Mapping) -> dict | None:                        # list payload → just enough to prefilter
        run_id = run.get("run_id")
        if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
            return None                                             # not an id we could ask the coordinator about
        return {"id": run_id, "source": GRID, "started_ms": pc.iso_to_ms(run.get("created_at")),
                "environment": pc.text(run.get("environment")),
                "chunks": [{"browser": b.lower(), "pod": ""} for b in pc.texts(run.get("browsers"))]}

    time_source = Filters(source=f.source, start_ms=f.start_ms, end_ms=f.end_ms)
    by_id: dict[str, dict] = {}
    for entry in map(listed, grid_list):
        if entry:
            by_id.setdefault(entry["id"], entry)                    # a run listed twice is still one run
    listed_runs = sorted(by_id.values(), key=lambda r: r["started_ms"] or 0, reverse=True)
    in_window_listed = filter_runs(listed_runs, time_source)
    in_window_local = filter_runs(local_runs, time_source)
    facets = {
        "environments": sorted({r["environment"] for r in in_window_listed + in_window_local if r["environment"]}),
        "browsers": sorted({c["browser"] for r in in_window_listed + in_window_local
                            for c in r["chunks"] if c["browser"]}),
    }

    matching = [r["id"] for r in filter_runs(listed_runs,
                                             Filters(source=f.source, environment=f.environment,
                                                     browser=f.browser, start_ms=f.start_ms, end_ms=f.end_ms))]
    wanted = matching[:MAX_GRID_RUNS] if f.source != LOCAL else []
    # The coordinator lists at most GRID_LIST_LIMIT runs: if it filled that, older runs inside the chosen
    # time frame may not have been listed at all.
    oldest_listed = min((r["started_ms"] for r in listed_runs if r["started_ms"]), default=None)
    list_incomplete = (f.source != LOCAL and len(grid_list) >= GRID_LIST_LIMIT
                       and (f.start_ms is None or (oldest_listed is not None and oldest_listed > f.start_ms)))
    details = fetch_grid_details(wanted) if wanted else {}
    details = details if isinstance(details, dict) else {}
    grid_runs = [normalize_grid_run(details[i]) for i in wanted if isinstance(details.get(i), dict)]

    before_pod = filter_runs(grid_runs + local_runs, f, with_pod=False)
    facets["pods"] = sorted({c["pod"] for r in before_pod for c in r["chunks"] if c["pod"]})
    in_view = filter_runs(grid_runs + local_runs, f)
    in_view.sort(key=lambda r: r["started_ms"] or 0, reverse=True)

    rows = [summarize_run(r) for r in in_view[:MAX_RUN_ROWS]]
    return {
        "filters": f.echo(), "facets": facets,
        "sources": {"grid": grid_status, "local": {"runs": len(local_runs)}},
        "kpis": kpis(in_view),
        "by_browser": _by(in_view, lambda run, c: c["browser"]),
        "by_environment": _by(in_view, lambda run, c: run["environment"]),
        "by_day": trend_by_day(in_view, f.zone),
        "pods": pod_stats(before_pod),
        "runs": rows, "truncated": len(in_view) > MAX_RUN_ROWS,
        "grid_runs_examined": len(grid_runs),
        "grid_runs_skipped": max(0, len(wanted) - len(grid_runs)),      # listed but couldn't be read
        "grid_runs_matched": len(matching) if f.source != LOCAL else 0,
        "grid_runs_capped": max(0, len(matching) - len(wanted)) if f.source != LOCAL else 0,   # matched, not examined
        "grid_list_incomplete": list_incomplete,
    }


def chunk_state(run: Mapping, chunk: Mapping) -> str:
    """passed | failed | running | pending | cancelled — what to show for one chunk."""
    if run["source"] == LOCAL:
        return run_status({**run, "chunks": [chunk]})
    if chunk["status"] != "completed":
        return chunk["status"]
    return "failed" if chunk["failed"] or chunk["broken"] else "passed"


def build_run_detail(run: Mapping, filters: Filters) -> dict:
    """One run with its chunks (browser, pod, per-test results) — the drill-down."""
    shown = filter_runs([run], Filters(browser=filters.browser, pod=filters.pod))
    if not shown:
        return {"run": None, "chunks": [], "hidden_chunks": len(run["chunks"])}
    view = shown[0]
    chunks = [{**{k: c[k] for k in ("chunk_id", "browser", "pod", "status", "passed", "failed", "broken", "skipped",
                                    "duration_s", "has_log", "tests")}, "state": chunk_state(view, c)}
              for c in view["chunks"]]
    return {"run": summarize_run(view), "chunks": chunks,
            "hidden_chunks": len(run["chunks"]) - len(chunks)}
