"""The dashboard reads Grid runs whose per-test results were written by the pods' test code, and run-history
files on disk. None of that may be able to break the overview for everybody: nothing raises, and the answer is
always valid JSON (no NaN / Infinity) with numbers where numbers belong.
"""
import copy
import importlib
import json
import math
import random
import time
from datetime import datetime, timezone

import pytest
from flask import Flask

from amplify_server import consolidated as c
from amplify_server import grid_data
from amplify_server.payload_coerce import count, duration, epoch_ms, iso_to_ms

NOW = int(time.time() * 1000)
HOUR = 3_600_000
HOSTILE = [None, True, False, 0, -1, 7, 1.5, -2.5, float("nan"), float("inf"), float("-inf"), 10 ** 400, 1e308, -(10 ** 15),
           10 ** 15, "", "oops", "12", "1.5", "<img src=x onerror=window.__xss=1>", "x\"'><script>1</script>", [], [1, "a"],
           [None], {}, {"a": 1}, [{}], [[]], "0001-01-01T00:00:00+14:00", "9999-12-31T23:59:59-14:00", "2026-13-45",
           "2026-11-20T10:00:00Z"]


def _id(value):
    return repr(value)[:24]


def iso(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def good_chunk(browser="edge", pod="pw-edge-1", n=2):
    return {"chunk_id": f"r-{browser}", "browser": browser, "status": "completed", "assigned_runner": pod,
            "tests": ["t.py::a", "t.py::b"], "passed": n, "failed": 1, "skipped": 0, "updated_at": iso(NOW - 1000),
            "has_allure": True,
            "results": [{"duration_seconds": 3.5, "stdout": "", "stderr": "", "test_results": [
                {"nodeid": f"t.py::t{i}", "outcome": "passed", "duration": 0.5 + i, "message": "m"} for i in range(n)]}]}


def good_detail(run_id="run-1"):
    return {"run_id": run_id, "environment": "qa", "created_at": iso(NOW - HOUR), "requested_by": "boss",
            "repo": "shop", "sha": "a" * 40, "browsers": ["edge", "firefox"], "status": "completed",
            "chunks": {"total": 2, "pending": 0, "running": 0, "completed": 2},
            "chunk_details": [good_chunk(), good_chunk("firefox", "pw-ff-1")]}


def good_record(ts=NOW - HOUR):
    return {"ts": ts, "repo": "shop", "status": "passed", "browser": "chromium", "environment": "local",
            "stats": {"passed": 2, "failed": 0, "broken": 0, "skipped": 1, "total": 3}, "report_path": "r/index.html",
            "tests": [{"suite": "s", "name": f"n{i}", "method": f"m{i}", "fullName": f"s.m{i}", "status": "passed",
                       "uid": f"u{i}", "duration_s": 1.25, "report_path": ""} for i in range(2)]}


# ── the coercion helpers ──────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [True, False, "oops", "1.5", "0.1</span><img src=x onerror=1>", float("nan"), float("inf"),
                                 float("-inf"), -1, -0.001, [], [1], {}, {"a": 1}, None, 10 ** 400, 1e12 * 1e12])
def test_a_duration_must_be_a_finite_non_negative_number(bad):
    assert duration(bad) is None


@pytest.mark.parametrize("good, expected", [(0, 0.0), (0.0, 0.0), (1, 1.0), (1.5, 1.5), (3600, 3600.0)])
def test_real_durations_survive(good, expected):
    assert duration(good) == expected and isinstance(duration(good), float)


@pytest.mark.parametrize("bad", [True, False, None, "x", "1.5", "-3", float("nan"), float("inf"), -float("inf"), -5, [], {}, [3]])
def test_a_count_is_zero_unless_it_is_a_whole_number(bad):
    assert count(bad) == 0


@pytest.mark.parametrize("good, expected", [(3, 3), (3.9, 3), ("12", 12), (0, 0), (10 ** 400, count(10 ** 9))])
def test_real_counts_survive(good, expected):
    assert count(good) == expected


@pytest.mark.parametrize("bad", ["0001-01-01T00:00:00+14:00", "9999-12-31T23:59:59-14:00", "nope", 5, [], {}, True, None, ""])
def test_a_timestamp_must_be_a_plausible_iso_date(bad):
    assert iso_to_ms(bad) is None


@pytest.mark.parametrize("bad", [True, None, -5, 0, float("nan"), float("inf"), "x", [], {}, 10 ** 30, "9" * 5000])
def test_an_epoch_must_be_a_plausible_date(bad):
    assert epoch_ms(bad) == 0


# ── the normalizers, one hostile value at a time ──────────────────────────────

