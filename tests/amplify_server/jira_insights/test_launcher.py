import subprocess
from unittest import mock

import pytest

from jira_insights import launcher
from jira_insights.launcher import APP_FILE, InsightsProcess, build_command
from jira_insights.settings import TOOL_ROOT


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("JIRA_INSIGHTS_HOME", str(tmp_path))
    monkeypatch.delenv("INSIGHTS_PORT", raising=False)
    monkeypatch.delenv("INSIGHTS_PUBLIC_URL", raising=False)
    monkeypatch.delenv("CONTAINER", raising=False)
    monkeypatch.setattr(launcher.atexit, "register", lambda fn: None)
    return tmp_path


def fake_proc(alive=True):
    proc = mock.Mock()
    proc.poll.return_value = None if alive else 1
    return proc


def test_command_runs_the_app_on_loopback_by_default():
    cmd = build_command(python="py")
    assert cmd[:5] == ["py", "-m", "streamlit", "run", str(APP_FILE)]
    assert cmd[cmd.index("--server.port") + 1] == "8501"
    assert cmd[cmd.index("--server.address") + 1] == "127.0.0.1"


def test_container_binds_all_interfaces_and_honours_port(monkeypatch):
    monkeypatch.setenv("CONTAINER", "1")
    monkeypatch.setenv("INSIGHTS_PORT", "9000")
    cmd = build_command(python="py")
    assert cmd[cmd.index("--server.address") + 1] == "0.0.0.0"
    assert cmd[cmd.index("--server.port") + 1] == "9000"


def test_start_spawns_streamlit_from_tool_root_so_config_toml_applies():
    popen = mock.Mock(return_value=fake_proc())
    p = InsightsProcess(popen=popen, probe=lambda port: False, has_streamlit=lambda: True)
    status = p.start()

    args, kwargs = popen.call_args
    assert args[0][2:4] == ["streamlit", "run"]
    assert kwargs["cwd"] == str(TOOL_ROOT)
    assert kwargs["stderr"] is subprocess.STDOUT
    assert status["managed"] and status["running"] and not status["healthy"]


def test_start_is_a_no_op_when_already_healthy():
    popen = mock.Mock()
    status = InsightsProcess(popen=popen, probe=lambda port: True, has_streamlit=lambda: True).start()
    popen.assert_not_called()
    assert status["healthy"] and not status["managed"]


def test_start_reports_missing_streamlit():
    popen = mock.Mock()
    status = InsightsProcess(popen=popen, probe=lambda port: False, has_streamlit=lambda: False).start()
    popen.assert_not_called()
    assert "pip install -r amplify-requirements.txt" in status["error"]


def test_crashed_process_reports_log_tail(workspace):
    p = InsightsProcess(popen=mock.Mock(return_value=fake_proc(alive=False)),
                        probe=lambda port: False, has_streamlit=lambda: True)
    p.start()
    (workspace / "streamlit.log").write_text("Traceback...\nModuleNotFoundError: plotly\n")
    status = p.status()
    assert status["exited"] and not status["running"]
    assert "ModuleNotFoundError: plotly" in status["log_tail"]


def test_stop_terminates_the_managed_process():
    proc = fake_proc()
    p = InsightsProcess(popen=mock.Mock(return_value=proc), probe=lambda port: False, has_streamlit=lambda: True)
    p.start()
    status = p.stop()
    proc.terminate.assert_called_once()
    assert not status["managed"]


def test_public_url_override(monkeypatch):
    monkeypatch.setenv("INSIGHTS_PUBLIC_URL", "https://insights.apps.example.com/")
    status = InsightsProcess(probe=lambda port: True).status()
    assert status["public_url"] == "https://insights.apps.example.com"


def test_flask_routes_delegate_to_the_process(monkeypatch, admin):
    from amplify_server.routes import insights

    fake = mock.Mock()
    fake.status.return_value = {"running": False}
    fake.start.return_value = {"error": "Streamlit is not installed"}
    fake.stop.return_value = {"running": False}
    monkeypatch.setattr(insights, "process", fake)

    assert admin.get("/api/insights/status").json == {"running": False}
    started = admin.post("/api/insights/start")
    assert started.status_code == 500 and "not installed" in started.json["error"]
    assert admin.post("/api/insights/stop").status_code == 200
    fake.stop.assert_called_once()


def test_launcher_routes_require_login(monkeypatch, app):
    from amplify_server.routes import insights

    fake = mock.Mock()
    monkeypatch.setattr(insights, "process", fake)
    anon = app.test_client()
    assert anon.get("/api/insights/status").status_code == 401
    assert anon.post("/api/insights/start").status_code == 401
    assert anon.post("/api/insights/stop").status_code == 401
    fake.start.assert_not_called()
    fake.stop.assert_not_called()


# ── Jira Hub in its own pod (INSIGHTS_INTERNAL_URL) ──────────────────────────

def test_with_an_internal_url_qea_checks_the_service_and_never_starts_streamlit(monkeypatch):
    monkeypatch.setenv("INSIGHTS_INTERNAL_URL", "http://jira-hub:8501/jira-hub/")
    monkeypatch.setenv("INSIGHTS_PUBLIC_URL", "/jira-hub")
    probed, popen = [], mock.Mock()
    p = InsightsProcess(popen=popen, probe=lambda port: pytest.fail("no local probe"), has_streamlit=lambda: True,
                        probe_url=lambda base: probed.append(base) or True)

    status = p.start()

    popen.assert_not_called()
    assert probed == ["http://jira-hub:8501/jira-hub"]
    assert status["healthy"] is True and status["external"] is True and status["public_url"] == "/jira-hub"
    assert "error" not in status and p.stop()["managed"] is False


def test_an_internal_service_that_does_not_answer_is_reported_not_replaced(monkeypatch):
    monkeypatch.setenv("INSIGHTS_INTERNAL_URL", "http://jira-hub:8501/jira-hub")
    popen = mock.Mock()

    status = InsightsProcess(popen=popen, has_streamlit=lambda: True, probe_url=lambda base: False).start()

    popen.assert_not_called()
    assert status["healthy"] is False and "jira-hub pod" in status["error"]


def test_open_sends_the_browser_to_a_path_on_qeas_own_host(monkeypatch, admin):
    monkeypatch.setenv("INSIGHTS_PUBLIC_URL", "/jira-hub")

    resp = admin.get("/api/insights/open?theme=dark")

    assert resp.status_code == 302 and resp.headers["Location"].startswith("/jira-hub/?sso=")
