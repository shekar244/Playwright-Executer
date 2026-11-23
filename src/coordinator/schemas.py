"""Input validation and request schemas (system boundary)."""
import re
import secrets
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, Header
from pydantic import BaseModel, Field, field_validator, model_validator

from .artifacts import valid_repo, valid_sha
from .pytest_args import PytestArg, validate_list as validate_pytest_args
from .config import (
    API_TOKEN, DEFAULT_BROWSER, DEFAULT_CHUNK_SIZE, DEFAULT_ENVIRONMENT, POD_API_TOKEN,
    SUPPORTED_BROWSERS, SUPPORTED_ENVIRONMENTS,
)

FULL, POD = "full", "pod"


def _token_matches(sent: Optional[str], expected: str) -> bool:
    # Compare bytes: compare_digest raises TypeError (HTTP 500) on non-ASCII str input.
    return secrets.compare_digest((sent or "").encode("utf-8"), expected.encode("utf-8"))


def token_role(sent: Optional[str]) -> Optional[str]:
    """Which credential X-API-Token is: "full", "pod", or None (missing/invalid).

    API_TOKEN unset means authentication is disabled, so everything is "full". A POD_API_TOKEN
    equal to API_TOKEN is the full token. Both comparisons always run (no early exit on a hit).
    """
    if not API_TOKEN:
        return FULL
    is_full = _token_matches(sent, API_TOKEN)
    is_pod = bool(POD_API_TOKEN) and _token_matches(sent, POD_API_TOKEN)
    return FULL if is_full else (POD if is_pod else None)


def require_token(x_api_token: Optional[str] = Header(default=None)) -> None:
    """Pod endpoints: the pod token OR the full token. 401 when missing or invalid."""
    if token_role(x_api_token) is None:
        raise HTTPException(status_code=401, detail="Missing or invalid X-API-Token")


def require_full_token(x_api_token: Optional[str] = Header(default=None)) -> None:
    """Every other endpoint: the full token only (401 unknown, 403 for a valid pod token)."""
    role = token_role(x_api_token)
    if role is None:
        raise HTTPException(status_code=401, detail="Missing or invalid X-API-Token")
    if role != FULL:
        raise HTTPException(status_code=403, detail="This token is not allowed to call that endpoint")


_TEST_PATH = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./-]*$")


def validate_test_id(test_id: str) -> str:
    """Test ids end up on a pytest command line: forbid options, absolute paths, traversal."""
    path = test_id.partition("::")[0]
    if (
        len(test_id) > 1000
        or not _TEST_PATH.match(path)
        or ".." in path.split("/")
        or any(ord(c) < 32 for c in test_id)
    ):
        raise ValueError(f"Invalid test id: {test_id[:80]!r}")
    return test_id


def validate_browser(browser: str) -> str:
    browser = browser.lower()
    if browser not in SUPPORTED_BROWSERS:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported browser '{browser}'. Choose one of: {list(SUPPORTED_BROWSERS)}",
        )
    return browser


def validate_environment(environment: str) -> str:
    environment = environment.lower()
    if environment not in SUPPORTED_ENVIRONMENTS:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported environment '{environment}'. Choose one of: {SUPPORTED_ENVIRONMENTS}",
        )
    return environment


# --- Pydantic Schemas ------------------------------------------------------------
class TestChunk(BaseModel):
    chunk_id: str
    tests: List[str]
    browser: str = DEFAULT_BROWSER
    environment: str = DEFAULT_ENVIRONMENT

    _check_tests = field_validator("tests")(lambda v: [validate_test_id(t) for t in v])


class RunRequest(BaseModel):
    tests: List[str] = Field(min_length=1)
    environment: str = DEFAULT_ENVIRONMENT
    browsers: List[str] = Field(default_factory=lambda: [DEFAULT_BROWSER], min_length=1)
    chunk_size: int = Field(default=DEFAULT_CHUNK_SIZE, ge=1, le=10)
    requested_by: Optional[str] = Field(default=None, max_length=200)
    # Code the runners run the tests from: an immutable snapshot of <repo> at <sha>.
    repo: Optional[str] = None
    sha: Optional[str] = None
    # Extra pytest options (admin-configured in Amplify, chosen per run); see pytest_args.py.
    pytest_args: List[PytestArg] = Field(default_factory=list)

    _check_tests = field_validator("tests")(lambda v: [validate_test_id(t) for t in v])
    _check_pytest_args = field_validator("pytest_args")(lambda v: validate_pytest_args(v))

    @model_validator(mode="after")
    def _check_code_ref(self):
        if (self.repo is None) != (self.sha is None):
            raise ValueError("repo and sha must be given together")
        if self.repo is not None and not valid_repo(self.repo):
            raise ValueError("Invalid repo name")
        if self.sha is not None and not valid_sha(self.sha):
            raise ValueError("sha must be 40 lowercase hex characters")
        return self


class TestResultSubmission(BaseModel):
    chunk_id: str
    runner_id: str
    results: List[Dict[str, Any]]
