"""
Manages subprocess execution of pytest with real-time output streaming.
Runs in a background thread so the Tkinter UI stays responsive.
"""

import os
import signal
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable, Dict, List, Optional


class TestRunner:
    """Spawn pytest as a subprocess and stream stdout/stderr back to the UI."""

    def __init__(
        self,
        cmd: List[str],
        cwd: str,
        env_overrides: Dict[str, str],
        on_output: Callable[[str], None],
        on_finish: Callable[[int, bool], None],
    ):
        self.cmd = cmd
        self.cwd = cwd
        self.env_overrides = env_overrides  # Extra env vars injected for this run
        self.on_output = on_output
        self.on_finish = on_finish

        self._process: Optional[subprocess.Popen] = None
        self._cancelled = False
        self._lock = threading.Lock()

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        thread = threading.Thread(target=self._run, daemon=True)
        thread.start()

    def stop(self) -> None:
        with self._lock:
            self._cancelled = True
            proc = self._process

        if proc is None:
            return

        try:
            if sys.platform == "win32":
                proc.terminate()
            else:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                except (ProcessLookupError, OSError):
                    proc.terminate()
        except Exception:
            pass

    # ── Internals ─────────────────────────────────────────────────────────────

    def _run(self) -> None:
        exit_code = -1
        try:
            env = os.environ.copy()
            env.update(self.env_overrides)

            # Ensure pytest output is not buffered
            env["PYTHONUNBUFFERED"] = "1"

            popen_kwargs = {
                "args": self.cmd,
                "cwd": self.cwd,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.STDOUT,
                "text": True,
                "bufsize": 1,
                "encoding": "utf-8",
                "errors": "replace",
                "env": env,
            }

            # Create a new process group on POSIX so we can kill the whole tree.
            if sys.platform != "win32":
                popen_kwargs["preexec_fn"] = os.setsid

            with self._lock:
                self._process = subprocess.Popen(**popen_kwargs)

            assert self._process.stdout is not None
            for line in self._process.stdout:
                self.on_output(line.rstrip("\n\r"))

            self._process.wait()
            exit_code = self._process.returncode

        except FileNotFoundError as exc:
            self.on_output(f"ERROR: Could not launch process — {exc}")
            self.on_output("Verify that Python is installed and accessible from PATH.")
        except PermissionError as exc:
            self.on_output(f"ERROR: Permission denied — {exc}")
        except Exception as exc:
            self.on_output(f"ERROR: Unexpected error — {exc}")
        finally:
            with self._lock:
                cancelled = self._cancelled
            self.on_finish(exit_code, cancelled)
