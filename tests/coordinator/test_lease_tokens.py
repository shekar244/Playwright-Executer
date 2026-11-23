"""Per-chunk lease secrets: generation, enforcement on submit/allure/release, clearing, and secrecy.

Endpoints are driven through the real app over in-memory SQLite (see pod_scope_helpers.py). The lease
and reaper statements are PostgreSQL-only SQL: they are checked with recording sessions here and for
real in test_lease_db.py.
"""
import json
import logging
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from coordinator import leasing
from coordinator import schemas
from coordinator import services
from artifact_helpers import result_archive
from pod_scope_helpers import (
    FULL_TOKEN, LEASE, OTHER_LEASE, POD_TOKEN, RUN, call, chunk, chunk_row, run_record, seed, runner_record,
)

DENIED = {"detail": "Invalid lease token for this chunk"}
JSON = {"Content-Type": "application/json"}


@pytest.fixture(autouse=True)
def tokens(monkeypatch):
    monkeypatch.setattr(schemas, "API_TOKEN", FULL_TOKEN)
    monkeypatch.setattr(schemas, "POD_API_TOKEN", POD_TOKEN)


def _submit(grid, chunk_id, lease=None, token=POD_TOKEN, runner="w1"):
    body = json.dumps({"chunk_id": chunk_id, "runner_id": runner, "results": [{"success": True}]}).encode()
    return call(grid.app, "POST", "/api/results/submit", token=token, lease=lease, body=body, extra_headers=JSON)


def _release(grid, chunk_id, lease=None, runner="w1"):
    return call(grid.app, "POST", f"/api/chunks/{chunk_id}/release", token=POD_TOKEN, lease=lease,
                query=f"runner_id={runner}")


def _upload(grid, chunk_id, lease=None, data=None):
    return call(grid.app, "PUT", f"/api/chunks/{chunk_id}/allure", token=POD_TOKEN, lease=lease,
                body=data if data is not None else result_archive(["test_a"]))


# --- generation and comparison (pure) ---------------------------------------------------------
def test_a_lease_token_is_32_random_hex_characters():
    tokens = {leasing.new_lease_token() for _ in range(50)}
    assert len(tokens) == 50
    assert all(re.fullmatch(r"[0-9a-f]{32}", t) for t in tokens)


@pytest.mark.parametrize(
    "stored, presented, ok",
    [
        (None, None, True),          # not protected: nothing is enforced
        (None, "anything", True),
        (LEASE, LEASE, True),
        (LEASE, None, False),
        (LEASE, "", False),
        (LEASE, OTHER_LEASE, False),
        (LEASE, LEASE.upper(), False),
        (LEASE, LEASE + "0", False),
        (LEASE, "c0ffeé", False),    # non-ASCII must be a refusal, not a TypeError / HTTP 500
    ],
)
def test_lease_token_comparison(stored, presented, ok):
    assert leasing.lease_token_ok(stored, presented) is ok
    if ok:
        assert leasing.require_lease_token(stored, presented) is None
    else:
        with pytest.raises(HTTPException) as exc:
            leasing.require_lease_token(stored, presented)
        assert (exc.value.status_code, exc.value.detail) == (403, DENIED["detail"])


def test_the_comparison_runs_over_bytes_with_compare_digest(monkeypatch):
    seen = []
    real = leasing.secrets.compare_digest
    monkeypatch.setattr(leasing.secrets, "compare_digest", lambda a, b: seen.append((type(a), type(b))) or real(a, b))
    assert leasing.lease_token_ok(LEASE, "wrong") is False
    assert seen == [(bytes, bytes)]


# --- the lease statement and response (PostgreSQL SQL: recording session) ------------------------------
class LeaseSession:
    """Stands in for the session: records statements and answers the lease UPDATE like PostgreSQL would."""

    def __init__(self, has_chunk=True):
        self.has_chunk, self.calls, self.commits = has_chunk, [], 0

    def execute(self, statement, params=None):
        self.calls.append((" ".join(str(statement).split()), params))
        token = params["lease_token"]
        row = SimpleNamespace(chunk_id="c-1", tests=["tests/test_a.py"], run_id=RUN, environment="qa",
                              browser="chrome", repo=None, sha=None, lease_token=token, pytest_args=None)
        return SimpleNamespace(fetchone=lambda: row if self.has_chunk else None)

    def get(self, *_a, **_k):
        return None

    def add(self, *_a):
        pass

    def commit(self):
        self.commits += 1


