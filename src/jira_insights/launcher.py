"""
Start / stop / probe the Jira Insights Streamlit app as a side-car process.

Flask and Streamlit each want to own the server loop, so Insights runs next
to the Flask app on its own port and the UI embeds it with an <iframe>.
The process is started from the tool root so .streamlit/config.toml applies.

  INSIGHTS_PORT         — Streamlit port (default 8501)
  INSIGHTS_PUBLIC_URL   — browser-facing URL when it differs from http://<flask-host>:<port>: a full URL
                          (its own host) or a path on QEA's own host, e.g. /jira-insights (a route/ingress
                          path that sends /jira-insights to the Insights service)
  INSIGHTS_INTERNAL_URL — Insights runs as its own service (its own pod), e.g.
                          http://amplify-insights:8501/jira-insights: QEA checks its health there and never
                          starts a Streamlit process of its own

Standalone:  python -m jira_insights.launcher
"""
from __future__ import annotations

import atexit
import importlib.util
import os
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path

from .settings import TOOL_ROOT, workspace_dir

APP_FILE     = Path(__file__).with_name("app.py")
DEFAULT_PORT = 8501


def port() -> int:
    return int(os.environ.get("INSIGHTS_PORT", DEFAULT_PORT))


def bind_address() -> str:
    # Inside a container the port must be reachable from outside; locally stay on loopback.
    return "0.0.0.0" if os.environ.get("CONTAINER", "0") == "1" else "127.0.0.1"


def build_command(python: str = sys.executable, port_: int | None = None,
                  address: str | None = None) -> list[str]:
    return [python, "-m", "streamlit", "run", str(APP_FILE),
            "--server.port", str(port_ or port()),
            "--server.address", address or bind_address(),
            "--server.headless", "true"]


def internal_url() -> str:
    return os.environ.get("INSIGHTS_INTERNAL_URL", "").strip().rstrip("/")


def _probe_url(base: str) -> bool:
    try:
        with urllib.request.urlopen(f"{base}/_stcore/health", timeout=2) as resp:
            return resp.read().strip() == b"ok"
    except Exception:
        return False


def _probe_health(port_: int) -> bool:
    return _probe_url(f"http://127.0.0.1:{port_}")


class InsightsProcess:
    def __init__(self, popen=subprocess.Popen, probe=_probe_health,
                 has_streamlit=lambda: importlib.util.find_spec("streamlit") is not None, probe_url=_probe_url):
        self._popen = popen
        self._probe = probe
        self._probe_url = probe_url
        self._has_streamlit = has_streamlit
        self._proc = None
        self._lock = threading.Lock()
        self._atexit_registered = False

    @property
    def log_file(self) -> Path:
        return workspace_dir() / "streamlit.log"

    def _log_tail(self, lines: int = 25) -> str:
        try:
            return "\n".join(self.log_file.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
        except OSError:
            return ""

    def _external_status(self, base: str) -> dict:
        healthy = self._probe_url(base)
        return {"running": healthy, "healthy": healthy, "managed": False, "exited": False, "external": True,
                "port": port(), "public_url": os.environ.get("INSIGHTS_PUBLIC_URL", "").rstrip("/"), "log_tail": ""}

    def status(self) -> dict:
        if internal_url():
            return self._external_status(internal_url())
        managed = self._proc is not None and self._proc.poll() is None
        exited  = self._proc is not None and not managed
        healthy = self._probe(port())
        return {
            "running": managed or healthy, "healthy": healthy, "managed": managed,
            "exited": exited and not healthy, "port": port(),
            "public_url": os.environ.get("INSIGHTS_PUBLIC_URL", "").rstrip("/"),
            "log_tail": self._log_tail() if exited and not healthy else "",
        }

    def start(self) -> dict:
        if internal_url():                    # its own pod: nothing to start here, only report whether it answers
            status = self._external_status(internal_url())
            if not status["healthy"]:
                status["error"] = (f"Jira Insights runs as its own service and is not answering at {internal_url()} "
                                   "— check the amplify-insights pod")
            return status
        with self._lock:
            if self._probe(port()) or (self._proc is not None and self._proc.poll() is None):
                return self.status()
            if not self._has_streamlit():
                return {**self.status(), "error": "Streamlit is not installed for this Python "
                        f"({sys.executable}). Run: pip install -r amplify-requirements.txt"}
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            log = open(self.log_file, "w", encoding="utf-8")
            try:
                self._proc = self._popen(build_command(), cwd=str(TOOL_ROOT),
                                         stdout=log, stderr=subprocess.STDOUT)
            except OSError as exc:
                return {**self.status(), "error": f"Could not start Jira Insights: {exc}"}
            finally:
                log.close()     # the child holds its own handle
            if not self._atexit_registered:
                atexit.register(self.stop)
                self._atexit_registered = True
            return self.status()

    def stop(self) -> dict:
        if internal_url():
            return self._external_status(internal_url())
        with self._lock:
            proc, self._proc = self._proc, None
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
            return self.status()


process = InsightsProcess()


if __name__ == "__main__":
    raise SystemExit(subprocess.call(build_command(), cwd=str(TOOL_ROOT)))
