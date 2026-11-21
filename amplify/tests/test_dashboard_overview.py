"""The consolidated dashboard routes: overview, drill-down, and viewing HTML reports in the app."""
import importlib
import json
import os
import time
from datetime import datetime, timezone

import jwt
import pytest
from flask import Flask

from amplify import grid_data, report_viewer
from amplify.auth_utils import generate_jwt
from amplify.config import settings
from amplify.routes.history import save_history

NOW = int(time.time() * 1000)
HOUR = 3_600_000


def iso(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


@pytest.fixture
def app(data_dir):
    flask_app = Flask(__name__)
    for name in ("auth", "dashboard"):
        flask_app.register_blueprint(importlib.import_module(f"amplify.routes.{name}").bp)
    return flask_app


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch):
    grid_data.clear_cache()
    yield
    grid_data.clear_cache()


def chunk(browser, pod, passed=1, failed=0, status="completed"):
    outcomes = ["passed"] * passed + ["failed"] * failed
    return {"chunk_id": f"r-{browser}-{pod}", "browser": browser, "status": status, "assigned_runner": pod,
            "tests": ["tests/t.py::t"], "passed": passed, "failed": failed, "skipped": 0, "updated_at": iso(NOW - 1000),
            "results": [{"duration_seconds": 3.0, "test_results": [
                {"nodeid": f"tests/t.py::t{i}", "outcome": o, "duration": 1.0, "message": None}
                for i, o in enumerate(outcomes)]}]}


def grid_run(run_id="run-1", env="qa", ago=HOUR, chunks=None):
    chunks = chunks or [chunk("edge", "pw-edge-1"), chunk("firefox", "pw-firefox-1", passed=0, failed=1)]
    return {"run_id": run_id, "environment": env, "created_at": iso(NOW - ago), "requested_by": "boss",
            "browsers": sorted({c["browser"] for c in chunks}), "chunk_details": chunks, "status": "completed",
            "chunks": {"total": len(chunks), "pending": 0, "running": 0, "completed": len(chunks)}}


@pytest.fixture
def coordinator(monkeypatch):
    """A fake coordinator behind grid_data: runs by id, plus a count of detail calls."""
    state = {"runs": {}, "detail_calls": [], "list_calls": 0, "down": None}

    def request(method, path, params=None, body=None, timeout=0):
        if state["down"]:
            return {"error": f"Coordinator unreachable: {state['down']}"}, 502
        if path == "/api/runs":
            state["list_calls"] += 1
            return {"runs": [{k: r[k] for k in ("run_id", "environment", "browsers", "created_at")}
                             for r in state["runs"].values()]}, 200
        run_id = path.rsplit("/", 1)[1]
        state["detail_calls"].append(run_id)
        if run_id not in state["runs"]:
            return {"error": "Run not found"}, 404
        return state["runs"][run_id], 200

    monkeypatch.setattr(grid_data, "coordinator_request", request)
    monkeypatch.setattr(grid_data, "load_grid_config", lambda: {"coordinator_url": "http://c:8000", "api_token": ""})
    state["add"] = lambda r: state["runs"].__setitem__(r["run_id"], r)
    return state


def local_record(ts, report=None, repo="shop", browser="chromium", passed=2):
    return {"ts": ts, "repo": repo, "status": "passed", "browser": browser, "environment": "local",
            "stats": {"passed": passed, "failed": 0, "broken": 0, "skipped": 0, "total": passed},
            "report_path": report or "",
            "tests": [{"suite": "s", "name": f"n{i}", "method": f"m{i}", "status": "passed", "uid": f"uid{i}",
                       "duration_s": 1.0, "report_path": ""} for i in range(passed)]}


def seed_history(user_id, *records):
    save_history(list(records), user_id=user_id)


# ── overview ──────────────────────────────────────────────────────────────────

def test_overview_needs_a_login(app):
    assert app.test_client().get("/api/dashboard/overview").status_code == 401