def test_lease_with_lease_tokens_stores_and_returns_a_fresh_secret(coordinator):
    db = LeaseSession()
    first = coordinator.lease_chunk(runner_id="w1", browser="chrome", lease_tokens=True, db=db)
    second = coordinator.lease_chunk(runner_id="w1", browser="chrome", lease_tokens=True, db=db)
    stored = [params["lease_token"] for _sql, params in db.calls]
    assert re.fullmatch(r"[0-9a-f]{32}", first["lease_token"])
    assert [first["lease_token"], second["lease_token"]] == stored   # what is returned is what is stored
    assert first["lease_token"] != second["lease_token"]            # a re-lease gets a fresh one
    sql = db.calls[0][0]
    returning = sql.rstrip(";").rpartition("RETURNING")[2]
    assert "lease_token = :lease_token" in sql and "lease_token" in returning.split(", ")        # SET and RETURNING


def test_lease_without_the_parameter_creates_no_token_and_the_response_is_unchanged(coordinator):
    db = LeaseSession()
    leased = coordinator.lease_chunk(runner_id="w1", browser="chrome", db=db)
    assert db.calls[0][1]["lease_token"] is None
    assert "lease_token" not in leased
    assert set(leased) == {"chunk_id", "tests", "run_id", "environment", "browser", "repo", "sha", "pytest_args"}
    assert leased["pytest_args"] == []                       # a run without extra arguments


def test_no_chunk_leased_means_no_token_and_a_404(coordinator):
    with pytest.raises(HTTPException) as exc:
        coordinator.lease_chunk(runner_id="w1", browser="chrome", lease_tokens=True, db=LeaseSession(False))
    assert exc.value.status_code == 404 and "lease_token" not in str(exc.value.detail)


@pytest.mark.parametrize("query, expected", [("", False), ("&lease_tokens=1", True), ("&lease_tokens=0", False)])
def test_the_lease_tokens_query_parameter_reaches_the_lease(grid, coordinator, monkeypatch, query, expected):
    asked = []
    monkeypatch.setattr(coordinator, "lease_next_chunk", lambda db, w, b, with_token: asked.append(with_token))
    call(grid.app, "GET", "/api/runner/lease", token=POD_TOKEN, query="runner_id=w1&browser=chrome" + query)
    assert asked == [expected]


# --- submit ------------------------------------------------------------------------------------------
def test_submit_to_a_protected_chunk_needs_the_right_lease_token(grid):
    seed(grid.sessions, chunk("c-1", "running", LEASE))
    assert _submit(grid, "c-1") == (403, DENIED)
    assert _submit(grid, "c-1", lease="wrong") == (403, DENIED)
    assert _submit(grid, "c-1", lease=OTHER_LEASE) == (403, DENIED)
    assert chunk_row(grid.sessions, "c-1") == ("running", "w1", LEASE)   # nothing was written
    assert _submit(grid, "c-1", lease=LEASE) == (200, {"status": "recorded", "chunk_id": "c-1"})
    assert chunk_row(grid.sessions, "c-1") == ("completed", "w1", LEASE)


def test_the_lease_token_is_kept_after_completion_so_a_retried_submit_works(grid):
    seed(grid.sessions, chunk("c-1", "running", LEASE))
    assert _submit(grid, "c-1", lease=LEASE)[0] == 200
    assert _submit(grid, "c-1", lease=LEASE)[0] == 200
    assert _submit(grid, "c-1", lease="wrong")[0] == 403


def test_the_full_token_does_not_bypass_the_lease_token(grid):
    seed(grid.sessions, chunk("c-1", "running", LEASE))
    assert _submit(grid, "c-1", token=FULL_TOKEN) == (403, DENIED)
    assert _submit(grid, "c-1", token=FULL_TOKEN, lease=LEASE)[0] == 200


def test_a_pod_cannot_use_one_chunks_token_on_another_chunk(grid):
    seed(grid.sessions, chunk("c-1", "running", LEASE), chunk("c-2", "running", OTHER_LEASE, runner="w2"))
    assert _submit(grid, "c-2", lease=LEASE, runner="w1") == (403, DENIED)
    assert chunk_row(grid.sessions, "c-2")[0] == "running"


def test_a_stale_token_cannot_write_to_a_re_leased_chunk(grid):
    seed(grid.sessions, chunk("c-1", "running", OTHER_LEASE, runner="w2"))   # re-leased: fresh secret
    assert _submit(grid, "c-1", lease=LEASE) == (403, DENIED)
    assert chunk_row(grid.sessions, "c-1") == ("running", "w2", OTHER_LEASE)


def test_chunks_without_a_lease_token_behave_exactly_as_before(grid):
    seed(grid.sessions, chunk("old", "running", None))
    assert _submit(grid, "old") == (200, {"status": "recorded", "chunk_id": "old"})
    assert _submit(grid, "missing")[0] == 404                                 # unknown chunk: 404 first


