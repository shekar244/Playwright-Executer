"""Cancelling a run (POST /api/runs/{run_id}/cancel).

Queued and running chunks become cancelled, finished chunks keep their results, a runner that is mid-chunk has its
results refused with the 403 a runner already understands as "this pod no longer holds the chunk", and only the full
token may cancel. Requests go through the real app over in-memory SQLite (pod_scope_helpers).
"""
import json

import pytest

import schemas
from db import TestChunkRecord
from pod_scope_helpers import FULL_TOKEN, POD_TOKEN, RUN, call, chunk, chunk_row, run_record, seed

JSON = {"Content-Type": "application/json"}
LEASE = "c0ffee00c0ffee00c0ffee00c0ffee00"


@pytest.fixture(autouse=True)
def tokens(monkeypatch):
    monkeypatch.setattr(schemas, "API_TOKEN", FULL_TOKEN)
    monkeypatch.setattr(schemas, "POD_API_TOKEN", POD_TOKEN)


def cancel(grid, run=RUN, token=FULL_TOKEN):
    return call(grid.app, "POST", f"/api/runs/{run}/cancel", token=token)


def submit(grid, chunk_id, lease=None, runner="w1"):
    body = json.dumps({"chunk_id": chunk_id, "runner_id": runner, "results": [{"success": True}]}).encode()
    return call(grid.app, "POST", "/api/results/submit", token=POD_TOKEN, lease=lease, body=body, extra_headers=JSON)


def test_a_queued_run_is_cancelled_and_says_so(grid):
    seed(grid.sessions, run_record(), chunk("c-1", "pending", None, runner=None), chunk("c-2", "pending", None, runner=None))

    status, body = cancel(grid)

    assert status == 200 and body["cancelled_chunks"] == 2
    assert body["status"] == "cancelled" and body["chunks"] == {"total": 2, "pending": 0, "running": 0, "completed": 0, "cancelled": 2}
    assert chunk_row(grid.sessions, "c-1")[0] == chunk_row(grid.sessions, "c-2")[0] == "cancelled"


def test_finished_chunks_keep_their_results_and_the_run_still_reports_them(grid):
    seed(grid.sessions, run_record(), chunk("c-done", "completed", LEASE, tests_passed=2, tests_failed=1),
         chunk("c-wait", "pending", None, runner=None))

    status, body = cancel(grid)

    assert status == 200 and body["cancelled_chunks"] == 1 and body["status"] == "cancelled"
    assert (body["passed"], body["failed"]) == (2, 1) and body["outcome"] == "failed"
    assert chunk_row(grid.sessions, "c-done")[0] == "completed"


def test_a_running_chunk_is_cancelled_too_and_the_pod_that_runs_it_cannot_record_results(grid):
    seed(grid.sessions, run_record(), chunk("c-1", "running", LEASE))

    assert cancel(grid)[1]["cancelled_chunks"] == 1

    assert submit(grid, "c-1", lease=LEASE) == (403, {"detail": "This chunk's run was cancelled"})
    assert chunk_row(grid.sessions, "c-1")[0] == "cancelled"      # still cancelled: nothing was written
    with grid.sessions() as db:
        assert db.get(TestChunkRecord, "c-1").results is None


def test_a_chunk_without_a_lease_token_cannot_be_completed_after_a_cancel_either(grid):
    """A NULL token means "unprotected" to the token check, so the cancelled check has to come first."""
    seed(grid.sessions, run_record(), chunk("c-1", "running", None))
    cancel(grid)

    assert submit(grid, "c-1")[0] == 403 and chunk_row(grid.sessions, "c-1")[0] == "cancelled"


def test_cancelling_twice_or_cancelling_a_finished_run_changes_nothing(grid):
    seed(grid.sessions, run_record(), chunk("c-1", "pending", None, runner=None))
    first = cancel(grid)[1]

    again = cancel(grid)[1]

    assert again["cancelled_chunks"] == 0 and again["status"] == first["status"] == "cancelled"


def test_a_completed_run_stays_completed(grid):
    seed(grid.sessions, run_record(), chunk("c-1", "completed", LEASE, tests_passed=1))

    status, body = cancel(grid)

    assert status == 200 and body["cancelled_chunks"] == 0 and body["status"] == "completed"
    assert chunk_row(grid.sessions, "c-1")[0] == "completed"


def test_only_the_runs_own_chunks_are_cancelled(grid):
    other = "run-20260101-000000-zzz999"
    seed(grid.sessions, run_record(), chunk("c-1", "pending", None, runner=None),
         chunk("o-1", "pending", None, runner=None, run_id=other))

    cancel(grid)

    assert chunk_row(grid.sessions, "o-1")[0] == "pending"


def test_an_unknown_run_is_a_404(grid):
    assert cancel(grid, run="run-nope") == (404, {"detail": "Run not found"})


def test_a_pod_token_cannot_cancel_and_neither_can_an_anonymous_caller(grid):
    seed(grid.sessions, run_record(), chunk("c-1", "pending", None, runner=None))

    assert cancel(grid, token=POD_TOKEN)[0] == 403
    assert cancel(grid, token=None)[0] == 401
    assert cancel(grid, token="wrong")[0] == 401
    assert chunk_row(grid.sessions, "c-1")[0] == "pending"


def test_the_run_list_and_detail_show_the_cancelled_status(grid):
    seed(grid.sessions, run_record(), chunk("c-1", "pending", None, runner=None))
    cancel(grid)

    listed = call(grid.app, "GET", "/api/runs", token=FULL_TOKEN)[1]["runs"][0]
    detail = call(grid.app, "GET", f"/api/runs/{RUN}", token=FULL_TOKEN)[1]

    assert listed["status"] == detail["status"] == "cancelled" and listed["outcome"] is None
    assert [c["status"] for c in detail["chunk_details"]] == ["cancelled"]


def test_the_summary_counts_cancelled_chunks_apart_from_pending_ones(grid):
    seed(grid.sessions, run_record(), chunk("c-1", "pending", None, runner=None), chunk("c-2", "pending", None, runner=None))
    cancel(grid)

    summary = call(grid.app, "GET", "/api/results/summary", token=FULL_TOKEN)[1]

    assert (summary["pending_chunks"], summary["cancelled_chunks"]) == (0, 2) and summary["pending_by_browser"] == {}
