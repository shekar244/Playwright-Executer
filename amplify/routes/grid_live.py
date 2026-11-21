"""
Blueprint: live view of a Grid runner pod's browser (Grid tab → 👁 Live).

A runner pod started with GRID_LIVE_VIEW=1 runs its browser headed on a virtual display and serves it with x11vnc
(view-only, random per-pod password) on port 5900. The coordinator knows each pod's address and password and gives
them out only for the full token, i.e. to Amplify. Users never reach a pod directly: their noVNC viewer talks to
Amplify over a WebSocket and Amplify relays the bytes to the pod.

  GET /api/grid/pods/<name>/view  — may this user watch the pod? {pod, password, ws_path, run_id, chunk_id, browser}
  GET /api/grid/pods/<name>/vnc   — WebSocket (subprotocol "binary"): the viewer <-> the pod's VNC server

Who may watch: the person who started the run the pod is executing, or an admin (a pod between runs: admins only).
Both routes check that again on every call; the WebSocket also refuses other origins (it authenticates with the
session cookie, which a foreign page could otherwise ride). Amplify connects only to an IP address the coordinator
recorded for the pod, on port 5900, never to anything the browser names.

  GRID_LIVE_VIEW_MAX          — viewers at once (default 4); each one holds a server thread while it watches
  GRID_LIVE_VIEW_MAX_SECONDS  — a viewer is disconnected after this long (default 3600)
"""
from __future__ import annotations

import ipaddress
import os
import socket
import threading
import time
import urllib.parse

from flask import Blueprint, Response, g, jsonify, request

from amplify.auth_utils import require_auth
from amplify.routes import grid as grid_routes          # its coordinator_request and id rule (looked up per call)

bp = Blueprint("grid_live", __name__)

VNC_PORT = 5900                    # the only port Amplify ever connects to on a pod
CONNECT_TIMEOUT = 5
BUFFER = 64 * 1024


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, default)))
    except ValueError:
        return default


MAX_VIEWERS = _env_int("GRID_LIVE_VIEW_MAX", 4)
MAX_SECONDS = _env_int("GRID_LIVE_VIEW_MAX_SECONDS", 3600)
_viewers = threading.BoundedSemaphore(MAX_VIEWERS)


def _error(message: str, status: int):
    return jsonify({"error": message}), status


def pod_address(view) -> str | None:
    """The pod's IP address from the coordinator's answer, or None when it is not one Amplify will connect to."""
    if not isinstance(view, dict) or view.get("port") != VNC_PORT or not isinstance(view.get("host"), str):
        return None
    try:
        ip = ipaddress.ip_address(view["host"])
    except ValueError:
        return None
    if ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
        return None                                     # e.g. 169.254.169.254, the cloud metadata service
    return str(ip)


def authorize(name: str):
    """(coordinator's view record, None) when g.user may watch pod `name`; (None, (message, status)) otherwise."""
    if not isinstance(name, str) or not grid_routes._ID.match(name):
        return None, ("Invalid pod name", 400)
    view, status = grid_routes.coordinator_request("GET", f"/api/runners/{name}/view")
    if status != 200:
        message = view.get("error") if isinstance(view, dict) else None
        return None, (message or "This pod has no live view", status)
    if pod_address(view) is None or not isinstance(view.get("password"), str):
        return None, ("The coordinator returned an unusable live view address", 502)
    if g.user.is_admin:
        return view, None
    run_id = view.get("run_id")
    if not (isinstance(run_id, str) and grid_routes._ID.match(run_id)):
        return None, ("Only an admin can watch a pod that is not running a test", 403)
    run, status = grid_routes.coordinator_request("GET", f"/api/runs/{run_id}")
    if status != 200:
        return None, ("The run this pod is executing could not be checked", 502 if status < 500 else status)
    owner = run.get("requested_by") if isinstance(run, dict) else None
    if isinstance(owner, str) and owner and owner == g.user.username:
        return view, None
    return None, ("Only the person who started this run, or an admin, can watch it", 403)


def same_origin() -> bool:
    """A browser always sends Origin on a WebSocket handshake; it must be this server (host and port)."""
    origin = request.headers.get("Origin")
    if origin is None:
        return True                                     # not a browser: it cannot be riding someone's cookie
    return urllib.parse.urlsplit(origin).netloc.lower() == (request.host or "").lower()


@bp.route("/api/grid/pods/<name>/view")
@require_auth
def grid_pod_view(name):
    view, err = authorize(name)
    if err:
        return _error(*err)
    resp = jsonify({"pod": name, "password": view["password"], "ws_path": f"/api/grid/pods/{name}/vnc",
                    "run_id": view.get("run_id"), "chunk_id": view.get("chunk_id"), "browser": view.get("browser")})
    resp.headers["Cache-Control"] = "no-store"
    return resp


def relay(ws, sock, max_seconds: float = MAX_SECONDS) -> None:
    """Copy bytes both ways until either side closes or max_seconds pass. Never raises."""
    from simple_websocket import ConnectionClosed

    def pod_to_viewer():
        try:
            while True:
                data = sock.recv(BUFFER)
                if not data:
                    break
                ws.send(data)
        except Exception:
            pass
        finally:
            try:
                ws.close()
            except Exception:
                pass

    pump = threading.Thread(target=pod_to_viewer, name="grid-live-view", daemon=True)
    pump.start()
    deadline = time.monotonic() + max_seconds
    try:
        while ws.connected and time.monotonic() < deadline:
            data = ws.receive(timeout=5)
            if data is None:
                continue
            sock.sendall(data if isinstance(data, (bytes, bytearray)) else data.encode())
    except (ConnectionClosed, OSError):
        pass
    finally:
        for step in (lambda: sock.shutdown(socket.SHUT_RDWR), sock.close, ws.close):
            try:
                step()
            except Exception:
                pass
        pump.join(timeout=5)


def _websocket_done(ws) -> Response:
    """The response for a request whose connection the WebSocket took over (same as flask-sock does)."""
    class WebSocketResponse(Response):
        def __call__(self, *args, **kwargs):
            if ws.mode == "gunicorn":
                raise StopIteration()                   # gunicorn must not write an HTTP response on the socket
            if ws.mode == "werkzeug":
                return super().__call__(*args, **kwargs)
            return []
    return WebSocketResponse()


@bp.route("/api/grid/pods/<name>/vnc", websocket=True)
@require_auth
def grid_pod_vnc(name):
    if not same_origin():
        return _error("Live view refused: the request did not come from this site", 403)
    view, err = authorize(name)
    if err:
        return _error(*err)
    if not _viewers.acquire(blocking=False):
        return _error(f"Too many people are watching live views right now (at most {MAX_VIEWERS}); try again soon", 503)
    try:
        try:
            sock = socket.create_connection((pod_address(view), VNC_PORT), timeout=CONNECT_TIMEOUT)
        except OSError:
            return _error("The pod's live view cannot be reached (it may have just finished)", 502)
        sock.settimeout(None)
        try:
            from simple_websocket import Server
            ws = Server(request.environ, subprotocols=["binary"], max_message_size=BUFFER)
        except Exception:
            sock.close()
            return _error("WebSocket handshake failed", 400)
        relay(ws, sock)
        return _websocket_done(ws)
    finally:
        _viewers.release()