def test_submit_reads_the_chunk_under_a_row_lock(coordinator):
    calls = []

    class Db:
        def get(self, model, ident, **kwargs):
            calls.append(kwargs)

    with pytest.raises(HTTPException):
        coordinator.submit_results(SimpleNamespace(chunk_id="c", runner_id="w", results=[]), Db(), None)
    assert calls == [{"with_for_update": True}]   # the token check and the write see the same row version


# --- allure upload -----------------------------------------------------------------------------------
def test_allure_upload_to_a_protected_chunk_needs_the_right_lease_token(grid, store):
    seed(grid.sessions, chunk("c-1", "completed", LEASE))
    target = store.allure_path(RUN, "c-1")
    assert _upload(grid, "c-1") == (403, DENIED)
    assert _upload(grid, "c-1", lease="wrong") == (403, DENIED)
    assert not target.exists()                                               # refused before storing anything
    status, body = _upload(grid, "c-1", lease=LEASE)
    assert (status, body["status"]) == (200, "stored") and target.exists()
    assert _upload(grid, "c-1", lease=LEASE)[0] == 200                       # a retry still works


def test_allure_lease_check_comes_before_the_body_is_read_or_the_state_checked(grid):
    seed(grid.sessions, chunk("c-1", "running", LEASE))
    assert _upload(grid, "c-1", data=b"not a tar") == (403, DENIED)
    assert _upload(grid, "c-1", lease=LEASE)[0] == 409                       # right token, not completed yet
    assert _upload(grid, "missing")[0] == 404


def test_allure_upload_to_a_chunk_without_a_lease_token_behaves_as_before(grid):
    seed(grid.sessions, chunk("old", "completed", None))
    assert _upload(grid, "old")[0] == 200


# --- release and clearing ----------------------------------------------------------------------------
def test_release_of_a_protected_chunk_needs_the_right_lease_token(grid):
    seed(grid.sessions, chunk("c-1", "running", LEASE))
    assert _release(grid, "c-1") == (403, DENIED)
    assert _release(grid, "c-1", lease="wrong") == (403, DENIED)
    assert chunk_row(grid.sessions, "c-1") == ("running", "w1", LEASE)
    assert _release(grid, "c-1", lease=LEASE) == (200, {"status": "released", "chunk_id": "c-1"})


def test_release_returns_the_chunk_to_pending_and_clears_the_lease_token(grid):
    seed(grid.sessions, chunk("c-1", "running", LEASE))
    _release(grid, "c-1", lease=LEASE)
    assert chunk_row(grid.sessions, "c-1") == ("pending", None, None)
    assert _release(grid, "c-1", lease=LEASE)[0] == 200                      # nothing running any more
    assert _submit(grid, "c-1", lease=LEASE)[0] == 200                       # unprotected again (as today)


def test_a_stale_pod_cannot_release_the_new_holders_chunk(grid):
    seed(grid.sessions, chunk("c-1", "running", OTHER_LEASE, runner="w2"))
    assert _release(grid, "c-1", lease=LEASE, runner="w2") == (403, DENIED)
    assert chunk_row(grid.sessions, "c-1") == ("running", "w2", OTHER_LEASE)


def test_release_with_the_right_token_but_for_another_runner_changes_nothing(grid):
    seed(grid.sessions, chunk("c-1", "running", LEASE))
    assert _release(grid, "c-1", lease=LEASE, runner="w9") == (200, {"status": "unchanged", "chunk_id": "c-1"})
    assert chunk_row(grid.sessions, "c-1") == ("running", "w1", LEASE)


def test_release_of_a_chunk_without_a_lease_token_behaves_as_before(grid):
    seed(grid.sessions, chunk("old", "running", None))
    assert _release(grid, "old") == (200, {"status": "released", "chunk_id": "old"})
    assert _release(grid, "unknown") == (200, {"status": "unchanged", "chunk_id": "unknown"})


def test_the_release_statement_itself_refuses_a_wrong_token_even_if_the_pre_check_raced(grid, monkeypatch):
    seed(grid.sessions, chunk("c-1", "running", LEASE))
    monkeypatch.setattr(leasing, "require_lease_token", lambda *a: None)     # chunk re-leased after the check
    with grid.sessions() as db:
        assert leasing.release_chunk_row(db, "c-1", "w1", OTHER_LEASE) is False
        assert leasing.release_chunk_row(db, "c-1", "w1", None) is False
    assert chunk_row(grid.sessions, "c-1") == ("running", "w1", LEASE)


def test_the_reaper_requeue_clears_the_lease_token(monkeypatch):
    executed = []

    class Db:
        def execute(self, statement, params=None):
            executed.append(" ".join(str(statement).split()))
            return SimpleNamespace(rowcount=2)

        def commit(self):
            pass

    assert services.requeue_stale_chunks(Db()) == 2
    assert "SET status = 'pending', assigned_runner = NULL, lease_token = NULL," in executed[0]


