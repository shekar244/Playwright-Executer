"""Watching a runner pod's browser live from the Grid tab (routes/grid_live.py).

Who may watch (the person who started the run the pod executes, or an admin), that Amplify only ever connects to the
pod address the coordinator recorded and on the VNC port, that the WebSocket refuses other sites, and that the bytes
really are relayed both ways (a real server, a real WebSocket client and a stand-in VNC server).
"""
import socket
import threading
from types import SimpleNamespace

import pytest
from flask import Flask

from amplify import auth_utils
from amplify.routes import grid, grid_live

POD, RUN = "pw-chromium-7ab2cf", "run-20261121-143840-b684b8"
VIEW = {"runner_id": POD, "host": "10.128.4.17", "port": 5900, "password": "Zq3_x-9K", "status": "busy",
        "browser": "chromium", "run_id": RUN, "chunk_id": f"{RUN}-chromium-001"}


class Coordinator:
    def __init__(self, view=None, view_status=200, owner="shekar", run_status=200):
        self.view, self.view_status, self.owner, self.run_status, self.calls = \
            dict(VIEW) if view is None else view, view_status, owner, run_status, []

    def __call__(self, method, path, params=None, body=None, timeout=None):
        self.calls.append((method, path))
        if path == f"/api/runners/{POD}/view":
            return (self.view, 200) if self.view_status == 200 else ({"error": "This pod has no live view"}, self.view_status)
        if path == f"/api/runs/{RUN}":
            return ({"run_id": RUN, "requested_by": self.owner}, self.run_status)
        return {"error": "unexpected"}, 500


@pytest.fixture
def make_client(monkeypatch):
    def make(username="shekar", is_admin=False, coordinator=None):
        monkeypatch.setattr(auth_utils, "verify_jwt", lambda token: 1)
        monkeypatch.setattr(auth_utils.User, "get_by_id", staticmethod(lambda uid: SimpleNamespace(
            id=1, username=username, is_active=True, is_admin=is_admin, must_change_password=False)))
        fake = coordinator or Coordinator()
        monkeypatch.setattr(grid, "coordinator_request", fake)
        app = Flask(__name__)
        app.register_blueprint(grid_live.bp)
        client = app.test_client()
        client.set_cookie("access_token", "x")
        return client, fake, app
    return make


WS_HEADERS = {"Upgrade": "websocket", "Connection": "Upgrade", "Sec-WebSocket-Version": "13",
              "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ=="}


def ws_get(client, origin="http://localhost", pod=POD):
    headers = dict(WS_HEADERS, **({"Origin": origin} if origin else {}))
    return client.get(f"/api/grid/pods/{pod}/vnc", headers=headers)


# ── who may watch ─────────────────────────────────────────────────────────────

def test_the_person_who_started_the_run_gets_the_password_and_nothing_is_cached(make_client):
    client, coordinator, _ = make_client("shekar")

    resp = client.get(f"/api/grid/pods/{POD}/view")

    assert resp.status_code == 200 and resp.headers["Cache-Control"] == "no-store"
    assert resp.get_json() == {"pod": POD, "password": "Zq3_x-9K", "ws_path": f"/api/grid/pods/{POD}/vnc",
                               "run_id": RUN, "chunk_id": f"{RUN}-chromium-001", "browser": "chromium"}
    assert "10.128.4.17" not in resp.get_data(as_text=True)          # the pod's address stays on the server
    assert coordinator.calls == [("GET", f"/api/runners/{POD}/view"), ("GET", f"/api/runs/{RUN}")]


def test_someone_else_cannot_watch_another_persons_run(make_client):
    client, _, _ = make_client("mallory")

    resp = client.get(f"/api/grid/pods/{POD}/view")

    assert resp.status_code == 403 and "password" not in resp.get_json()


def test_an_admin_can_watch_any_pod_even_between_runs(make_client):
    client, coordinator, _ = make_client("root", is_admin=True, coordinator=Coordinator(view=dict(VIEW, run_id=None)))

    assert client.get(f"/api/grid/pods/{POD}/view").status_code == 200
    assert coordinator.calls == [("GET", f"/api/runners/{POD}/view")]


def test_a_pod_between_runs_is_for_admins_only(make_client):
    client, _, _ = make_client("shekar", coordinator=Coordinator(view=dict(VIEW, run_id=None)))

    assert client.get(f"/api/grid/pods/{POD}/view").status_code == 403


@pytest.mark.parametrize("owner", [None, "", 7, "SHEKAR"])
def test_a_run_without_a_matching_owner_is_not_watchable_by_users(make_client, owner):
    client, _, _ = make_client("shekar", coordinator=Coordinator(owner=owner))

    assert client.get(f"/api/grid/pods/{POD}/view").status_code == 403


def test_a_pod_without_a_live_view_is_a_404_with_the_coordinators_reason(make_client):
    client, _, _ = make_client(coordinator=Coordinator(view_status=404))

    resp = client.get(f"/api/grid/pods/{POD}/view")

    assert resp.status_code == 404 and resp.get_json() == {"error": "This pod has no live view"}


@pytest.mark.parametrize("name", ["..", ".x", "a b", "x;y", "-p"])
def test_a_pod_name_that_is_not_a_plain_name_never_reaches_the_coordinator(make_client, name):
    client, coordinator, _ = make_client()

    assert client.get(f"/api/grid/pods/{name}/view").status_code in (400, 404)
    assert coordinator.calls == []


