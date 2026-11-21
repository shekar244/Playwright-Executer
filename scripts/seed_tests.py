import argparse
import os
from typing import List

import pytest
import requests

COORDINATOR_URL = os.getenv("COORDINATOR_URL", "http://localhost:8000")
HEADERS = {"X-API-Token": os.environ["GRID_API_TOKEN"]} if os.getenv("GRID_API_TOKEN") else {}
CHUNK_SIZE = 3
SUPPORTED_BROWSERS = ("edge", "chrome", "chromium", "firefox", "webkit")


class TestItemExtractor:
    # Not a test class: stop pytest from trying to collect it.
    __test__ = False

    def __init__(self):
        self.tests: List[str] = []

    def pytest_collection_modifyitems(self, items):
        for item in items:
            self.tests.append(item.nodeid)


def discover_pytest_tests(test_dir: str = "tests") -> List[str]:
    extractor = TestItemExtractor()
    exit_code = pytest.main(["--collect-only", "-q", test_dir], plugins=[extractor])
    if exit_code not in (pytest.ExitCode.OK, pytest.ExitCode.NO_TESTS_COLLECTED):
        raise SystemExit(f"pytest collection failed with exit code {int(exit_code)}")
    return extractor.tests


def parse_browsers(value: str) -> List[str]:
    browsers = [b.strip().lower() for b in value.split(",") if b.strip()]
    unknown = [b for b in browsers if b not in SUPPORTED_BROWSERS]
    if not browsers or unknown:
        raise argparse.ArgumentTypeError(
            f"Unsupported browser(s) {unknown or value!r}. Choose from: {', '.join(SUPPORTED_BROWSERS)}"
        )
    return list(dict.fromkeys(browsers))  # de-duplicate, keep order


def create_run(tests: List[str], environment: str, browsers: List[str], chunk_size: int) -> dict:
    resp = requests.post(
        f"{COORDINATOR_URL}/api/runs",
        json={
            "tests": tests,
            "environment": environment,
            "browsers": browsers,
            "chunk_size": chunk_size,
            "requested_by": os.getenv("GRID_REQUESTED_BY", "seed_tests.py"),
        },
        headers=HEADERS,
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Discover pytest tests and queue them as a grid run.")
    parser.add_argument(
        "--browsers",
        type=parse_browsers,
        default=parse_browsers(os.getenv("BROWSERS", "edge")),
        help=f"Comma-separated browsers to run every test on ({', '.join(SUPPORTED_BROWSERS)}). "
        "Env: BROWSERS. Default: edge",
    )
    parser.add_argument(
        "--environment",
        default=os.getenv("TEST_ENV", "qa"),
        help="Target environment (must be one the coordinator allows). Env: TEST_ENV. Default: qa",
    )
    parser.add_argument("--chunk-size", type=int, default=CHUNK_SIZE)
    args = parser.parse_args()

    tests = discover_pytest_tests()
    print(f"Discovered {len(tests)} tests.")
    run = create_run(tests, args.environment, args.browsers, args.chunk_size)
    print(
        f"Created {run['run_id']}: {run['chunks_queued']} chunks, {run['total_tests']} test executions "
        f"on {', '.join(run['browsers'])} against '{run['environment']}'."
    )