def test_every_statement_that_returns_a_chunk_to_pending_clears_the_lease_token():
    sources = [p for p in Path(services.__file__).parent.glob("*.py")]
    pending = clearing = 0
    for path in sources:
        text = path.read_text()
        pending += len(re.findall(r"SET\s+status\s*=\s*'pending'", text))
        clearing += len(re.findall(r"SET\s+status\s*=\s*'pending'(?:(?!WHERE)[^;])*?lease_token\s*=\s*NULL", text, re.S))
        assert not re.search(r"\.status\s*=\s*[\"']pending[\"']", text), f"{path.name}: ORM requeue must clear the token"
    assert pending == clearing == 2   # release endpoint + reaper


# --- secrecy -----------------------------------------------------------------------------------------
@pytest.fixture
def populated(grid):
    seed(grid.sessions, run_record(), runner_record("w1", "c-run"),
         chunk("c-pending", "pending", LEASE, runner=None), chunk("c-run", "running", LEASE),
         chunk("c-done", "completed", LEASE, results=[{"success": True, "stdout": "x"}], tests_passed=1))
    return grid


def _get_routes(coordinator):
    return {r.path for r in coordinator.app.routes if hasattr(r, "methods") and "GET" in r.methods
            and r.path.startswith("/api/")}


def test_the_lease_token_is_never_in_any_get_response(populated, coordinator, monkeypatch):
    monkeypatch.setattr(coordinator, "kube_list_pods", lambda: {"enabled": False, "error": None, "pods": {}})
    walked = {"/api/config": ["/api/config"], "/api/runs": ["/api/runs", "/api/runs?limit=200"],
              "/api/runs/{run_id}": [f"/api/runs/{RUN}"],
              "/api/chunks/{chunk_id}": [f"/api/chunks/{c}" for c in ("c-pending", "c-run", "c-done")],
              "/api/pods": ["/api/pods"], "/api/results/summary": ["/api/results/summary"],
              "/api/runs/{run_id}/report/info": [f"/api/runs/{RUN}/report/info"],
              "/api/runs/{run_id}/report": [f"/api/runs/{RUN}/report"],
              "/api/code/{repo}/{sha}": ["/api/code/demo/" + "a" * 40],
              "/api/runners/{runner_id}/view": ["/api/runners/w1/view"]}
    # The lease response carries the secret by design; every other GET route must be walked here.
    assert set(walked) | {"/api/runner/lease"} == _get_routes(coordinator), "a new GET endpoint is not walked"
    for urls in walked.values():
        for url in urls:
            path, _, query = url.partition("?")
            status, raw = call(populated.app, "GET", path, token=FULL_TOKEN, query=query)
            text = raw.decode() if isinstance(raw, bytes) else json.dumps(raw)
            assert LEASE not in text and "lease_token" not in text, url
            assert status in (200, 404), (url, status)


def test_the_walk_can_see_the_token_when_it_is_there(coordinator):
    leased = coordinator.lease_chunk(runner_id="w1", browser="chrome", lease_tokens=True, db=LeaseSession())
    assert "lease_token" in json.dumps(leased)   # control: the check above is not vacuous


def test_chunk_and_run_serialisation_lists_exactly_the_known_fields(populated):
    _, chunk_json = call(populated.app, "GET", "/api/chunks/c-done", token=FULL_TOKEN)
    assert set(chunk_json) == {"chunk_id", "run_id", "browser", "environment", "status", "tests",
                               "assigned_runner", "results"}
    _, run_json = call(populated.app, "GET", f"/api/runs/{RUN}", token=FULL_TOKEN)
    assert all("lease_token" not in c for c in run_json["chunk_details"]) and len(run_json["chunk_details"]) == 3


def test_the_lease_token_is_never_logged(grid, coordinator, caplog):
    seed(grid.sessions, chunk("c-1", "running", LEASE), chunk("c-2", "running", LEASE, runner="w2"))
    caplog.set_level(logging.DEBUG, logger="coordinator")
    _submit(grid, "c-1", lease=LEASE)
    _submit(grid, "c-1", lease="wrong-attempt")
    _upload(grid, "c-1", lease=LEASE)
    _release(grid, "c-2", lease=LEASE, runner="w2")
    issued = coordinator.lease_chunk(runner_id="w1", browser="chrome", lease_tokens=True, db=LeaseSession())
    assert issued["lease_token"] and issued["lease_token"] not in caplog.text
    assert LEASE not in caplog.text and "wrong-attempt" not in caplog.text