@pytest.mark.parametrize("bad", HOSTILE, ids=_id)
def test_a_hostile_test_duration_never_reaches_the_roll_ups(bad):
    detail = good_detail()
    detail["chunk_details"][0]["results"][0]["test_results"][0]["duration"] = bad
    run = c.normalize_grid_run(detail)
    assert all(t["duration_s"] is None or (isinstance(t["duration_s"], float) and math.isfinite(t["duration_s"]))
               for ch in run["chunks"] for t in ch["tests"])
    out = c.build_overview(grid_list=[detail], fetch_grid_details=lambda ids: {"run-1": detail}, local_records=[],
                           filters=c.Filters(), grid_status={})
    json.dumps(out, allow_nan=False)
    avg = out["kpis"]["avg_test_s"]
    assert avg is None or (isinstance(avg, float) and math.isfinite(avg) and avg >= 0)


@pytest.mark.parametrize("bad", HOSTILE, ids=_id)
def test_a_hostile_chunk_duration_and_local_duration_are_dropped(bad):
    detail = good_detail()
    detail["chunk_details"][0]["results"][0]["duration_seconds"] = bad
    run = c.normalize_grid_run(detail)
    assert run["chunks"][0]["duration_s"] is None or math.isfinite(run["chunks"][0]["duration_s"])
    record = good_record()
    record["tests"][0]["duration_s"] = bad
    local = c.normalize_local_record(record)
    assert local["chunks"][0]["duration_s"] is None or math.isfinite(local["chunks"][0]["duration_s"])
    json.dumps([run, local], allow_nan=False)


def test_a_non_numeric_duration_does_not_take_the_overview_down_for_everybody(monkeypatch, data_dir):
    """The reported case: one run whose pod wrote "oops" as a duration made /api/dashboard/overview a 500."""
    detail = good_detail()
    detail["chunk_details"][0]["results"][0]["test_results"][0]["duration"] = "oops"
    detail["chunk_details"][1]["results"][0]["test_results"][0]["duration"] = []
    grid_data.clear_cache()
    monkeypatch.setattr(grid_data, "load_grid_config", lambda: {"coordinator_url": "http://c:8000", "api_token": ""})

    def request(method, path, params=None, body=None, timeout=0):
        if path == "/api/runs":
            return {"runs": [{k: detail[k] for k in ("run_id", "environment", "browsers", "created_at")}]}, 200
        return detail, 200

    monkeypatch.setattr(grid_data, "coordinator_request", request)
    app = Flask(__name__)
    for name in ("auth", "dashboard"):
        app.register_blueprint(importlib.import_module(f"amplify_server.routes.{name}").bp)
    client = app.test_client()
    assert client.post("/api/auth/register", json={"username": "boss", "password": "secret123"}).status_code == 201
    r = client.get("/api/dashboard/overview?days=7")
    assert r.status_code == 200
    body = r.get_json()
    assert body["kpis"]["runs"] == 1 and isinstance(body["kpis"]["avg_test_s"], float)
    assert client.get("/api/dashboard/run/grid/run-1").status_code == 200
    grid_data.clear_cache()


def every_field_survives_any_type(make, fields, check, target=None):
    """Put each hostile value into each field in turn (inside `target(payload)` when given); `check(payload)` must not raise."""
    failures = []
    for field in fields:
        for bad in HOSTILE:
            payload = make()
            (target(payload) if target else payload)[field] = bad
            try:
                check(payload)
            except Exception as exc:                                    # noqa: BLE001 - reported below with its context
                failures.append(f"{field}={_id(bad)}: {type(exc).__name__}: {exc}")
    assert not failures, "\n".join(failures[:10])


def test_every_chunk_field_survives_any_type():
    def check(detail):
        run = c.normalize_grid_run(detail)
        json.dumps(c.build_run_detail(run, c.Filters()), allow_nan=False)
        json.dumps(c.summarize_run(run), allow_nan=False)
    every_field_survives_any_type(good_detail, ["status", "passed", "failed", "skipped", "browser", "assigned_runner",
                                                "chunk_id", "updated_at", "has_allure", "tests", "results"],
                                  check, target=lambda d: d["chunk_details"][0])


def test_every_run_field_survives_any_type():
    def check(detail):
        out = c.build_overview(grid_list=[detail], fetch_grid_details=lambda ids: {i: detail for i in ids},
                               local_records=[], filters=c.Filters(), grid_status={})
        json.dumps(out, allow_nan=False)
    every_field_survives_any_type(good_detail, ["run_id", "environment", "created_at", "requested_by", "repo", "sha",
                                                "browsers", "chunk_details", "status", "chunks"], check)


@pytest.mark.parametrize("bad", [None, "x", 5, [], [1], True, float("nan")], ids=_id)
def test_things_that_are_not_runs_are_ignored(bad):
    out = c.build_overview(grid_list=[bad, good_detail()], fetch_grid_details=lambda ids: {i: bad for i in ids},
                           local_records=[bad, good_record()], filters=c.Filters(), grid_status={})
    assert out["kpis"]["runs"] == 1 and out["grid_runs_skipped"] == 1
    json.dumps(out, allow_nan=False)


@pytest.mark.parametrize("bad", [None, "x", 5, {"a": 1}, True])
def test_a_list_that_is_not_a_list_is_nothing(bad):
    out = c.build_overview(grid_list=bad, fetch_grid_details=lambda ids: bad, local_records=bad,
                           filters=c.Filters(), grid_status={})
    assert out["kpis"]["runs"] == 0
    assert c.normalize_grid_run(bad)["chunks"] == [] and c.normalize_local_record(bad)["chunks"][0]["tests"] == []


