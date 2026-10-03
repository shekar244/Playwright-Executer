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
    assert "pip install -r requirements.txt" in status["error"]


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


def test_flask_routes_delegate_to_the_process(monkeypatch):
    from flask import Flask
    from routes import insights

    fake = mock.Mock()
    fake.status.return_value = {"running": False}
    fake.start.return_value = {"error": "Streamlit is not installed"}
    fake.stop.return_value = {"running": False}
    monkeypatch.setattr(insights, "process", fake)
    app = Flask(__name__)
    app.register_blueprint(insights.bp)
    client = app.test_client()

    assert client.get("/api/insights/status").json == {"running": False}
    started = client.post("/api/insights/start")
    assert started.status_code == 500 and "not installed" in started.json["error"]
    assert client.post("/api/insights/stop").status_code == 200
    fake.stop.assert_called_once()
