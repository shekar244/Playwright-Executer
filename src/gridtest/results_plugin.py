"""pytest plugin: write per-test outcomes to a JSON file for the grid runner to report.

Enabled by pytest.ini (-p gridtest.results_plugin) and active only when GRID_RESULTS_FILE
is set, so local runs are unaffected. Works with pytest-xdist: reports are collected in
the controller process, which is the only one that writes the file.
"""
import json
import os

_results = {}


def _enabled(config) -> bool:
    return bool(os.getenv("GRID_RESULTS_FILE")) and not hasattr(config, "workerinput")


def pytest_runtest_logreport(report):
    if not os.getenv("GRID_RESULTS_FILE"):
        return
    entry = _results.setdefault(
        report.nodeid, {"nodeid": report.nodeid, "outcome": "passed", "duration": 0.0, "message": None}
    )
    entry["duration"] = round(entry["duration"] + report.duration, 3)
    if report.failed:
        # A failure in setup/teardown is an "error"; in the call phase it is a "failed" test.
        entry["outcome"] = "failed" if report.when == "call" else "error"
        entry["message"] = str(report.longrepr)[-2000:]
    elif report.skipped and entry["outcome"] == "passed":
        entry["outcome"] = "skipped"
        longrepr = report.longrepr
        entry["message"] = str(longrepr[2] if isinstance(longrepr, tuple) else longrepr)[-500:]


def pytest_sessionfinish(session):
    path = os.getenv("GRID_RESULTS_FILE")
    if path and _enabled(session.config):
        with open(path, "w") as f:
            json.dump(list(_results.values()), f)
