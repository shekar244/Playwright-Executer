"""Cancelling a Grid run from Amplify (POST /api/grid/runs/<id>/cancel) and how a cancelled run shows up.

Who may cancel (the person who started the run, or an admin), what is passed to the coordinator, and that a cancelled run
is final everywhere Amplify looks at runs: the dashboard's status, its tests' outcome and the cache of finished runs.
"""
import json
import urllib.request
from types import SimpleNamespace

import pytest
from flask import Flask

from amplify_server import auth_utils, consolidated, grid_data
from amplify_server.routes import grid

RUN = "run-20261121-034254-ba2ea2"


def detail(statuses, requested_by="shekar", status=None):
    chunks = [{"chunk_id": f"{RUN}-edge-{i:03d}", "browser": "edge", "status": s, "tests": [f"tests/test_{i}.py::t"],
               "assigned_runner": "pw-1" if s != "pending" else None, "passed": 1 if s == "completed" else 0, "failed": 0, "skipped": 0,
               "updated_at": "2026-11-21T03:42:55+00:00", "results": None, "has_allure": False} for i, s in enumerate(statuses)]
    return {"run_id": RUN, "created_at": "2026-11-21T03:42:54+00:00", "environment": "dev", "requested_by": requested_by,
            "status": status or "queued", "chunks": {"total": len(chunks), "pending": statuses.count("pending"),
                                                      "running": statuses.count("running"), "completed": statuses.count("completed"),
                                                      "cancelled": statuses.count("cancelled")}, "chunk_details": chunks}


class Coordinator:
    """Stands in for the coordinator: records every call and answers from a table."""

    def __init__(self, run=None, run_status=200, cancel=None, cancel_status=200):
        self.calls, self.run, self.run_status = [], run if run is not None else detail(["pending"]), run_status
        self.cancel, self.cancel_status = cancel if cancel is not None else {"cancelled_chunks": 1, "status": "cancelled"}, cancel_status

    def __call__(self, method, path, params=None, body=None, timeout=None):
        self.calls.append((method, path))
        if method == "POST":
            return self.cancel, self.cancel_status
        return self.run, self.run_status


@pytest.fixture
def make_client(monkeypatch, data_dir):
    def make(username="shekar", is_admin=False, coordinator=None):
        monkeypatch.setattr(auth_utils, "verify_jwt", lambda token: 1)
        monkeypatch.setattr(auth_utils.User, "get_by_id", staticmethod(lambda uid: SimpleNamespace(
            id=1, username=username, is_active=True, is_admin=is_admin, must_change_password=False)))
        monkeypatch.setenv("GRID_COORDINATOR_URL", "http://coordinator:8000")
        monkeypatch.setenv("GRID_API_TOKEN", "tok")
        fake = coordinator or Coordinator()
        monkeypatch.setattr(grid, "coordinator_request", fake)
        app = Flask(__name__)
        app.register_blueprint(grid.bp)
        client = app.test_client()
        client.set_cookie("access_token", "x")
        return client, fake
    return make


@pytest.fixture
def cache_cleared(monkeypatch):
    cleared = []
    monkeypatch.setattr(grid_data, "clear_cache", lambda: cleared.append(1))
    return cleared


def post(client, run=RUN):
    return client.post(f"/api/grid/runs/{run}/cancel")


# ── who may cancel ────────────────────────────────────────────────────────────

def test_the_person_who_started_the_run_can_cancel_it(make_client, cache_cleared):
    client, coordinator = make_client("shekar")

    resp = post(client)

    assert resp.status_code == 200 and resp.get_json() == {"cancelled_chunks": 1, "status": "cancelled"}
    assert coordinator.calls == [("GET", f"/api/runs/{RUN}"), ("POST", f"/api/runs/{RUN}/cancel")]
    assert cache_cleared == [1]                       # the dashboard must not keep showing it as queued


def test_an_admin_can_cancel_anyones_run(make_client):
    client, coordinator = make_client("boss", is_admin=True, coordinator=Coordinator(run=detail(["pending"], requested_by="someone-else")))

    assert post(client).status_code == 200 and coordinator.calls[-1][0] == "POST"


@pytest.mark.parametrize("owner", ["someone-else", "Shekar", "shekar ", "", None, 7])
def test_anyone_else_is_refused_and_the_coordinator_is_never_asked_to_cancel(make_client, cache_cleared, owner):
    client, coordinator = make_client("shekar", coordinator=Coordinator(run=detail(["pending"], requested_by=owner)))

    resp = post(client)

    assert resp.status_code == 403 and "started" in resp.get_json()["error"]
    assert [m for m, _ in coordinator.calls] == ["GET"] and cache_cleared == []


def test_a_run_nobody_is_recorded_as_starting_can_only_be_cancelled_by_an_admin(make_client):
    run = detail(["pending"], requested_by=None)
    assert post(make_client("shekar", coordinator=Coordinator(run=run))[0]).status_code == 403
    assert post(make_client("boss", is_admin=True, coordinator=Coordinator(run=run))[0]).status_code == 200