def test_overview_shows_grid_and_local_runs_together(admin, coordinator):
    coordinator["add"](grid_run())
    seed_history(1, local_record(NOW - 2 * HOUR))
    out = admin.get("/api/dashboard/overview").get_json()
    assert {r["source"] for r in out["runs"]} == {"grid", "local"}
    assert out["kpis"]["runs"] == 2 and out["kpis"]["total"] == 4 and out["kpis"]["failed"] == 1
    assert out["sources"]["grid"]["reachable"] is True and out["sources"]["local"]["runs"] == 1
    assert out["facets"]["environments"] == ["local", "qa"] and out["facets"]["pods"] == ["pw-edge-1", "pw-firefox-1"]


def test_filters_reach_the_logic(admin, coordinator):
    coordinator["add"](grid_run("run-qa", env="qa"))
    coordinator["add"](grid_run("run-prod", env="prod"))
    by = lambda **q: admin.get("/api/dashboard/overview", query_string=q).get_json()  # noqa: E731
    assert [r["id"] for r in by(env="prod")["runs"]] == ["run-prod"]
    assert by(browser="firefox")["kpis"]["failed"] == 2 and by(browser="edge")["kpis"]["failed"] == 0
    assert {r["id"] for r in by(pod="pw-edge-1")["runs"]} == {"run-qa", "run-prod"}
    assert by(pod="pw-edge-1")["kpis"]["total"] == 2


