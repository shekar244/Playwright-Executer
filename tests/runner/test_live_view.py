"""Live view on the pod: a virtual display, x11vnc serving it view-only behind a random password, a headed browser.

Nothing about it may cost a chunk: when the display or the VNC server cannot start, or the coordinator does not take
the registration, the runner logs it and runs the tests headless as before.
"""
import re
import shutil
import socket

import pytest

from runner import live_view

SECRET = re.compile(r"^[A-Za-z0-9_-]{8,64}$")          # what the coordinator accepts


class Resp:
    def __init__(self, status_code):
        self.status_code = status_code


class FakeView(live_view.LiveView):
    def __init__(self, alive=True):
        super().__init__(":99", "pw-12345")
        self._alive = alive

    def alive(self):
        return self._alive


# ── settings and commands ─────────────────────────────────────────────────────

@pytest.mark.parametrize("value, on", [("1", True), ("true", True), ("YES", True), (" on ", True), ("0", False),
                                       ("", False), ("false", False), (None, False), ("2", False)])
def test_live_view_is_opt_in(value, on):
    assert live_view.enabled({} if value is None else {"GRID_LIVE_VIEW": value}) is on


def test_passwords_are_random_and_acceptable_to_the_coordinator():
    passwords = {live_view.new_password() for _ in range(200)}

    assert len(passwords) == 200 and all(SECRET.match(p) and len(p) == 8 for p in passwords)


def test_the_vnc_server_is_view_only_on_the_fixed_port_and_the_password_is_not_on_its_command_line():
    cmd = live_view.x11vnc_command("/tmp/grid-work/vnc-abc")

    assert cmd[0] == "x11vnc" and "-viewonly" in cmd and cmd[cmd.index("-rfbport") + 1] == "5900"
    assert cmd[cmd.index("-passwdfile") + 1] == "rm:/tmp/grid-work/vnc-abc"     # x11vnc deletes it once read
    assert "-passwd" not in cmd and "-nopw" not in cmd and "-localhost" not in cmd


def test_the_display_takes_no_network_connections():
    cmd = live_view.xvfb_command()

    assert cmd[:2] == ["Xvfb", ":99"] and cmd[cmd.index("-nolisten") + 1] == "tcp"


def test_without_the_programs_there_is_no_live_view_and_no_error(monkeypatch):
    logged = []
    monkeypatch.setattr(live_view.shutil, "which", lambda name: None)

    assert live_view.start(logged.append) is None
    assert logged == ["Live view off: Xvfb, x11vnc not installed in this image."]