def test_it_needs_a_signed_in_user():
    app = Flask(__name__)
    app.register_blueprint(grid.bp)

    assert app.test_client().post(f"/api/grid/runs/{RUN}/cancel").status_code == 401


# ── errors ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", ["bad id!", "a b", "x;y", "run$(x)", "%2e%2e", "ru'n"])
def test_a_malformed_run_id_is_a_400_and_never_reaches_the_coordinator(make_client, bad):
    client, coordinator = make_client("shekar")

    assert post(client, bad).status_code in (400, 404) and coordinator.calls == []


def test_an_unknown_run_is_passed_on_as_404_without_a_cancel_call(make_client):
    client, coordinator = make_client(coordinator=Coordinator(run={"error": "Run not found"}, run_status=404))

    resp = post(client)

    assert resp.status_code == 404 and resp.get_json() == {"error": "Run not found"} and [m for m, _ in coordinator.calls] == ["GET"]


def test_an_unreachable_coordinator_is_a_502_and_nothing_is_cleared(make_client, cache_cleared):
    client, _ = make_client(coordinator=Coordinator(run={"error": "Coordinator unreachable: boom"}, run_status=502))

    assert post(client).status_code == 502 and cache_cleared == []


def test_a_failed_cancel_is_passed_on_and_does_not_clear_the_cache(make_client, cache_cleared):
    client, _ = make_client(coordinator=Coordinator(cancel={"error": "Coordinator unreachable: boom"}, cancel_status=502))

    resp = post(client)

    assert resp.status_code == 502 and "unreachable" in resp.get_json()["error"] and cache_cleared == []


def test_the_cancel_really_goes_out_as_a_post_with_the_token_in_the_header_only(monkeypatch, data_dir):
    monkeypatch.setenv("GRID_COORDINATOR_URL", "http://coordinator:8000")
    monkeypatch.setenv("GRID_API_TOKEN", "tok-secret")
    seen = []

    class Resp:
        status = 200

        def read(self):
            return b'{"cancelled_chunks": 2}'

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: seen.append(req) or Resp())

    payload, status = grid.coordinator_request("POST", f"/api/runs/{RUN}/cancel")

    req = seen[0]
    assert (status, payload) == (200, {"cancelled_chunks": 2}) and req.get_method() == "POST"
    assert req.full_url == f"http://coordinator:8000/api/runs/{RUN}/cancel" and "tok-secret" not in req.full_url
    assert req.get_header("X-api-token") == "tok-secret"


# ── a cancelled run is final everywhere Amplify looks ─────────────────────────

def run_of(statuses):
    return consolidated.normalize_grid_run(detail(statuses))


@pytest.mark.parametrize("statuses, expected", [
    (["cancelled"], "cancelled"),
    (["completed", "cancelled"], "cancelled"),            # partly done, then cancelled: still a cancelled run
    (["cancelled", "cancelled"], "cancelled"),
    (["pending"], "queued"),
    (["running", "cancelled"], "running"),                # defensive: the coordinator leaves none, but running wins
    (["completed", "completed"], "passed"),
])
def test_the_dashboard_classifies_a_cancelled_run(statuses, expected):
    assert consolidated.run_status(run_of(statuses)) == expected


def test_the_tests_of_a_cancelled_chunk_show_cancelled_not_pending():
    run = run_of(["cancelled", "pending"])

    assert [t["outcome"] for t in run["chunks"][0]["tests"]] == ["cancelled"]
    assert [t["outcome"] for t in run["chunks"][1]["tests"]] == [None]        # still waiting
    assert consolidated.chunk_state(run, run["chunks"][0]) == "cancelled"
    assert run["chunks"][0]["has_log"] is False


def test_cancelled_runs_do_not_count_as_running_or_failed_in_the_kpis():
    runs = [run_of(["cancelled"]), run_of(["completed"])]

    kpis = consolidated.kpis(runs)

    assert kpis["runs"] == 2 and kpis["running"] == 0 and kpis["failed_runs"] == 0


def test_a_cancelled_run_is_finished_and_settled_at_once_so_it_is_cached():
    cancelled = detail(["cancelled", "cancelled"], status="cancelled")

    assert grid_data._finished(cancelled) and grid_data._settled(cancelled)


def test_a_cancelled_run_with_finished_chunks_waits_only_for_their_allure_results():
    partly = detail(["completed", "cancelled"], status="cancelled")

    assert grid_data._finished(partly) and not grid_data._settled(partly, now=1.8e9 - 10_000_000)      # inside the grace period
    partly["chunk_details"][0]["has_allure"] = True
    assert grid_data._settled(partly)


def test_a_queued_run_is_not_finished():
    assert not grid_data._finished(detail(["pending"], status="queued"))