def test_the_time_frame_filter(admin, coordinator):
    coordinator["add"](grid_run("recent", ago=HOUR))
    coordinator["add"](grid_run("old", ago=40 * 24 * HOUR))
    assert [r["id"] for r in admin.get("/api/dashboard/overview?days=7").get_json()["runs"]] == ["recent"]
    assert {r["id"] for r in admin.get("/api/dashboard/overview?days=90").get_json()["runs"]} == {"recent", "old"}
    day = datetime.fromtimestamp((NOW - 40 * 24 * HOUR) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    got = admin.get("/api/dashboard/overview", query_string={"from": day, "to": day}).get_json()
    assert [r["id"] for r in got["runs"]] == ["old"]


@pytest.mark.parametrize("query", ["source=cloud", "days=0", "days=abc", "from=bad", "tz=100000",
                                   "from=2026-11-21&to=2026-11-20"])
def test_bad_filters_are_a_400_with_a_reason(admin, coordinator, query):
    r = admin.get(f"/api/dashboard/overview?{query}")
    assert r.status_code == 400 and r.get_json()["error"]


def test_the_browsers_zone_name_is_honoured(admin, coordinator):
    q = {"from": "2026-07-01", "to": "2026-07-01", "tz": "300", "tzname": "America/New_York"}
    assert admin.get("/api/dashboard/overview", query_string=q).status_code == 200
    assert admin.get("/api/dashboard/overview", query_string={**q, "tzname": "../x"}).status_code == 400


def test_the_overview_reports_when_it_could_not_include_every_grid_run(admin, coordinator, monkeypatch):
    from amplify import consolidated
    monkeypatch.setattr(consolidated, "MAX_GRID_RUNS", 1)
    coordinator["add"](grid_run("a", ago=HOUR)), coordinator["add"](grid_run("b", ago=2 * HOUR))
    out = admin.get("/api/dashboard/overview").get_json()
    assert out["grid_runs_matched"] == 2 and out["grid_runs_capped"] == 1 and out["kpis"]["runs"] == 1


def test_a_local_only_view_does_not_call_the_coordinator(admin, coordinator):
    seed_history(1, local_record(NOW - HOUR))
    out = admin.get("/api/dashboard/overview?source=local").get_json()
    assert coordinator["list_calls"] == 0 and coordinator["detail_calls"] == []
    assert [r["source"] for r in out["runs"]] == ["local"]


def test_when_the_grid_is_down_local_runs_still_show_and_the_reason_is_given(admin, coordinator):
    coordinator["down"] = "connection refused"
    seed_history(1, local_record(NOW - HOUR))
    out = admin.get("/api/dashboard/overview").get_json()
    assert out["sources"]["grid"] == {"configured": True, "reachable": False, "error": "Coordinator unreachable: connection refused"}
    assert [r["source"] for r in out["runs"]] == ["local"]


def test_when_no_grid_is_configured_it_says_so_without_an_error(admin, monkeypatch):
    monkeypatch.setattr(grid_data, "coordinator_request",
                        lambda *a, **k: ({"error": "not configured", "configured": False}, 503))
    out = admin.get("/api/dashboard/overview").get_json()
    assert out["sources"]["grid"] == {"configured": False, "reachable": False, "error": None}


def test_each_user_sees_only_their_own_local_runs(admin, user, coordinator):
    seed_history(1, local_record(NOW - HOUR, repo="admins-repo"))
    seed_history(2, local_record(NOW - 2 * HOUR, repo="users-repo"))
    mine = admin.get("/api/dashboard/overview?source=local").get_json()["runs"]
    theirs = user.get("/api/dashboard/overview?source=local").get_json()["runs"]
    assert [r["repo"] for r in mine] == ["admins-repo"] and [r["repo"] for r in theirs] == ["users-repo"]


def test_finished_runs_are_read_from_the_coordinator_once(admin, coordinator, monkeypatch):
    monkeypatch.setattr(grid_data, "LIVE_TTL_SECONDS", 0)       # so only a settled run (all Allure results in) skips re-reading
    finished = grid_run()
    for c in finished["chunk_details"]:
        c["has_allure"] = True
    coordinator["add"](finished)
    for _ in range(3):
        admin.get("/api/dashboard/overview")
    assert coordinator["detail_calls"] == ["run-1"] and coordinator["list_calls"] == 3


def test_a_run_still_in_flight_is_read_again(admin, coordinator, monkeypatch):
    live = grid_run(chunks=[chunk("edge", "p", status="running")])
    live["status"], live["chunks"] = "running", {"total": 1, "pending": 0, "running": 1, "completed": 0}
    coordinator["add"](live)
    monkeypatch.setattr(grid_data, "LIVE_TTL_SECONDS", 0)
    admin.get("/api/dashboard/overview"), admin.get("/api/dashboard/overview")
    assert coordinator["detail_calls"] == ["run-1", "run-1"]
    assert admin.get("/api/dashboard/overview").get_json()["runs"][0]["status"] == "running"


def test_the_cache_does_not_mix_up_two_coordinators(admin, coordinator, monkeypatch):
    coordinator["add"](grid_run())
    admin.get("/api/dashboard/overview")
    monkeypatch.setattr(grid_data, "load_grid_config", lambda: {"coordinator_url": "http://other:8000", "api_token": ""})
    admin.get("/api/dashboard/overview")
    assert coordinator["detail_calls"] == ["run-1", "run-1"]


# ── drill-down ────────────────────────────────────────────────────────────────

def test_grid_run_detail_has_chunks_pods_and_tests(admin, coordinator):
    coordinator["add"](grid_run())
    d = admin.get("/api/dashboard/run/grid/run-1").get_json()
    assert d["run"]["environment"] == "qa" and d["run"]["status"] == "failed"
    ff = next(k for k in d["chunks"] if k["browser"] == "firefox")
    assert ff["pod"] == "pw-firefox-1" and ff["has_log"] and ff["tests"][0]["outcome"] == "failed"


def test_drill_down_follows_the_active_browser_filter(admin, coordinator):
    coordinator["add"](grid_run())
    d = admin.get("/api/dashboard/run/grid/run-1?browser=edge").get_json()
    assert [k["browser"] for k in d["chunks"]] == ["edge"] and d["hidden_chunks"] == 1 and d["run"]["status"] == "passed"


def test_local_run_detail(admin, coordinator):
    seed_history(1, local_record(1_700_000_000_000, report="/tmp/nowhere/index.html"))
    d = admin.get("/api/dashboard/run/local/local-1700000000000").get_json()
    assert d["run"]["has_report"] is True and len(d["chunks"][0]["tests"]) == 2 and d["chunks"][0]["pod"] == ""


def test_detail_failures(admin, user, coordinator):
    seed_history(1, local_record(1_700_000_000_000))
    assert admin.get("/api/dashboard/run/grid/nope").status_code == 404
    assert admin.get("/api/dashboard/run/local/local-1").status_code == 404
    assert admin.get("/api/dashboard/run/other/x").status_code == 404
    assert admin.get("/api/dashboard/run/grid/..%2Fconfig").status_code == 404
    assert user.get("/api/dashboard/run/local/local-1700000000000").status_code == 404     # not their run
    coordinator["down"] = "refused"
    assert admin.get("/api/dashboard/run/grid/run-1").status_code == 502


def test_detail_needs_a_login(app):
    assert app.test_client().get("/api/dashboard/run/grid/x").status_code == 401


# ── Grid runs and their Allure reports ────────────────────────────────────────

def with_allure(run, *flags, repo=None, sha=None):
    """The coordinator's run payload, with has_allure set on its chunks (in order) and the run's repo / sha."""
    for c, flag in zip(run["chunk_details"], flags):
        c["has_allure"] = flag
    if repo:
        run["repo"], run["sha"] = repo, sha
    return run


def test_a_grid_run_has_a_report_when_any_chunk_uploaded_allure_results(admin, coordinator):
    coordinator["add"](with_allure(grid_run("some"), True, False))
    coordinator["add"](with_allure(grid_run("none"), False, False))
    coordinator["add"](grid_run("old-coordinator"))                       # an older coordinator says nothing about it
    rows = {r["id"]: r for r in admin.get("/api/dashboard/overview").get_json()["runs"]}
    assert rows["some"]["has_report"] is True
    assert rows["none"]["has_report"] is False and rows["old-coordinator"]["has_report"] is False


def test_the_run_summary_names_the_repo_and_commit_of_a_grid_run(admin, coordinator):
    sha = "0123456789abcdef0123456789abcdef01234567"
    coordinator["add"](with_allure(grid_run("from-repo"), True, True, repo="shop-tests", sha=sha))
    coordinator["add"](grid_run("baked"))
    seed_history(1, local_record(NOW - 3 * HOUR))
    rows = {r["id"]: r for r in admin.get("/api/dashboard/overview").get_json()["runs"]}
    assert (rows["from-repo"]["repo"], rows["from-repo"]["sha"]) == ("shop-tests", sha)
    assert (rows["baked"]["repo"], rows["baked"]["sha"]) == ("", "")
    local = next(r for r in rows.values() if r["source"] == "local")
    assert local["repo"] == "shop" and local["sha"] == ""                 # local runs keep their own repo label


def test_the_drill_down_of_a_grid_run_carries_report_repo_and_sha(admin, coordinator):
    coordinator["add"](with_allure(grid_run(), False, True, repo="shop-tests", sha="a" * 40))
    run = admin.get("/api/dashboard/run/grid/run-1").get_json()["run"]
    assert run["has_report"] is True and run["repo"] == "shop-tests" and run["sha"] == "a" * 40
    only_first = admin.get("/api/dashboard/run/grid/run-1?browser=edge").get_json()["run"]
    assert only_first["has_report"] is True                               # the report covers the whole run, whatever the filter


def test_local_report_availability_is_unchanged(admin, coordinator):
    seed_history(1, local_record(NOW - HOUR, report="/tmp/x/index.html"), local_record(NOW - 2 * HOUR))
    rows = admin.get("/api/dashboard/overview?source=local").get_json()["runs"]
    assert [r["has_report"] for r in rows] == [True, False]


# ── viewing HTML reports in the app ───────────────────────────────────────────

@pytest.fixture
def report(tmp_path):
    """An Allure-style report folder: index.html + data/, next to files that must stay out of reach."""
    root = tmp_path / "repo" / "allure" / "report"
    (root / "data").mkdir(parents=True)
    (root / "index.html").write_text("<html><body>RUN REPORT</body></html>")
    (root / "data" / "summary.json").write_text('{"ok": true}')
    (root / "uid0.html").write_text("<html>TEST REPORT 0</html>")
    (tmp_path / "secret.json").write_text('{"secret": "outside"}')
    (tmp_path / "repo" / "notes.py").write_text("print('no')")
    return root


def link_for(client, run_id, **extra):
    return client.post("/api/dashboard/report-link", json={"run_id": run_id, **extra})


def test_a_run_report_opens_inside_the_app(admin, report):
    seed_history(1, local_record(1_700_000_000_000, report=str(report / "index.html")))
    r = link_for(admin, "local-1700000000000")
    assert r.status_code == 200
    url = r.get_json()["url"]
    assert url.startswith("/rv/") and url.endswith("/index.html")
    page = admin.get(url)
    assert page.status_code == 200 and b"RUN REPORT" in page.data


def test_the_report_can_load_its_own_data_files(admin, report):
    seed_history(1, local_record(1_700_000_000_000, report=str(report / "index.html")))
    base = link_for(admin, "local-1700000000000").get_json()["url"].rsplit("/", 1)[0]
    assert admin.get(f"{base}/data/summary.json").get_json() == {"ok": True}
    assert admin.get(f"{base}/").status_code == 200                       # the bare folder serves the entry page


def test_a_single_tests_report_and_its_anchor(admin, report):
    rec = local_record(1_700_000_000_000, report=str(report / "index.html"))
    rec["tests"][0]["report_path"] = f"{report / 'index.html'}#/test/uid0"
    rec["tests"][1]["report_path"] = str(report / "uid0.html")
    seed_history(1, rec)
    anchored = link_for(admin, "local-1700000000000", uid="uid0").get_json()
    assert anchored["url"].endswith("/index.html#/test/uid0")
    single = link_for(admin, "local-1700000000000", uid="uid1").get_json()
    assert single["url"].endswith("/uid0.html") and b"TEST REPORT 0" in admin.get(single["url"]).data


def test_a_test_without_its_own_report_falls_back_to_the_run_report(admin, report):
    seed_history(1, local_record(1_700_000_000_000, report=str(report / "index.html")))
    assert link_for(admin, "local-1700000000000", uid="uid0").get_json()["url"].endswith("/index.html")


def test_link_requests_that_cannot_be_honoured(admin, user, app, report):
    seed_history(1, local_record(1_700_000_000_000, report=str(report / "index.html")),
                 local_record(1_700_000_000_001, report=str(report / "gone.html")),
                 local_record(1_700_000_000_002, report=""))
    assert link_for(admin, "local-999").status_code == 404                              # no such run
    assert link_for(admin, "local-1700000000000", uid="nope").status_code == 404        # no such test
    gone = link_for(admin, "local-1700000000001")
    assert gone.status_code == 404 and "no longer on disk" in gone.get_json()["error"]
    assert link_for(admin, "local-1700000000002").status_code == 404                    # run has no report
    assert link_for(user, "local-1700000000000").status_code == 404                     # another user's run
    assert link_for(app.test_client(), "local-1700000000000").status_code == 401


@pytest.mark.parametrize("body", [[1], "str", 5, True])
def test_a_report_link_request_that_is_not_a_json_object_is_a_400_not_a_500(admin, body):
    assert admin.post("/api/dashboard/report-link", json=body).status_code == 400


def test_a_report_link_request_without_a_body_is_just_not_found(admin):
    assert admin.post("/api/dashboard/report-link").status_code == 404


def test_the_browser_cannot_ask_for_a_file_by_path(admin, report, tmp_path):
    seed_history(1, local_record(1_700_000_000_000, report=str(report / "index.html")))
    r = admin.post("/api/dashboard/report-link", json={"run_id": "local-1700000000000", "path": str(tmp_path / "secret.json"),
                                                       "report_path": "/etc/hosts"})
    assert r.status_code == 200 and r.get_json()["url"].endswith("/index.html")        # extra fields are ignored


def test_a_history_entry_pointing_at_a_non_html_file_gets_no_link(admin, tmp_path):
    secret = tmp_path / "secret.json"
    secret.write_text("{}")
    seed_history(1, local_record(1_700_000_000_000, report=str(secret)), local_record(1_700_000_000_001, report="/etc/hosts"))
    assert link_for(admin, "local-1700000000000").status_code == 404
    assert link_for(admin, "local-1700000000001").status_code == 404


def test_viewer_blocks_traversal_and_unsafe_files(admin, report, tmp_path):
    seed_history(1, local_record(1_700_000_000_000, report=str(report / "index.html")))
    base = link_for(admin, "local-1700000000000").get_json()["url"].rsplit("/", 1)[0]
    assert admin.get(f"{base}/../../../secret.json").status_code == 404
    assert admin.get(f"{base}/%2e%2e/%2e%2e/%2e%2e/secret.json").status_code == 404
    assert admin.get(f"{base}/..%2f..%2f..%2fsecret.json").status_code == 404
    assert admin.get(f"{base}/%2Fetc%2Fhosts", follow_redirects=True).status_code == 404     # an absolute path
    assert admin.get(f"{base}/nothing.html").status_code == 404
    (report / "run.py").write_text("print(1)")
    assert admin.get(f"{base}/run.py").status_code == 404                              # not a web asset


def test_viewer_does_not_follow_a_symlink_out_of_the_report(admin, report, tmp_path):
    os.symlink(tmp_path / "secret.json", report / "data" / "leak.json")
    os.symlink(tmp_path / "repo", report / "linked")
    seed_history(1, local_record(1_700_000_000_000, report=str(report / "index.html")))
    base = link_for(admin, "local-1700000000000").get_json()["url"].rsplit("/", 1)[0]
    assert admin.get(f"{base}/data/leak.json").status_code == 404
    assert admin.get(f"{base}/linked/notes.py").status_code == 404


def test_someone_elses_link_is_not_found(admin, user, report):
    seed_history(1, local_record(1_700_000_000_000, report=str(report / "index.html")))
    url = link_for(admin, "local-1700000000000").get_json()["url"]
    assert user.get(url).status_code == 404


def test_the_viewer_needs_a_login(admin, app, report):
    seed_history(1, local_record(1_700_000_000_000, report=str(report / "index.html")))
    url = link_for(admin, "local-1700000000000").get_json()["url"]
    assert app.test_client().get(url).status_code in (302, 401)


def _forge(**overrides):
    claims = {"uid": 1, "dir": str(overrides.pop("dir", "/")), "entry": "index.html", "aud": report_viewer.AUDIENCE,
              "iat": int(time.time()), "exp": int(time.time()) + 600, **overrides}
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, overrides.get("key", settings.JWT_SECRET_KEY), algorithm="HS256")


