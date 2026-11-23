"""A chunk's pytest run has a wall-clock limit; on timeout the whole process group dies (W1)."""
import io
import json
import os
import signal
import subprocess
import sys
import tarfile
import threading
import time

import pytest

from runner import chunk_runner
from runner_fakes import REPO, SHA, make_tar

HANGING = {
    "tests/test_hang.py": b'''import os
import subprocess
import sys
import time
from pathlib import Path


def test_passes():
    assert True


def test_hangs_and_leaves_a_child():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(3600)"])
    # this process (an xdist worker), its parent (the pytest controller) and the child
    Path(os.environ["HANG_PIDS_FILE"]).write_text(f"{os.getpid()} {os.getppid()} {child.pid}")
    time.sleep(10**6)
''',
}
PASSES = "tests/test_hang.py::test_passes"
HANGS = "tests/test_hang.py::test_hangs_and_leaves_a_child"
MESSAGE = "Chunk timed out after 2 s (GRID_CHUNK_TIMEOUT_SECONDS)"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(state) and not state.startswith("Z")  # a zombie is dead, just not reaped by init yet


def _wait_until_gone(pids, seconds=5.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and any(_alive(p) for p in pids):
        time.sleep(0.05)
    return [p for p in pids if _alive(p)]


def _read_pids(path, count=3):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            pids = [int(x) for x in path.read_text().split()]
            if len(pids) == count:
                return pids
        except (OSError, ValueError):
            pass
        time.sleep(0.02)
    raise AssertionError(f"{path} was not written")


@pytest.fixture
def hang_env(tmp_path, monkeypatch):
    monkeypatch.setenv("HANG_PIDS_FILE", str(tmp_path / "pids"))
    monkeypatch.setenv("GRID_CHUNK_TIMEOUT_SECONDS", "2")
    return tmp_path / "pids"


@pytest.fixture
def hanging_snapshot(runner, coordinator, tmp_path):
    coordinator.serve_code(REPO, SHA, make_tar(tmp_path / "hang.tgz", HANGING))


def _results(directory):
    return [json.loads(p.read_text()) for p in sorted(directory.glob("*-result.json"))]


# ---------------------------------------------------------------- the real thing

def test_a_hanging_test_is_killed_with_every_process_it_started(runner, hanging_snapshot, hang_env, tmp_path):
    started = time.monotonic()

    (chunk,) = runner.run_assigned_tests([PASSES, HANGS], "qa", REPO, SHA, allure_dir=str(tmp_path / "allure"))

    assert time.monotonic() - started < 40  # it would have been ~11 days
    assert chunk["success"] is False
    assert chunk["stderr"].startswith(MESSAGE)
    assert chunk["tests"] == [PASSES, HANGS] and chunk["environment"] == "qa" and chunk["browser"] == "chromium"
    assert chunk["duration_seconds"] >= 2
    assert "test_hang.py" in chunk["stdout"]  # what pytest printed before it was stopped
    pids = _read_pids(hang_env)
    assert len(set(pids)) == 3
    assert _wait_until_gone(pids) == [], "pytest, the xdist worker or the test's child survived"


def test_allure_results_of_what_finished_are_still_there_after_a_timeout(runner, hanging_snapshot, hang_env,
                                                                         tmp_path):
    allure_dir = tmp_path / "allure"

    runner.run_assigned_tests([PASSES, HANGS], "qa", REPO, SHA, allure_dir=str(allure_dir))

    results = _results(allure_dir)
    assert [(r["name"], r["status"]) for r in results] == [("test_passes", "passed")]
    assert runner.pack_allure(str(allure_dir), 10_000_000)  # and they can be packed for upload


def test_the_runner_loop_reports_the_timeout_uploads_allure_and_carries_on(runner, coordinator, hanging_snapshot,
                                                                           hang_env, tmp_path,
                                                                           keep_sigterm_handler):
    def lease(chunk_id, tests):
        return {"chunk_id": chunk_id, "run_id": "run1", "tests": tests, "environment": "qa",
                "browser": "chromium", "repo": REPO, "sha": SHA}

    coordinator.leases.append(lease("run1-chromium-000", [PASSES, HANGS]))
    coordinator.leases.append(lease("run1-chromium-001", [PASSES]))

    runner.poll_and_execute()  # must neither raise nor exit early

    assert [e[0] for e in coordinator.events] == ["submit", "allure", "submit", "allure"]
    (hung,) = coordinator.events[0][1]["results"]
    assert hung["success"] is False and hung["stderr"].startswith(MESSAGE)
    with tarfile.open(fileobj=io.BytesIO(coordinator.events[1][2]), mode="r:gz") as tar:
        assert any(n.endswith("-result.json") for n in tar.getnames())
    (ok,) = coordinator.events[2][1]["results"]
    assert ok["success"] is True and ok["stderr"] == ""
    assert _wait_until_gone(_read_pids(hang_env)) == []
    assert [c for c in (tmp_path / "work" / "chunks").iterdir()] == []


# ---------------------------------------------------------------- configuration

@pytest.mark.parametrize("value, expected", [
    (None, 1800.0), ("", 1800.0), ("2", 2.0), ("0.5", 0.5), (" 90 ", 90.0), ("7200", 7200.0),
    ("abc", 1800.0), ("0", 1800.0), ("-5", 1800.0), ("nan", 1800.0), ("inf", 1800.0),
])
def test_the_timeout_comes_from_the_environment(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("GRID_CHUNK_TIMEOUT_SECONDS", raising=False)
    else:
        monkeypatch.setenv("GRID_CHUNK_TIMEOUT_SECONDS", value)

    assert chunk_runner.chunk_timeout_seconds() == expected


def test_the_timeout_is_read_for_every_chunk(runner, monkeypatch):
    seen = []

    def fake_run(args, env, cwd, timeout):
        seen.append(timeout)
        return chunk_runner.PytestOutcome(0, "", "")

    monkeypatch.setattr(runner.chunk_runner, "run_pytest", fake_run)
    for value in ("11", "22"):
        monkeypatch.setenv("GRID_CHUNK_TIMEOUT_SECONDS", value)
        runner.run_assigned_tests(["t.py::x"])

    assert seen == [11.0, 22.0]


def test_timeout_message_and_stderr_budget():
    assert chunk_runner.timeout_message(2.0) == MESSAGE
    assert chunk_runner.timeout_message(0.5) == "Chunk timed out after 0.5 s (GRID_CHUNK_TIMEOUT_SECONDS)"
    assert chunk_runner.timeout_stderr(2.0, "", 1000) == MESSAGE
    assert chunk_runner.timeout_stderr(2.0, "boom\n", 1000) == MESSAGE + "\nboom\n"
    long = chunk_runner.timeout_stderr(2.0, "e" * 50_000, 1000)
    assert len(long) <= 1000 and long.startswith(MESSAGE + "\n...[truncated]...\n") and long.endswith("e" * 100)


# ---------------------------------------------------------------- run_pytest on its own

def _tree_script(pids_file, ignore_sigterm=True):
    """A parent and a child that (optionally) ignore SIGTERM; the parent publishes both pids, then waits."""
    child = ("import signal, time; "
             + ("signal.signal(signal.SIGTERM, signal.SIG_IGN); " if ignore_sigterm else "")
             + "open(%r, 'w').write('x'); time.sleep(3600)" % (str(pids_file) + ".child"))
    return f"""
import os, signal, subprocess, sys, time
{"signal.signal(signal.SIGTERM, signal.SIG_IGN)" if ignore_sigterm else ""}
child = subprocess.Popen([sys.executable, "-c", {child!r}])
while not os.path.exists({str(pids_file) + ".child"!r}):
    time.sleep(0.01)
open({str(pids_file)!r}, "w").write(f"{{os.getpid()}} {{child.pid}}")
print("tree up", flush=True)
print("an error line", file=sys.stderr, flush=True)
time.sleep(3600)
"""


def test_a_tree_that_ignores_sigterm_is_killed_after_the_grace_period(tmp_path, monkeypatch):
    monkeypatch.setattr(chunk_runner, "KILL_GRACE_SECONDS", 0.3)
    pids_file = tmp_path / "pids"

    outcome = chunk_runner.run_pytest([sys.executable, "-c", _tree_script(pids_file)], dict(os.environ), None, 3.0)

    assert outcome.timed_out is True
    assert outcome.returncode == -signal.SIGKILL  # SIGTERM was ignored, so it needed the SIGKILL
    assert "tree up" in outcome.stdout and "an error line" in outcome.stderr  # the partial output survives
    assert _wait_until_gone(_read_pids(pids_file, 2)) == []


def test_a_cooperative_tree_is_stopped_by_sigterm_without_waiting_for_the_grace_period(tmp_path, monkeypatch):
    monkeypatch.setattr(chunk_runner, "KILL_GRACE_SECONDS", 30)  # would be obvious if it were used up
    pids_file = tmp_path / "pids"
    started = time.monotonic()

    outcome = chunk_runner.run_pytest([sys.executable, "-c", _tree_script(pids_file, ignore_sigterm=False)],
                                      dict(os.environ), None, 2.0)

    assert outcome.timed_out is True and outcome.returncode == -signal.SIGTERM
    assert time.monotonic() - started < 15
    assert _wait_until_gone(_read_pids(pids_file, 2)) == []


def test_a_normal_run_is_unchanged(tmp_path):
    ok = chunk_runner.run_pytest([sys.executable, "-c", "print('out'); import sys; print('err', file=sys.stderr)"],
                                 dict(os.environ), tmp_path, 30)
    failed = chunk_runner.run_pytest([sys.executable, "-c", "import sys; sys.exit(3)"], dict(os.environ), None, 30)

    assert (ok.returncode, ok.stdout, ok.stderr, ok.timed_out) == (0, "out\n", "err\n", False)
    assert (failed.returncode, failed.timed_out) == (3, False)


def test_output_is_decoded_leniently(tmp_path):
    outcome = chunk_runner.run_pytest(
        [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'ok \\xff\\xfe done')"], dict(os.environ), None, 30)

    assert outcome.stdout == "ok �� done"


def test_a_process_that_cannot_start_raises_oserror(tmp_path):
    with pytest.raises(OSError):
        chunk_runner.run_pytest([str(tmp_path / "no-such-binary")], dict(os.environ), None, 5)
    with pytest.raises(OSError):
        chunk_runner.run_pytest([sys.executable, "-c", "pass"], dict(os.environ), tmp_path / "missing-cwd", 5)


def test_the_runner_reports_a_start_failure_as_a_failed_chunk(runner, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(runner.sys, "executable", str(tmp_path / "no-such-python"))

    (chunk,) = runner.run_assigned_tests(["t.py::x"], "")

    assert chunk["success"] is False and chunk["stderr"] == "Could not start pytest (FileNotFoundError)"


def test_an_exception_while_pytest_runs_kills_its_group_before_propagating(tmp_path):
    pids_file = tmp_path / "pids"
    previous = signal.signal(signal.SIGUSR1, lambda signum, frame: (_ for _ in ()).throw(SystemExit(143)))

    def deliver_sigterm_like_k8s():
        _read_pids(pids_file, 2)
        os.kill(os.getpid(), signal.SIGUSR1)  # the runner's SIGTERM handler raises SystemExit(143)

    threading.Thread(target=deliver_sigterm_like_k8s, daemon=True).start()
    try:
        with pytest.raises(SystemExit):
            chunk_runner.run_pytest([sys.executable, "-c", _tree_script(pids_file)], dict(os.environ), None, 60)
    finally:
        signal.signal(signal.SIGUSR1, previous)

    assert _wait_until_gone(_read_pids(pids_file, 2)) == []