def test_a_display_that_never_comes_up_turns_live_view_off(monkeypatch, tmp_path):
    started, logged = [], []

    class Proc:
        def __init__(self, cmd, **kw):
            started.append(cmd[0])
            self.terminated = False

        def poll(self):
            return 0 if self.terminated else None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(live_view.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(live_view.subprocess, "Popen", Proc)
    monkeypatch.setattr(live_view, "START_TIMEOUT", 0.2)
    monkeypatch.setattr(live_view.os.path, "exists", lambda p: False)

    assert live_view.start(logged.append, str(tmp_path)) is None
    assert started == ["Xvfb"] and logged == ["Live view off: Xvfb did not start"]


@pytest.mark.skipif(not (shutil.which("Xvfb") and shutil.which("x11vnc")), reason="needs Xvfb and x11vnc (runner image)")
def test_the_real_display_and_vnc_server_start_ask_for_a_password_and_stop(tmp_path):
    logged = []
    view = live_view.start(logged.append, str(tmp_path))
    try:
        assert view is not None, logged
        with socket.create_connection(("127.0.0.1", live_view.PORT), timeout=5) as s:
            assert s.recv(12) == b"RFB 003.008\n"
            s.sendall(b"RFB 003.008\n")
            n = s.recv(1)[0]
            assert 2 in s.recv(n)                        # security type 2: VNC password authentication
        assert list(tmp_path.iterdir()) == []            # the password file was read and deleted
    finally:
        if view:
            view.stop()
    assert not view.alive()


# ── registration ──────────────────────────────────────────────────────────────

def test_registration_sends_only_the_password():
    calls = []

    ok = live_view.register(FakeView(), lambda url, **kw: calls.append((url, kw)) or Resp(200), "http://c/api/x",
                            {"X-API-Token": "t"}, print)

    assert ok is True
    assert calls == [("http://c/api/x", {"json": {"password": "pw-12345"}, "headers": {"X-API-Token": "t"}, "timeout": 5})]


@pytest.mark.parametrize("post", [lambda url, **kw: Resp(409), lambda url, **kw: Resp(401),
                                  lambda url, **kw: (_ for _ in ()).throw(ConnectionError("down"))])
def test_a_refused_or_failed_registration_is_logged_and_never_raises(post):
    logged = []

    assert live_view.register(FakeView(), post, "http://c", {}, logged.append) is False
    assert len(logged) == 1 and "tests still run" in logged[0] and "pw-12345" not in logged[0]


# ── the pytest process ────────────────────────────────────────────────────────

def test_with_a_live_view_pytest_gets_the_display_and_runs_headed(runner):
    env = runner.build_test_env("qa", "/tmp/r.json", snapshot=True, view=FakeView())

    assert (env["DISPLAY"], env["HEADLESS"]) == (":99", "false")
    assert "GRID_API_TOKEN" not in env and "pw-12345" not in env.values()


def test_without_a_live_view_nothing_changes(runner):
    env = runner.build_test_env("qa", "/tmp/r.json", snapshot=True)

    assert "DISPLAY" not in env and "HEADLESS" not in env


def test_headed_adds_the_pytest_playwright_flag_before_the_test_ids(runner, monkeypatch):
    monkeypatch.setattr(runner, "pytest_playwright_available", lambda: True)
    args = runner.build_pytest_args(["t.py::a"], snapshot=True, headed=True)

    assert args.index("--headed") < args.index("--") and args[-1] == "t.py::a"
    assert "--headed" not in runner.build_pytest_args(["t.py::a"], snapshot=True)


def test_headed_without_pytest_playwright_adds_no_flag_pytest_would_reject(runner, monkeypatch):
    monkeypatch.setattr(runner, "pytest_playwright_available", lambda: False)

    assert "--headed" not in runner.build_pytest_args(["t.py::a"], snapshot=True, headed=True)


def test_a_chunk_uses_the_live_view_only_while_it_is_alive(runner, monkeypatch):
    seen = []
    monkeypatch.setattr(runner.chunk_runner, "run_pytest",
                        lambda args, env, cwd, timeout: seen.append(("--headed" in args, env.get("DISPLAY")))
                        or runner.chunk_runner.PytestOutcome(0, "", ""))
    monkeypatch.setattr(runner, "pytest_playwright_available", lambda: True)

    for view in (FakeView(alive=True), FakeView(alive=False), None):
        monkeypatch.setattr(runner, "_live", view)
        runner._run_chunk(["t.py::a"], "qa", True, None, None)

    assert seen == [(True, ":99"), (False, None), (False, None)]


# ── start-up ──────────────────────────────────────────────────────────────────

def test_start_live_view_does_nothing_unless_enabled(runner, monkeypatch):
    monkeypatch.setattr(runner.live_view, "start", lambda *a: pytest.fail("must not start"))

    runner.start_live_view()

    assert runner._live is None


def test_start_live_view_starts_and_registers_at_this_pods_url(runner, monkeypatch):
    monkeypatch.setenv("GRID_LIVE_VIEW", "1")
    view, registered = FakeView(), []
    monkeypatch.setattr(runner.live_view, "start", lambda log, work: view)
    monkeypatch.setattr(runner.live_view, "register", lambda v, post, url, headers, log: registered.append((v, url, headers)))

    runner.start_live_view()

    assert runner._live is view
    assert registered == [(view, f"{runner.COORDINATOR_URL}/api/runners/pod-test-1/view", runner.HEADERS)]


def test_a_live_view_that_cannot_start_is_not_registered(runner, monkeypatch):
    monkeypatch.setenv("GRID_LIVE_VIEW", "1")
    monkeypatch.setattr(runner.live_view, "start", lambda log, work: None)
    monkeypatch.setattr(runner.live_view, "register", lambda *a: pytest.fail("nothing to register"))

    runner.start_live_view()

    assert runner._live is None


# ── the framework's own browser fixture ───────────────────────────────────────

@pytest.mark.parametrize("value, headless", [(None, True), ("true", True), ("false", False), ("0", False),
                                             ("No", False), ("off", False), ("1", True)])
def test_the_framework_runs_headed_when_headless_is_false(monkeypatch, value, headless):
    from gridtest import browsers
    if value is None:
        monkeypatch.delenv("HEADLESS", raising=False)
    else:
        monkeypatch.setenv("HEADLESS", value)
    launched = {}

    class Engine:
        def launch(self, **kw):
            launched.update(kw)

    browsers.launch(type("PW", (), {"chromium": Engine()})(), "chromium")

    assert launched["headless"] is headless
    browsers.launch(type("PW", (), {"chromium": Engine()})(), "chromium", headless=True)
    assert launched["headless"] is True                  # an explicit argument still wins
