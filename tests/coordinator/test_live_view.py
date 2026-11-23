"""Live view registration: a pod says it serves its display; Amplify (full token) asks where and with what password.

The address is the one the registration came from and the port is fixed, so test code holding the pod token cannot
point Amplify at another host or port, nor take over another live pod's view.
"""
import json
from datetime import timedelta

import pytest

from coordinator import live_view
from coordinator import schemas
from coordinator.db import RunnerRecord, _utcnow
from pod_scope_helpers import FULL_TOKEN, POD_TOKEN, call

POD_IP, OTHER_IP = ("10.128.4.17", 40000), ("10.128.9.3", 40000)
PASSWORD = "Zq3_x-9Kp2"


@pytest.fixture(autouse=True)
def tokens(monkeypatch):
    monkeypatch.setattr(schemas, "API_TOKEN", FULL_TOKEN)
    monkeypatch.setattr(schemas, "POD_API_TOKEN", POD_TOKEN)


def register(grid, runner="pw-chromium-1", password=PASSWORD, client=POD_IP, token=POD_TOKEN):
    body = json.dumps({"password": password}).encode()
    return call(grid.app, "POST", f"/api/runners/{runner}/view", token=token, body=body, client=client,
                extra_headers={"Content-Type": "application/json"})


def view(grid, runner="pw-chromium-1", token=FULL_TOKEN):
    return call(grid.app, "GET", f"/api/runners/{runner}/view", token=token)


def test_a_registered_pod_can_be_watched_at_the_address_it_registered_from(grid):
    assert register(grid) == (200, {"status": "ok", "port": 5900})

    status, body = view(grid)

    assert status == 200
    assert (body["host"], body["port"], body["password"], body["status"]) == ("10.128.4.17", 5900, PASSWORD, "idle")


def test_the_port_and_host_come_from_the_coordinator_never_from_the_pod(grid):
    body = json.dumps({"password": PASSWORD, "host": "169.254.169.254", "port": 80}).encode()
    call(grid.app, "POST", "/api/runners/pw-chromium-1/view", token=POD_TOKEN, body=body, client=POD_IP,
         extra_headers={"Content-Type": "application/json"})

    _, got = view(grid)

    assert (got["host"], got["port"]) == ("10.128.4.17", 5900)


def test_the_pod_token_cannot_read_a_live_view(grid):
    register(grid)

    assert view(grid, token=POD_TOKEN)[0] == 403
    assert view(grid, token=None)[0] == 401


def test_another_pod_cannot_take_over_a_live_pods_view(grid):
    register(grid)

    status, _ = register(grid, password="attacker-pw", client=OTHER_IP)

    assert status == 409
    assert view(grid)[1]["host"] == "10.128.4.17" and view(grid)[1]["password"] == PASSWORD


def test_the_same_pod_can_register_again_with_a_new_password(grid):
    register(grid)
    assert register(grid, password="second-pw-1")[0] == 200
    assert view(grid)[1]["password"] == "second-pw-1"


@pytest.mark.parametrize("password", ["short", "x" * 65, "has space1", "semi;colon1", "", "ünïcode-pw"])
def test_a_password_that_is_not_a_plain_token_is_refused(grid, password):
    assert register(grid, password=password)[0] == 422


@pytest.mark.parametrize("client", [("test", 1), ("localhost", 1), ("", 1)])
def test_a_registration_from_no_ip_address_is_refused(grid, client):
    assert register(grid, client=client)[0] == 400


def test_a_runner_id_that_is_not_a_plain_name_is_refused(grid):
    assert register(grid, runner=".hidden")[0] == 400
    assert view(grid, runner=".hidden")[0] == 404


def test_an_exited_pod_has_no_live_view_and_its_password_is_forgotten(grid):
    register(grid)
    assert call(grid.app, "POST", "/api/runners/pw-chromium-1/exit", token=POD_TOKEN)[0] == 200

    assert view(grid)[0] == 404
    with grid.sessions() as db:
        runner = db.get(RunnerRecord, "pw-chromium-1")
        assert (runner.view_host, runner.view_secret) == (None, None)


def test_a_pod_that_stopped_heartbeating_has_no_live_view_and_can_be_replaced(grid):
    register(grid)
    with grid.sessions() as db:
        db.get(RunnerRecord, "pw-chromium-1").last_seen = _utcnow() - timedelta(hours=1)
        db.commit()

    assert view(grid)[0] == 404
    assert register(grid, client=OTHER_IP)[0] == 200          # the stale registration does not block a new one


def test_a_pod_without_live_view_is_404(grid):
    assert call(grid.app, "POST", "/api/runners/pw-chromium-2/heartbeat", token=POD_TOKEN)[0] == 200
    assert view(grid, runner="pw-chromium-2")[0] == 404
    assert view(grid, runner="nobody")[0] == 404


def test_the_pod_list_says_which_pods_can_be_watched_and_never_carries_the_password(grid):
    register(grid)
    call(grid.app, "POST", "/api/runners/pw-chromium-2/heartbeat", token=POD_TOKEN)

    status, body = call(grid.app, "GET", "/api/pods", token=FULL_TOKEN)

    assert status == 200
    assert {p["name"]: p["live_view"] for p in body["pods"]} == {"pw-chromium-1": True, "pw-chromium-2": False}
    assert PASSWORD not in json.dumps(body) and "10.128.4.17" not in json.dumps(body)


def test_a_busy_pod_reports_its_run_and_chunk(grid):
    register(grid)
    with grid.sessions() as db:
        r = db.get(RunnerRecord, "pw-chromium-1")
        r.status, r.run_id, r.current_chunk = "busy", "run-1", "run-1-chromium-001"
        db.commit()

    body = view(grid)[1]

    assert (body["status"], body["run_id"], body["chunk_id"]) == ("busy", "run-1", "run-1-chromium-001")


def test_the_feature_is_announced():
    from coordinator import config
    assert live_view.FEATURE in config.FEATURES
