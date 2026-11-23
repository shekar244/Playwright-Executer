"""Consolidated reporting logic: Grid + local runs in one model, filters, roll-ups (pure functions)."""
from datetime import datetime, timedelta, timezone

import pytest

from amplify_server import consolidated as c

NOW = int(datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc).timestamp() * 1000)
HOUR, DAY = 3_600_000, 86_400_000


def iso(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def chunk(browser, pod, passed=1, failed=0, skipped=0, status="completed", ago=0, tests=None):
    outcomes = ["passed"] * passed + ["failed"] * failed + ["skipped"] * skipped
    results = [{"browser": browser, "duration_seconds": 3.0, "success": not failed,
                "test_results": [{"nodeid": f"tests/t.py::t{i}", "outcome": o, "duration": 1.5, "message": None}
                                 for i, o in enumerate(outcomes)]}] if status == "completed" else []
    return {"chunk_id": f"x-{browser}-{pod}", "browser": browser, "status": status, "assigned_runner": pod,
            "tests": tests or [f"tests/t.py::t{i}" for i in range(max(1, len(outcomes)))],
            "passed": passed if status == "completed" else None, "failed": failed if status == "completed" else None,
            "skipped": skipped if status == "completed" else None, "results": results,
            "updated_at": iso(NOW - ago)}


def grid(run_id, env="qa", ago=HOUR, chunks=(), by="alice"):
    return {"run_id": run_id, "environment": env, "created_at": iso(NOW - ago), "requested_by": by,
            "browsers": sorted({k["browser"] for k in chunks}), "chunk_details": list(chunks), "status": "completed",
            "chunks": {"total": len(chunks), "pending": 0, "running": 0, "completed": len(chunks)}}


def listed(detail):
    return {k: detail[k] for k in ("run_id", "environment", "created_at", "browsers")}


def local(ts_ago, status="passed", passed=2, failed=0, browser="chromium", repo="shop", report="/r/index.html",
          env=None):
    rec = {"ts": NOW - ts_ago, "repo": repo, "status": status, "browser": browser,
           "stats": {"passed": passed, "failed": failed, "broken": 0, "skipped": 0, "total": passed + failed},
           "report_path": report,
           "tests": [{"suite": "s", "name": f"n{i}", "method": f"m{i}", "status": "passed", "uid": f"u{i}",
                      "duration_s": 2.0, "report_path": f"/r/u{i}.html"} for i in range(passed + failed)]}
    if env:
        rec["environment"] = env
    return rec


def overview(grid_runs=(), local_records=(), **filters):
    by_id = {d["run_id"]: d for d in grid_runs}
    asked = []

    def fetch(ids):
        asked.append(list(ids))
        return {i: by_id[i] for i in ids if i in by_id}

    f = c.Filters(**filters)
    out = c.build_overview(grid_list=[listed(d) for d in grid_runs], fetch_grid_details=fetch,
                           local_records=list(local_records), filters=f, grid_status={"configured": True, "reachable": True})
    out["_asked"] = asked
    return out


# ── reading the request ───────────────────────────────────────────────────────

def test_defaults_are_unfiltered():
    f = c.parse_filters({}, NOW)
    assert (f.source, f.environment, f.browser, f.pod, f.start_ms, f.end_ms) == ("all", "", "", "", None, None)


def test_days_means_the_last_n_days_from_now():
    assert c.parse_filters({"days": "7"}, NOW).start_ms == NOW - 7 * DAY


def test_dates_are_the_users_days_not_utc_days():
    # India is UTC+5:30: JS reports -330. Its 20 Nov begins at 18:30 UTC on 19 Nov and the "to" day is inclusive.
    f = c.parse_filters({"from": "2026-11-20", "to": "2026-11-20", "tz": "-330"}, NOW)
    assert f.start_ms == int(datetime(2026, 11, 19, 18, 30, tzinfo=timezone.utc).timestamp() * 1000)
    assert f.end_ms - f.start_ms == DAY


def test_dates_west_of_utc():
    f = c.parse_filters({"from": "2026-11-20", "tz": "300"}, NOW)       # UTC-5
    assert f.start_ms == int(datetime(2026, 11, 20, 5, 0, tzinfo=timezone.utc).timestamp() * 1000)


def test_filter_text_is_trimmed_and_browsers_are_lowercase():
    f = c.parse_filters({"env": "  qa ", "browser": "Firefox", "pod": " pw-1 "}, NOW)
    assert (f.environment, f.browser, f.pod) == ("qa", "firefox", "pw-1")


@pytest.mark.parametrize("args, message", [
    ({"source": "cloud"}, "source"), ({"days": "x"}, "days"), ({"days": "0"}, "between"), ({"days": "99999"}, "between"),
    ({"from": "20-11-2026"}, "dates"), ({"to": "nope"}, "dates"), ({"tz": "abc"}, "tz"), ({"tz": "9999"}, "tz"),
    ({"from": "2026-11-21", "to": "2026-11-20"}, "before"),
])
def test_bad_requests_are_refused_with_a_reason(args, message):
    with pytest.raises(ValueError, match=message):
        c.parse_filters(args, NOW)


# ── raw data → runs ───────────────────────────────────────────────────────────

def test_a_finished_grid_chunk_carries_its_pod_and_per_test_results():
    run = c.normalize_grid_run(grid("r1", chunks=[chunk("edge", "pw-edge-1", passed=2, failed=1)]))
    ch = run["chunks"][0]
    assert (run["source"], run["environment"], ch["browser"], ch["pod"]) == ("grid", "qa", "edge", "pw-edge-1")
    assert (ch["passed"], ch["failed"], ch["has_log"]) == (2, 1, True)
    assert [t["outcome"] for t in ch["tests"]] == ["passed", "passed", "failed"] and ch["tests"][0]["duration_s"] == 1.5


def test_a_queued_chunk_has_no_outcomes_yet_and_counts_as_zero():
    run = c.normalize_grid_run(grid("r1", chunks=[chunk("edge", "", status="pending", tests=["a", "b"])]))
    ch = run["chunks"][0]
    assert (ch["passed"], ch["failed"], ch["pod"], ch["has_log"]) == (0, 0, "", False)
    assert [t["outcome"] for t in ch["tests"]] == [None, None]


def test_a_local_record_is_one_chunk_without_a_pod():
    run = c.normalize_local_record(local(HOUR, passed=3, failed=1, browser="Firefox"))
    ch = run["chunks"][0]
    assert (run["source"], run["environment"], run["id"]) == ("local", "local", f"local-{NOW - HOUR}")
    assert (ch["browser"], ch["pod"], ch["passed"], ch["failed"], len(ch["tests"])) == ("firefox", "", 3, 1, 4)
    assert run["has_report"] and ch["tests"][0]["uid"] == "u0" and ch["tests"][0]["has_report"]


def test_records_from_before_browsers_were_recorded_still_load():
    old = local(HOUR)
    del old["browser"]
    ch = c.normalize_local_record(old)["chunks"][0]
    assert ch["browser"] == "" and c.summarize_run(c.normalize_local_record(old))["browsers"] == []


def test_a_finished_chunk_that_reported_no_tests_is_an_error_not_pending():
    crashed = chunk("webkit", "p", passed=0, failed=1, tests=["tests/missing.py::t"])
    crashed["results"] = [{"success": False, "test_results": []}]            # pytest died before reporting
    ch = c.normalize_grid_run(grid("r1", chunks=[crashed]))["chunks"][0]
    assert [t["outcome"] for t in ch["tests"]] == ["error"] and ch["failed"] == 1
    odd = chunk("webkit", "p", passed=0, failed=0, tests=["tests/x.py::t"])
    odd["results"] = [{"success": True, "test_results": []}]
    assert c.normalize_grid_run(grid("r1", chunks=[odd]))["chunks"][0]["tests"][0]["outcome"] == "unknown"


# ── filtering ─────────────────────────────────────────────────────────────────

def runs3():
    return [c.normalize_grid_run(grid("g1", env="qa", ago=HOUR, chunks=[chunk("edge", "p1"), chunk("firefox", "p2", failed=1)])),
            c.normalize_grid_run(grid("g2", env="prod", ago=3 * DAY, chunks=[chunk("edge", "p1")])),
            c.normalize_local_record(local(2 * HOUR, browser="edge"))]


def ids(runs):
    return [r["id"] for r in runs]


def test_each_filter_narrows_the_runs():
    runs = runs3()
    assert ids(c.filter_runs(runs, c.Filters(source="grid"))) == ["g1", "g2"]
    assert ids(c.filter_runs(runs, c.Filters(source="local"))) == [f"local-{NOW - 2 * HOUR}"]
    assert ids(c.filter_runs(runs, c.Filters(environment="prod"))) == ["g2"]
    assert ids(c.filter_runs(runs, c.Filters(environment="local"))) == [f"local-{NOW - 2 * HOUR}"]
    assert ids(c.filter_runs(runs, c.Filters(start_ms=NOW - DAY))) == ["g1", f"local-{NOW - 2 * HOUR}"]


def test_time_frame_start_is_inclusive_and_end_is_exclusive():
    runs = [c.normalize_grid_run(grid("g", ago=HOUR, chunks=[chunk("edge", "p")]))]
    started = NOW - HOUR
    assert ids(c.filter_runs(runs, c.Filters(start_ms=started))) == ["g"]
    assert ids(c.filter_runs(runs, c.Filters(start_ms=started + 1))) == []
    assert ids(c.filter_runs(runs, c.Filters(end_ms=started))) == []
    assert ids(c.filter_runs(runs, c.Filters(end_ms=started + 1))) == ["g"]


def test_browser_and_pod_filters_keep_only_the_matching_chunks():
    [g1] = c.filter_runs(runs3(), c.Filters(browser="firefox"))
    assert [(k["browser"], k["pod"]) for k in g1["chunks"]] == [("firefox", "p2")]
    [g1] = c.filter_runs(runs3(), c.Filters(pod="p1", source="grid", environment="qa"))
    assert [k["pod"] for k in g1["chunks"]] == ["p1"]


def test_a_run_with_nothing_left_after_filtering_is_dropped():
    assert c.filter_runs(runs3(), c.Filters(browser="webkit")) == []


def test_filtering_does_not_modify_its_input():
    runs = runs3()
    c.filter_runs(runs, c.Filters(browser="firefox"))
    assert len(runs[0]["chunks"]) == 2


# ── roll-ups ──────────────────────────────────────────────────────────────────

def test_run_status_for_grid_runs():
    mk = lambda *statuses, failed=0: {"source": "grid", "chunks": [  # noqa: E731
        {"status": s, "passed": 1, "failed": failed, "broken": 0, "skipped": 0} for s in statuses]}
    assert c.run_status(mk("completed", "completed")) == "passed"
    assert c.run_status(mk("completed", failed=1)) == "failed"
    assert c.run_status(mk("pending", "pending")) == "queued"
    assert c.run_status(mk("pending", "completed")) == "running"
    assert c.run_status(mk("running", "completed")) == "running"


def test_run_status_for_local_runs():
    st = lambda raw, failed=0: c.run_status({"source": "local", "run_status": raw, "chunks": [  # noqa: E731
        {"passed": 1, "failed": failed, "broken": 0, "skipped": 0}]})
    assert st("passed") == "passed" and st("failed:1") == "failed" and st("cancelled") == "cancelled"
    assert st("passed", failed=1) == "failed"


def test_a_filtered_run_reports_the_status_of_the_chunks_in_view():
    [g1] = c.filter_runs(runs3(), c.Filters(browser="edge", source="grid", environment="qa"))
    assert c.run_status(g1) == "passed"                       # its failure was on firefox
    [g1] = c.filter_runs(runs3(), c.Filters(browser="firefox"))
    assert c.run_status(g1) == "failed"


def test_duration_is_wall_clock_for_finished_grid_runs_and_unknown_while_running():
    done = c.normalize_grid_run(grid("g", ago=HOUR, chunks=[chunk("edge", "p", ago=HOUR - 30_000)]))
    assert c.summarize_run(done)["duration_s"] == 30.0
    live = c.normalize_grid_run(grid("g", chunks=[chunk("edge", "p", status="running")]))
    assert c.summarize_run(live)["duration_s"] is None


def test_overview_consolidates_grid_and_local_totals():
    out = overview([grid("g1", chunks=[chunk("edge", "p1", passed=2), chunk("firefox", "p2", passed=1, failed=1)])],
                   [local(2 * HOUR, passed=3)])
    k = out["kpis"]
    assert (k["runs"], k["total"], k["passed"], k["failed"], k["pass_rate"], k["pods"]) == (2, 7, 6, 1, 85.7, 2)
    assert {r["source"] for r in out["runs"]} == {"grid", "local"}
    assert [r["id"] for r in out["runs"]][0] == "g1"                      # newest first


def test_browser_filter_recomputes_totals_from_that_browsers_chunks_only():
    out = overview([grid("g1", chunks=[chunk("edge", "p1", passed=2), chunk("firefox", "p2", passed=1, failed=1)])],
                   browser="firefox")
    assert out["kpis"]["total"] == 2 and out["kpis"]["failed"] == 1
    assert [b["name"] for b in out["by_browser"]] == ["firefox"]
    assert out["runs"][0]["browsers"] == ["firefox"] and out["runs"][0]["pods"] == ["p2"]


def test_facets_do_not_collapse_when_a_filter_is_chosen():
    runs = [grid("g1", env="qa", chunks=[chunk("edge", "p1")]), grid("g2", env="prod", chunks=[chunk("firefox", "p2")])]
    out = overview(runs, [local(HOUR, browser="webkit")], environment="qa", browser="edge")
    assert out["facets"]["environments"] == ["local", "prod", "qa"]
    assert out["facets"]["browsers"] == ["edge", "firefox", "webkit"]


def test_the_pod_table_lists_every_pod_in_view_even_after_picking_one():
    runs = [grid("g1", chunks=[chunk("edge", "p1", passed=2), chunk("edge", "p2", passed=1)])]
    out = overview(runs, pod="p1")
    assert sorted(p["pod"] for p in out["pods"]) == ["p1", "p2"]
    assert out["kpis"]["total"] == 2 and out["runs"][0]["pods"] == ["p1"]
    assert out["facets"]["pods"] == ["p1", "p2"]


def test_pod_stats_add_up_per_pod():
    runs = [grid("g1", chunks=[chunk("edge", "p1", passed=2, failed=1)]), grid("g2", chunks=[chunk("edge", "p1", passed=1)])]
    [p] = overview(runs)["pods"]
    assert (p["pod"], p["browser"], p["chunks"], p["tests"], p["passed"], p["failed"], p["runs"]) == ("p1", "edge", 2, 4, 3, 1, 2)


def test_local_runs_never_appear_as_pods():
    assert overview([], [local(HOUR)])["pods"] == []


def test_only_runs_that_can_match_are_fetched_from_the_coordinator():
    runs = [grid("old", ago=10 * DAY, chunks=[chunk("edge", "p")]), grid("prod", env="prod", chunks=[chunk("edge", "p")]),
            grid("ff", chunks=[chunk("firefox", "p")]), grid("hit", chunks=[chunk("edge", "p")])]
    out = overview(runs, start_ms=NOW - 2 * DAY, environment="qa", browser="edge")
    assert out["_asked"] == [["hit"]]


def test_a_local_only_view_never_asks_the_coordinator():
    out = overview([grid("g", chunks=[chunk("edge", "p")])], [local(HOUR)], source="local")
    assert out["_asked"] == [] and [r["source"] for r in out["runs"]] == ["local"]


def test_runs_the_coordinator_could_not_return_are_counted_not_fatal():
    runs = [grid("a", chunks=[chunk("edge", "p")]), grid("b", chunks=[chunk("edge", "p")])]
    f = c.Filters()
    out = c.build_overview(grid_list=[listed(r) for r in runs], local_records=[], filters=f, grid_status={},
                           fetch_grid_details=lambda ids: {"a": runs[0]})
    assert out["grid_runs_examined"] == 1 and out["grid_runs_skipped"] == 1 and len(out["runs"]) == 1


def test_the_newest_grid_runs_are_the_ones_examined(monkeypatch):
    monkeypatch.setattr(c, "MAX_GRID_RUNS", 2)
    runs = [grid(f"r{i}", ago=i * HOUR, chunks=[chunk("edge", "p")]) for i in (5, 1, 3, 2, 4)]
    assert overview(runs)["_asked"] == [["r1", "r2"]]


def test_the_runs_table_is_capped_and_says_so(monkeypatch):
    monkeypatch.setattr(c, "MAX_RUN_ROWS", 3)
    out = overview([], [local(i * HOUR) for i in range(1, 6)])
    assert len(out["runs"]) == 3 and out["truncated"] is True and out["kpis"]["runs"] == 5


def test_by_environment_and_browser_breakdowns():
    runs = [grid("g1", env="qa", chunks=[chunk("edge", "p", passed=2)]), grid("g2", env="prod", chunks=[chunk("edge", "p", passed=0, failed=1)])]
    out = overview(runs, [local(HOUR, passed=1, browser="edge")])
    assert {e["name"]: (e["passed"], e["failed"]) for e in out["by_environment"]} == {"qa": (2, 0), "prod": (0, 1), "local": (1, 0)}
    assert out["by_browser"][0]["name"] == "edge" and out["by_browser"][0]["total"] == 4


def test_runs_with_no_browser_recorded_group_as_unknown():
    assert overview([], [dict(local(HOUR), browser="")])["by_browser"][0]["name"] == "unknown"


def test_trend_fills_quiet_days_and_buckets_in_the_users_timezone():
    # 23:30 UTC on the 19th is already the 20th in India (UTC+5:30), so its tests land on the 20th there.
    late = int(datetime(2026, 11, 19, 23, 30, tzinfo=timezone.utc).timestamp() * 1000)
    early = int(datetime(2026, 11, 17, 10, 0, tzinfo=timezone.utc).timestamp() * 1000)
    runs = [c.normalize_local_record({**local(0), "ts": late}), c.normalize_local_record({**local(0), "ts": early})]
    utc = c.trend_by_day(runs, timezone.utc)
    assert [d["date"] for d in utc] == ["2026-11-17", "2026-11-18", "2026-11-19"] and utc[1]["passed"] == 0
    assert [d["date"] for d in c.trend_by_day(runs, timezone(timedelta(hours=5, minutes=30)))][-1] == "2026-11-20"


def test_kpis_average_and_pass_rate_handle_empty_input():
    k = c.kpis([])
    assert (k["runs"], k["total"], k["pass_rate"], k["avg_test_s"]) == (0, 0, None, None)


def test_kpi_average_test_time_and_in_flight_count():
    done = c.normalize_grid_run(grid("a", chunks=[chunk("edge", "p", passed=2)]))
    live = c.normalize_grid_run(grid("b", chunks=[chunk("edge", "p", status="running")]))
    k = c.kpis([done, live])
    assert k["avg_test_s"] == 1.5 and k["running"] == 1 and k["runs"] == 2


# ── drill-down ────────────────────────────────────────────────────────────────

def test_run_detail_lists_chunks_with_pod_and_tests():
    run = c.normalize_grid_run(grid("g1", chunks=[chunk("edge", "p1", passed=2), chunk("firefox", "p2", passed=0, failed=1)]))
    detail = c.build_run_detail(run, c.Filters())
    assert detail["run"]["id"] == "g1" and detail["hidden_chunks"] == 0
    by_browser = {k["browser"]: k for k in detail["chunks"]}
    assert by_browser["firefox"]["pod"] == "p2" and by_browser["firefox"]["tests"][0]["outcome"] == "failed"
    assert by_browser["edge"]["has_log"] is True


def test_run_detail_follows_the_browser_and_pod_filters_and_says_what_is_hidden():
    run = c.normalize_grid_run(grid("g1", chunks=[chunk("edge", "p1"), chunk("firefox", "p2")]))
    detail = c.build_run_detail(run, c.Filters(browser="edge"))
    assert [k["browser"] for k in detail["chunks"]] == ["edge"] and detail["hidden_chunks"] == 1
    assert c.build_run_detail(run, c.Filters(browser="webkit"))["run"] is None


# ── daylight saving: a named zone reads July with July's offset ───────────────

def utc_ms(*args):
    return int(datetime(*args, tzinfo=timezone.utc).timestamp() * 1000)


def test_a_named_zone_uses_the_offset_of_the_date_not_of_today():
    # Today (NOW) is in November: New York is UTC-5 (JS says 300). On 1 July it is UTC-4.
    f = c.parse_filters({"from": "2026-07-01", "to": "2026-07-01", "tz": "300", "tzname": "America/New_York"}, NOW)
    assert f.start_ms == utc_ms(2026, 7, 1, 4) and f.end_ms == utc_ms(2026, 7, 2, 4)
    fixed = c.parse_filters({"from": "2026-07-01", "to": "2026-07-01", "tz": "300"}, NOW)
    assert fixed.start_ms == utc_ms(2026, 7, 1, 5)                         # the old fixed-offset reading, an hour late


def test_the_end_of_a_daylight_saving_day_is_not_24_hours_after_its_start():
    f = c.parse_filters({"from": "2026-11-01", "to": "2026-11-01", "tz": "300", "tzname": "America/New_York"}, NOW)
    assert f.end_ms - f.start_ms == 25 * HOUR                              # the clocks go back that night


def test_runs_just_after_local_midnight_land_on_the_right_day_and_in_the_right_range():
    started = utc_ms(2026, 7, 1, 4, 30)                                    # 00:30 EDT on 1 July
    run = c.normalize_local_record({**local(0), "ts": started})
    ny = c.parse_filters({"from": "2026-07-01", "to": "2026-07-01", "tz": "300", "tzname": "America/New_York"}, NOW)
    assert c.filter_runs([run], ny) != []
    assert c.trend_by_day([run], ny.zone)[0]["date"] == "2026-07-01"
    assert c.trend_by_day([run], timezone(timedelta(hours=-5)))[0]["date"] == "2026-06-30"


def test_an_unknown_zone_name_falls_back_to_the_browsers_offset():
    f = c.parse_filters({"from": "2026-07-01", "tz": "300", "tzname": "Mars/Olympus_Mons"}, NOW)
    assert f.start_ms == utc_ms(2026, 7, 1, 5)


@pytest.mark.parametrize("name", ["../etc/passwd", "a b", "Europe/London; drop", "x" * 5 + "/" * 5, "A/B/C/D"])
def test_a_malformed_zone_name_is_refused(name):
    with pytest.raises(ValueError, match="tzname"):
        c.parse_filters({"tzname": name}, NOW)


# ── caps are no longer silent ─────────────────────────────────────────────────

def many(n, **kw):
    return [grid(f"r{i}", ago=(i + 1) * 60_000, chunks=[chunk("edge", "p")], **kw) for i in range(n)]


def test_the_overview_says_how_many_matching_grid_runs_were_not_examined(monkeypatch):
    monkeypatch.setattr(c, "MAX_GRID_RUNS", 3)
    out = overview(many(5))
    assert (out["grid_runs_matched"], out["grid_runs_examined"], out["grid_runs_capped"]) == (5, 3, 2)


def test_nothing_is_reported_capped_when_everything_fits():
    out = overview(many(3))
    assert (out["grid_runs_capped"], out["grid_list_incomplete"]) == (0, False)


def test_a_full_coordinator_list_may_hide_older_runs_in_the_time_frame(monkeypatch):
    monkeypatch.setattr(c, "GRID_LIST_LIMIT", 4)
    runs = many(4)                                                           # the list is exactly full
    assert overview(runs)["grid_list_incomplete"] is True                    # "all time": older runs may exist
    assert overview(runs, start_ms=NOW - 2 * 60_000)["grid_list_incomplete"] is False    # window starts after the oldest listed (4 min ago)
    assert overview(runs, start_ms=NOW - 10 * 60_000)["grid_list_incomplete"] is True    # window reaches back past it
    assert overview(runs, start_ms=NOW - 3 * DAY)["grid_list_incomplete"] is True
    assert overview(many(3))["grid_list_incomplete"] is False                # not full → everything was listed
    assert overview(runs, source="local")["grid_list_incomplete"] is False


# ── a crashed local run is not "passed" ───────────────────────────────────────

def test_a_local_run_that_failed_without_reporting_any_tests_is_shown_as_failed():
    rec = {**local(HOUR, status="failed:2", passed=0), "tests": []}
    detail = c.build_run_detail(c.normalize_local_record(rec), c.Filters())
    assert detail["run"]["status"] == "failed" and detail["chunks"][0]["state"] == "failed"
    cancelled = c.build_run_detail(c.normalize_local_record({**local(HOUR, status="cancelled", passed=0), "tests": []}), c.Filters())
    assert cancelled["chunks"][0]["state"] == "cancelled"


def test_chunk_states_for_grid_chunks():
    run = c.normalize_grid_run(grid("g", chunks=[chunk("edge", "p1"), chunk("firefox", "p2", passed=0, failed=1),
                                                chunk("webkit", "", status="pending"), chunk("chrome", "p3", status="running")]))
    states = {k["browser"]: k["state"] for k in c.build_run_detail(run, c.Filters())["chunks"]}
    assert states == {"edge": "passed", "firefox": "failed", "webkit": "pending", "chrome": "running"}


# ── Grid runs carry their repo, commit and report availability ────────────────

def test_a_grid_run_reads_repo_sha_and_has_allure_from_the_coordinator_payload():
    sha = "f" * 40
    detail = grid("r1", chunks=[chunk("edge", "p1"), chunk("firefox", "p2")])
    detail.update(repo="shop-tests", sha=sha)
    detail["chunk_details"][1]["has_allure"] = True
    run = c.normalize_grid_run(detail)
    assert (run["repo"], run["sha"], run["has_report"]) == ("shop-tests", sha, True)
    assert [ch["has_allure"] for ch in run["chunks"]] == [False, True]
    summary = c.summarize_run(run)
    assert (summary["repo"], summary["sha"], summary["has_report"]) == ("shop-tests", sha, True)


@pytest.mark.parametrize("extra", [{}, {"repo": None, "sha": None}, {"repo": "", "sha": ""}])
def test_a_grid_run_without_repo_or_allure_data_has_neither(extra):
    detail = grid("r1", chunks=[chunk("edge", "p1")])
    detail.update(extra)
    detail["chunk_details"][0]["has_allure"] = None
    run = c.normalize_grid_run(detail)
    assert (run["repo"], run["sha"], run["has_report"]) == ("", "", False)
    assert c.summarize_run(run)["has_report"] is False


def test_local_runs_have_no_sha():
    summary = c.summarize_run(c.normalize_local_record(local(HOUR)))
    assert summary["repo"] == "shop" and summary["sha"] == "" and summary["has_report"] is True
