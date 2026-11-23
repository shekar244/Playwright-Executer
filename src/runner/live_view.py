"""Live view: the pod's browser runs headed on a virtual display that Amplify users can watch.

With GRID_LIVE_VIEW=1 the runner starts Xvfb (display :99) and x11vnc serving that display on port 5900, view-only and
behind a random password, then registers with the coordinator (POST /api/runners/{id}/view). pytest gets DISPLAY and
HEADLESS=false (plus --headed for pytest-playwright), so the browser draws on the display instead of off-screen.
Amplify fetches the password with the full token and proxies the user's viewer to this pod.

Anything that fails here only costs the live view: the runner logs it and runs the tests headless as before.
"""
from __future__ import annotations

import os
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
from dataclasses import dataclass, field

DISPLAY_NUMBER = 99
DISPLAY = f":{DISPLAY_NUMBER}"
PORT = 5900                       # fixed: the coordinator never takes a port from a pod
SCREEN = "1920x1080x24"
START_TIMEOUT = 10.0


def enabled(env=os.environ) -> bool:
    return (env.get("GRID_LIVE_VIEW") or "").strip().lower() in ("1", "true", "yes", "on")


def new_password() -> str:
    # VNC authentication only uses the first 8 characters; these 8 are random URL-safe ones (48 bits).
    return secrets.token_urlsafe(6)


@dataclass
class LiveView:
    display: str
    password: str
    processes: list = field(default_factory=list)

    def alive(self) -> bool:
        return all(p.poll() is None for p in self.processes)

    def stop(self) -> None:
        for p in reversed(self.processes):
            if p.poll() is None:
                p.terminate()
        for p in self.processes:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()


def _wait_for(check, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.1)
    return False


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def xvfb_command(display: str = DISPLAY, screen: str = SCREEN) -> list[str]:
    return ["Xvfb", display, "-screen", "0", screen, "-nolisten", "tcp"]      # local clients only (unix socket)


def x11vnc_command(password_file: str, display: str = DISPLAY, port: int = PORT) -> list[str]:
    # -viewonly: watchers cannot click or type into a running test. -passwdfile rm: x11vnc deletes the file once read,
    # so the password is never on a command line. -forever/-shared: several viewers, and reconnects.
    return ["x11vnc", "-display", display, "-rfbport", str(port), "-passwdfile", f"rm:{password_file}",
            "-viewonly", "-shared", "-forever", "-noxdamage", "-nolookup", "-quiet"]


def start(log, work_dir: str | None = None) -> LiveView | None:
    """Start the display and the VNC server. None (after logging why) when live view cannot run here."""
    missing = [b for b in ("Xvfb", "x11vnc") if shutil.which(b) is None]
    if missing:
        log(f"Live view off: {', '.join(missing)} not installed in this image.")
        return None
    view = LiveView(DISPLAY, new_password())
    devnull = subprocess.DEVNULL
    try:
        view.processes.append(subprocess.Popen(xvfb_command(), stdout=devnull, stderr=devnull))
        socket_path = f"/tmp/.X11-unix/X{DISPLAY_NUMBER}"
        if not _wait_for(lambda: os.path.exists(socket_path), START_TIMEOUT):
            raise RuntimeError("Xvfb did not start")
        fd, password_file = tempfile.mkstemp(prefix="vnc-", dir=work_dir)
        with os.fdopen(fd, "w") as f:                    # mkstemp: mode 0600, readable by this uid only
            f.write(view.password + "\n")
        view.processes.append(subprocess.Popen(x11vnc_command(password_file), stdout=devnull, stderr=devnull))
        if not _wait_for(lambda: _port_open(PORT), START_TIMEOUT) or not view.alive():
            raise RuntimeError("x11vnc did not start")
    except (OSError, RuntimeError) as e:
        log(f"Live view off: {e}")
        view.stop()
        return None
    log(f"Live view on display {DISPLAY}, VNC port {PORT}.")
    return view


def register(view: LiveView, post, url: str, headers: dict, log) -> bool:
    """Tell the coordinator this pod can be watched. `post` is requests.post (injected for tests)."""
    try:
        resp = post(url, json={"password": view.password}, headers=headers, timeout=5)
        if resp.status_code == 200:
            return True
        log(f"Live view not registered (HTTP {resp.status_code}); the tests still run.")
    except Exception as e:  # requests exceptions and anything else: never fail the pod for this
        log(f"Live view not registered ({type(e).__name__}); the tests still run.")
    return False


def test_env(env: dict, view: LiveView | None) -> dict:
    """pytest's environment with the display, when there is one: the browser then runs headed."""
    if view is not None:
        env["DISPLAY"] = view.display
        env["HEADLESS"] = "false"
    return env