@pytest.mark.parametrize("bad_id", ["../etc", "a/b", "a b", "", "x" * 200, "..", ".hidden", 5, None, ["r"]])
def test_run_ids_that_could_not_be_a_url_path_are_never_asked_for(bad_id):
    asked = []
    out = c.build_overview(grid_list=[{"run_id": bad_id, "environment": "qa", "browsers": ["edge"], "created_at": iso(NOW)}],
                           fetch_grid_details=lambda ids: asked.extend(ids) or {}, local_records=[],
                           filters=c.Filters(), grid_status={})
    assert asked == [] and out["kpis"]["runs"] == 0


def test_a_run_listed_twice_is_counted_once():
    listing = {"run_id": "run-1", "environment": "qa", "browsers": ["edge"], "created_at": iso(NOW - HOUR)}
    out = c.build_overview(grid_list=[listing, dict(listing)], fetch_grid_details=lambda ids: {i: good_detail(i) for i in ids},
                           local_records=[], filters=c.Filters(), grid_status={})
    assert out["kpis"]["runs"] == 1


def test_every_history_field_survives_any_type():
    def check(record):
        out = c.build_overview(grid_list=[], fetch_grid_details=lambda ids: {}, local_records=[record],
                               filters=c.Filters(), grid_status={})
        json.dumps(out, allow_nan=False)
        run = c.normalize_local_record(record)
        json.dumps(c.build_run_detail(run, c.Filters()), allow_nan=False)
        assert c.local_run_id(record) == run["id"]
    every_field_survives_any_type(good_record, ["ts", "repo", "status", "browser", "environment", "stats", "report_path",
                                                "tests"], check)


def test_every_history_test_field_survives_any_type():
    def check(record):
        json.dumps(c.build_run_detail(c.normalize_local_record(record), c.Filters()), allow_nan=False)
    every_field_survives_any_type(good_record, ["suite", "name", "method", "fullName", "status", "uid", "duration_s",
                                                "report_path"], check, target=lambda r: r["tests"][0])


def test_the_last_day_the_calendar_has_is_a_400_not_a_crash():
    """"to" means the whole of that day, which needs the next one: day 9999-12-31 has none (OverflowError)."""
    with pytest.raises(ValueError, match="dates like"):
        c.parse_filters({"to": "9999-12-31"}, NOW)


# ── fuzzing: random corruption of valid payloads ──────────────────────────────

def _paths(node, prefix=()):
    yield prefix
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _paths(v, prefix + (k,))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _paths(v, prefix + (i,))


def corrupt(payload, rng, hits):
    payload = copy.deepcopy(payload)
    for _ in range(hits):
        path = [p for p in _paths(payload) if p]
        target = rng.choice(path)
        parent = payload
        for key in target[:-1]:
            parent = parent[key]
        if rng.random() < 0.15 and isinstance(parent, dict):
            del parent[target[-1]]
        else:
            parent[target[-1]] = copy.deepcopy(rng.choice(HOSTILE))
    return payload


FILTERS = [c.Filters(), c.Filters(source="grid"), c.Filters(source="local", environment="local"),
           c.Filters(browser="edge", pod="pw-edge-1", environment="qa"),
           c.Filters(start_ms=NOW - 10 * HOUR, end_ms=NOW + HOUR, tz_minutes=-540, tz_name="Asia/Tokyo")]


def test_fuzzed_payloads_never_raise_and_always_serialise():
    rng = random.Random(20261120)
    for round_ in range(1500):
        details = [corrupt(good_detail(f"run-{i}"), rng, rng.randint(1, 4)) for i in range(2)]
        listing = [corrupt({k: d[k] for k in ("run_id", "environment", "browsers", "created_at") if k in d}, rng, rng.randint(0, 2))
                   for d in details]
        records = [corrupt(good_record(NOW - (i + 1) * HOUR), rng, rng.randint(1, 4)) for i in range(2)]
        filters = rng.choice(FILTERS)
        by_id = {d["run_id"]: d for d in details if isinstance(d.get("run_id"), str)}
        out = c.build_overview(grid_list=listing, fetch_grid_details=lambda ids: {i: by_id[i] for i in ids if i in by_id},
                               local_records=records, filters=filters, grid_status={})
        json.dumps(out, allow_nan=False)
        for run in out["runs"]:
            assert all(isinstance(run["totals"][k], int) and run["totals"][k] >= 0 for k in ("passed", "failed", "skipped", "total"))
            assert run["duration_s"] is None or (math.isfinite(run["duration_s"]) and run["duration_s"] >= 0), (round_, run)
        for d in details:
            normalized = c.normalize_grid_run(d)
            json.dumps(c.build_run_detail(normalized, rng.choice(FILTERS)), allow_nan=False)
        for r in records:
            json.dumps(c.build_run_detail(c.normalize_local_record(r), rng.choice(FILTERS)), allow_nan=False)
