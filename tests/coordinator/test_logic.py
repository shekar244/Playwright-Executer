from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from coordinator.db import _utcnow
from coordinator import schemas
from coordinator.schemas import RunRequest, TestChunk, validate_browser, validate_environment
from coordinator.services import count_outcomes, grid_state


@pytest.mark.parametrize(
    "test_id",
    ["tests/test_a.py", "tests/", "tests/test_a.py::test_x", "tests/test_a.py::TestC::test_x[a b-1]"],
)
def test_valid_test_ids(test_id):
    assert RunRequest(tests=[test_id]).tests == [test_id]


@pytest.mark.parametrize(
    "test_id",
    ["--co", "-p os", "/etc/passwd", "../x.py", "tests/../../x.py", "tests/a.py\n--co", "", "x" * 1001],
)
def test_test_ids_cannot_inject_pytest_options_or_escape_the_suite(test_id):
    with pytest.raises(ValidationError):
        RunRequest(tests=[test_id])
    with pytest.raises(ValidationError):
        TestChunk(chunk_id="c", tests=[test_id])


def test_run_request_bounds():
    for bad in ({"tests": []}, {"tests": ["t.py"], "chunk_size": 0}, {"tests": ["t.py"], "chunk_size": 11},
                {"tests": ["t.py"], "browsers": []}):
        with pytest.raises(ValidationError):
            RunRequest(**bad)


def test_run_request_code_reference_is_both_or_neither():
    sha = "a" * 40
    run = RunRequest(tests=["t.py"], repo="amplify", sha=sha)
    assert (run.repo, run.sha) == ("amplify", sha)
    bare = RunRequest(tests=["t.py"])
    assert (bare.repo, bare.sha) == (None, None)
    for bad in ({"repo": "amplify"}, {"sha": sha}, {"repo": "../x", "sha": sha}, {"repo": "a/b", "sha": sha},
                {"repo": "", "sha": sha}, {"repo": "amplify", "sha": "A" * 40}, {"repo": "amplify", "sha": "abc"},
                {"repo": "amplify", "sha": sha + "\n"}):
        with pytest.raises(ValidationError):
            RunRequest(tests=["t.py"], **bad)


def test_browser_and_environment_validation():
    assert validate_browser("Firefox") == "firefox"
    assert validate_environment("QA") == "qa"
    for fn, bad in ((validate_browser, "ie"), (validate_environment, "staging")):
        with pytest.raises(HTTPException) as exc:
            fn(bad)
        assert exc.value.status_code == 422


def test_count_outcomes_from_per_test_results():
    results = [{"test_results": [{"outcome": "passed"}, {"outcome": "failed"}, {"outcome": "error"},
                                 {"outcome": "skipped"}]}]
    assert count_outcomes(["a", "b", "c", "d"], results) == {"passed": 1, "failed": 2, "skipped": 1}


def test_count_outcomes_legacy_chunk_level():
    assert count_outcomes(["a", "b"], [{"success": True}]) == {"passed": 2, "failed": 0, "skipped": 0}
    assert count_outcomes(["a", "b"], [{"success": False}]) == {"passed": 0, "failed": 2, "skipped": 0}
    assert count_outcomes(["a"], []) == {"passed": 0, "failed": 1, "skipped": 0}


def _runner(status, age_seconds=0):
    return SimpleNamespace(status=status, last_seen=_utcnow() - timedelta(seconds=age_seconds))


@pytest.mark.parametrize(
    "runner, phase, expected",
    [
        (None, "Pending", "starting"),
        (None, "Running", "starting"),
        (None, "Succeeded", "exited"),
        (None, "Failed", "failed"),
        (_runner("busy"), "Running", "busy"),
        (_runner("idle"), None, "idle"),
        (_runner("busy", age_seconds=600), "Running", "lost"),
        (_runner("exited"), None, "exited"),
        (_runner("busy"), "Failed", "failed"),
        (_runner("idle"), "Succeeded", "exited"),
    ],
)
def test_grid_state(runner, phase, expected):
    assert grid_state(runner, phase, _utcnow()) == expected


@pytest.mark.parametrize(
    "configured, sent, allowed",
    [
        ("", None, True),            # auth disabled
        ("s3cret", "s3cret", True),
        ("s3cret", "wrong", False),
        ("s3cret", None, False),
        ("s3cret", "sécret", False),  # non-ASCII must be a 401, not a TypeError / HTTP 500
    ],
)
def test_require_token(monkeypatch, configured, sent, allowed):
    monkeypatch.setattr(schemas, "API_TOKEN", configured)
    if allowed:
        assert schemas.require_token(sent) is None
    else:
        with pytest.raises(HTTPException) as exc:
            schemas.require_token(sent)
        assert exc.value.status_code == 401
