"""Fixtures for the runner tests: a fake coordinator and an isolated runner_client module."""
import signal
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
TEST_ROOT = ROOT / "tests" / "runner"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(TEST_ROOT))

from runner import runner_client  # noqa: E402
from runner_fakes import FakeCoordinator  # noqa: E402


@pytest.fixture
def coordinator():
    fake = FakeCoordinator()
    yield fake
    fake.close()


@pytest.fixture
def runner(coordinator, tmp_path, monkeypatch):
    """runner_client wired to the fake coordinator, with its own empty volume and work dir."""
    for var in ("PYTEST_ADDOPTS", "PYTEST_CURRENT_TEST", "PYTEST_XDIST_WORKER", "PYTEST_XDIST_WORKER_COUNT",
                "GRID_ALLURE_MAX_BYTES", "BROWSER", "TEST_ENV", "PYTHONPATH", "GRID_LIVE_VIEW", "HEADLESS", "DISPLAY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GRID_CODE_ROOT", str(tmp_path / "code-root"))
    monkeypatch.setenv("GRID_WORK_DIR", str(tmp_path / "work"))
    monkeypatch.setenv("GRID_API_TOKEN", "secret-token-value")
    monkeypatch.setenv("COORDINATOR_URL", coordinator.url)
    monkeypatch.setattr(runner_client, "COORDINATOR_URL", coordinator.url)
    monkeypatch.setattr(runner_client, "HEADERS", {"X-API-Token": "secret-token-value"})
    monkeypatch.setattr(runner_client, "RUNNER_ID", "pod-test-1")
    monkeypatch.setattr(runner_client, "BROWSER", "chromium")
    monkeypatch.setattr(runner_client, "ALLURE_UPLOAD_BACKOFF", (0, 0))
    monkeypatch.setattr(runner_client, "_live", None)
    (tmp_path / "code-root").mkdir()
    return runner_client


@pytest.fixture
def keep_sigterm_handler():
    """poll_and_execute installs a SIGTERM handler; restore the previous one afterwards."""
    previous = signal.getsignal(signal.SIGTERM)
    yield
    signal.signal(signal.SIGTERM, previous)
