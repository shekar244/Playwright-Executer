"""Run the pytest process of one chunk under a wall-clock limit, in its own process group.

The runner keeps heartbeating while pytest runs, so the coordinator never requeues a chunk
whose pod is alive: without a limit one hung test (or a hostile one) pins the pod and its
run forever. On timeout the WHOLE group is stopped (pytest, its xdist workers, browsers and
anything else the tests started): SIGTERM, a short grace period, then SIGKILL.
"""
import math
import os
import signal
import subprocess
import time
from dataclasses import dataclass

DEFAULT_CHUNK_TIMEOUT_SECONDS = 1800.0
KILL_GRACE_SECONDS = 3.0  # between SIGTERM and SIGKILL
DRAIN_SECONDS = 5.0  # to collect the output once the group is dead


def chunk_timeout_seconds() -> float:
    """GRID_CHUNK_TIMEOUT_SECONDS: the wall-clock limit of one chunk's pytest run (default 1800)."""
    try:
        value = float(os.getenv("GRID_CHUNK_TIMEOUT_SECONDS", ""))
    except ValueError:
        return DEFAULT_CHUNK_TIMEOUT_SECONDS
    return value if math.isfinite(value) and value > 0 else DEFAULT_CHUNK_TIMEOUT_SECONDS


def timeout_message(timeout: float) -> str:
    shown = f"{int(timeout)}" if timeout == int(timeout) else f"{timeout:g}"
    return f"Chunk timed out after {shown} s (GRID_CHUNK_TIMEOUT_SECONDS)"


@dataclass
class PytestOutcome:
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool = False


def _signal_group(pgid: int, sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        pass  # nobody left (PermissionError: only zombies remain on some platforms)


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def _stop_group(proc: subprocess.Popen) -> None:
    """SIGTERM the process group, give it KILL_GRACE_SECONDS, SIGKILL whatever is left."""
    pgid = proc.pid  # start_new_session made the child the leader of its own group
    _signal_group(pgid, signal.SIGTERM)
    deadline = time.monotonic() + KILL_GRACE_SECONDS
    try:
        proc.wait(timeout=KILL_GRACE_SECONDS)  # reaps the leader, so the group check below is exact
    except subprocess.TimeoutExpired:
        pass
    while _group_alive(pgid) and time.monotonic() < deadline:
        time.sleep(0.05)
    if _group_alive(pgid):
        _signal_group(pgid, signal.SIGKILL)
    try:
        proc.wait(timeout=DRAIN_SECONDS)
    except subprocess.TimeoutExpired:
        pass


def _text(value) -> str:
    if isinstance(value, bytes):  # TimeoutExpired carries bytes even for a text-mode Popen
        return value.decode("utf-8", errors="replace")
    return value or ""


def _drain(proc: subprocess.Popen) -> tuple[str, str]:
    """Everything pytest printed before it was stopped."""
    try:
        return proc.communicate(timeout=DRAIN_SECONDS)
    except subprocess.TimeoutExpired as e:
        # A process outside the group still holds the pipes: keep what we have and let go.
        for pipe in (proc.stdout, proc.stderr):
            try:
                pipe.close()
            except OSError:
                pass
        return _text(e.stdout), _text(e.stderr)


def run_pytest(args: list[str], env: dict, cwd, timeout: float) -> PytestOutcome:
    """Run args, capture its output, and stop the whole process group after `timeout` seconds.

    Raises OSError when the process cannot be started. Any other exception raised while it
    runs (the runner's SIGTERM handler raises SystemExit) kills the group before propagating.
    """
    with subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                          encoding="utf-8", errors="replace", env=env, cwd=cwd,
                          start_new_session=True) as proc:
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
            return PytestOutcome(proc.returncode, stdout, stderr)
        except subprocess.TimeoutExpired:
            _stop_group(proc)
            stdout, stderr = _drain(proc)
            return PytestOutcome(proc.returncode, stdout, stderr, timed_out=True)
        except BaseException:
            _signal_group(proc.pid, signal.SIGKILL)
            raise


def timeout_stderr(timeout: float, captured: str, limit: int) -> str:
    """The timeout message, then as much of the captured stderr as still fits in `limit` characters."""
    message = timeout_message(timeout)
    room = limit - len(message) - 1
    if not captured or room <= 0:
        return message
    if len(captured) > room:
        marker = "...[truncated]...\n"
        captured = marker + captured[-max(room - len(marker), 0):]
    return f"{message}\n{captured}"
