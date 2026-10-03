"""
Start / stop / probe the Jira Insights Streamlit app as a side-car process.

Flask and Streamlit each want to own the server loop, so Insights runs next
to the Flask app on its own port and the UI embeds it with an <iframe>.
The process is started from the tool root so .streamlit/config.toml applies.

  INSIGHTS_PORT        — Streamlit port (default 8501)
  INSIGHTS_PUBLIC_URL  — browser-facing URL when it differs from
                         http://<flask-host>:<port> (e.g. an OpenShift route)

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


def _probe_health(port_: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port_}/_stcore/health", timeout=1) as resp:
            return resp.read().strip() == b"ok"
    except Exception:
        return False


class InsightsProcess:
    def __init__(self, popen=subprocess.Popen, probe=_probe_health,
                 has_streamlit=lambda: importlib.util.find_spec("streamlit") is not None):
        self._popen = popen
        self._probe = probe
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

    def status(self) -> dict:
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
        with self._lock:
            if self._probe(port()) or (self._proc is not None and self._proc.poll() is None):
                return self.status()
            if not self._has_streamlit():
                return {**self.status(), "error": "Streamlit is not installed for this Python "
                        f"({sys.executable}). Run: pip install -r requirements.txt"}
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