@pytest.mark.parametrize("view", [
    dict(VIEW, host="169.254.169.254"), dict(VIEW, host="0.0.0.0"), dict(VIEW, host="224.0.0.1"),
    dict(VIEW, host="evil.example.com"), dict(VIEW, host=None), dict(VIEW, port=22), dict(VIEW, port="5900"),
    dict(VIEW, password=None), "junk", [],
])
def test_an_address_amplify_will_not_connect_to_is_refused(make_client, view):
    client, _, _ = make_client(is_admin=True, coordinator=Coordinator(view=view))

    assert client.get(f"/api/grid/pods/{POD}/view").status_code == 502


def test_without_a_session_there_is_no_live_view(make_client):
    client, coordinator, _ = make_client()
    client.delete_cookie("access_token")

    assert client.get(f"/api/grid/pods/{POD}/view").status_code == 401
    assert ws_get(client).status_code == 401
    assert coordinator.calls == []


# ── the WebSocket, before the handshake ──────────────────────────────────────

def test_another_site_cannot_open_the_websocket_with_the_users_cookie(make_client):
    client, coordinator, _ = make_client()

    assert ws_get(client, origin="https://evil.example.com").status_code == 403
    assert coordinator.calls == []


def test_the_websocket_checks_who_may_watch_too(make_client):
    client, _, _ = make_client("mallory")

    assert ws_get(client).status_code == 403


def test_an_unreachable_pod_is_a_502_and_frees_the_viewer_slot(make_client, monkeypatch):
    client, _, _ = make_client()
    tried = []

    def refuse(address, timeout=None):
        tried.append(address)
        raise ConnectionRefusedError()

    monkeypatch.setattr(grid_live.socket, "create_connection", refuse)

    for _ in range(grid_live.MAX_VIEWERS + 1):
        assert ws_get(client).status_code == 502
    assert tried[0] == ("10.128.4.17", 5900)                 # the recorded address, the fixed port


def test_viewers_are_capped(make_client, monkeypatch):
    client, _, _ = make_client()
    monkeypatch.setattr(grid_live, "_viewers", threading.BoundedSemaphore(1))
    grid_live._viewers.acquire()

    resp = ws_get(client)

    assert resp.status_code == 503 and "at most" in resp.get_json()["error"]


def test_a_plain_request_to_the_websocket_route_is_not_served(make_client):
    client, _, _ = make_client()

    assert client.get(f"/api/grid/pods/{POD}/vnc").status_code == 400


# ── the relay ─────────────────────────────────────────────────────────────────

class FakeWs:
    def __init__(self, incoming):
        self.incoming, self.sent, self.connected = list(incoming), [], True

    def receive(self, timeout=None):
        if self.incoming:
            return self.incoming.pop(0)
        self.connected = False
        return None

    def send(self, data):
        self.sent.append(data)

    def close(self, *a, **k):
        self.connected = False


def test_relay_copies_both_ways_and_closes_both_ends():
    ours, pods = socket.socketpair()
    pods.sendall(b"RFB 003.008\n")
    ws = FakeWs([b"RFB 003.008\n", "text-frame"])

    grid_live.relay(ws, ours, max_seconds=5)

    assert pods.recv(64) == b"RFB 003.008\ntext-frame"
    assert ws.sent == [b"RFB 003.008\n"] and ws.connected is False
    assert pods.recv(1) == b""                                # Amplify's end was closed
    pods.close()


def test_relay_stops_at_the_time_limit():
    ours, pods = socket.socketpair()
    ws = FakeWs([])
    ws.receive = lambda timeout=None: None                    # a viewer that never says anything

    grid_live.relay(ws, ours, max_seconds=0.2)

    assert pods.recv(1) == b""
    pods.close()


def test_end_to_end_a_real_websocket_reaches_the_pods_vnc_server(make_client, monkeypatch):
    """A real HTTP server, a real WebSocket client and a stand-in VNC server: the RFB greeting comes through and the
    viewer's reply reaches the pod."""
    simple_websocket = pytest.importorskip("simple_websocket")
    from werkzeug.serving import make_server

    vnc = socket.socket()
    vnc.bind(("127.0.0.1", 0))
    vnc.listen(1)
    got = {}

    def fake_pod():
        conn, _ = vnc.accept()
        conn.sendall(b"RFB 003.008\n")
        got["reply"] = conn.recv(12)
        conn.close()

    threading.Thread(target=fake_pod, daemon=True).start()
    real_connect = socket.create_connection
    monkeypatch.setattr(grid_live.socket, "create_connection",
                        lambda address, timeout=None: real_connect(("127.0.0.1", vnc.getsockname()[1]), timeout=timeout))
    _, _, app = make_client("shekar")
    server = make_server("127.0.0.1", 0, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_port
    try:
        ws = simple_websocket.Client.connect(
            f"ws://127.0.0.1:{port}/api/grid/pods/{POD}/vnc", subprotocols=["binary"],
            # this client's Host header has no port (a browser's does), so neither does the Origin that matches it
            headers={"Cookie": "access_token=x", "Origin": "http://127.0.0.1"})
        assert ws.subprotocol == "binary"
        assert ws.receive(timeout=5) == b"RFB 003.008\n"
        ws.send(b"RFB 003.008\n")
        with pytest.raises(simple_websocket.ConnectionClosed):      # the pod hung up, so the viewer is disconnected
            for _ in range(50):
                ws.receive(timeout=0.1)
    finally:
        server.shutdown()
        vnc.close()
    assert got["reply"] == b"RFB 003.008\n"