def test_forged_expired_and_wrong_kind_tokens_are_refused(admin, report):
    good_dir = str(report)
    session = generate_jwt(1)
    for label, token in [
        ("another key", jwt.encode({"uid": 1, "dir": good_dir, "entry": "index.html", "aud": report_viewer.AUDIENCE,
                                    "exp": int(time.time()) + 600}, "x" * 32, algorithm="HS256")),
        ("expired", _forge(dir=good_dir, exp=int(time.time()) - 60)),
        ("wrong audience", _forge(dir=good_dir, aud="amplify-insights")),
        ("no audience", _forge(dir=good_dir, aud=None)),
        ("a session token", session),
        ("garbage", "not-a-token"),
    ]:
        assert admin.get(f"/rv/{token}/index.html").status_code == 404, label


def test_a_valid_token_for_the_wrong_user_is_refused(admin, user, report):
    token = report_viewer.mint(1, report / "index.html")
    assert admin.get(f"/rv/{token}/index.html").status_code == 200
    assert user.get(f"/rv/{token}/index.html").status_code == 404


def test_session_and_sign_in_tokens_do_not_open_reports(admin):
    from amplify.sso import issue_insights_token
    assert report_viewer.verify(generate_jwt(1)) is None
    assert report_viewer.verify(issue_insights_token(1, "boss")) is None


def test_report_responses_are_not_cached_or_framable_by_other_sites(admin, report):
    seed_history(1, local_record(1_700_000_000_000, report=str(report / "index.html")))
    page = admin.get(link_for(admin, "local-1700000000000").get_json()["url"])
    assert page.headers["Cache-Control"] == "no-store" and page.headers["X-Content-Type-Options"] == "nosniff"
    assert page.headers["Content-Security-Policy"] == "frame-ancestors 'self'" and page.headers["Referrer-Policy"] == "no-referrer"
