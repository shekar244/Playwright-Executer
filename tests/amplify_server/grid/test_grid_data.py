"""Reading Grid runs for the dashboard: which run details may be kept for good (grid_data's cache).

A pod uploads its Allure results AFTER its chunk is reported completed, so a run reads "completed" with
has_allure=false for a moment. That moment must not be cached for ever.
"""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from amplify_server import grid_data

T0 = 1_800_000_000.0                      # the fake clock's start (epoch seconds)


def iso(epoch_s):
    return datetime.fromtimestamp(epoch_s, tz=timezone.utc).isoformat()


def chunk(updated, has_allure=False, status="completed"):
    return {"chunk_id": "r-c", "browser": "edge", "status": status, "assigned_runner": "p", "tests": ["t"],
            "passed": 1, "failed": 0, "skipped": 0, "updated_at": iso(updated), "has_allure": has_allure, "results": []}


def run(chunks, status="completed", pending=0, running=0):
    return {"run_id": "run-1", "environment": "qa", "created_at": iso(T0 - 3600), "status": status,
            "chunks": {"total": len(chunks), "pending": pending, "running": running, "completed": len(chunks)},
            "chunk_details": chunks}


@pytest.fixture
def clock(monkeypatch):
    state = SimpleNamespace(now=T0)
    monkeypatch.setattr(grid_data, "time", SimpleNamespace(monotonic=lambda: state.now, time=lambda: state.now))
    state.advance = lambda seconds: setattr(state, "now", state.now + seconds)
    return state


@pytest.fixture
def coordinator(monkeypatch, clock):
    """A fake coordinator: whatever `state.detail` is right now, and a count of detail calls."""
    grid_data.clear_cache()
    state = SimpleNamespace(detail=None, calls=0)

    def request(method, path, params=None, body=None, timeout=0):
        state.calls += 1
        return state.detail, 200

    monkeypatch.setattr(grid_data, "coordinator_request", request)
    monkeypatch.setattr(grid_data, "load_grid_config", lambda: {"coordinator_url": "http://c:8000", "api_token": ""})
    yield state
    grid_data.clear_cache()


# ── what counts as settled ────────────────────────────────────────────────────

@pytest.mark.parametrize("label, detail, settled", [
    ("every chunk has its allure results", run([chunk(T0 - 5, True), chunk(T0 - 5, True)]), True),
    ("one chunk still lacks them, just finished", run([chunk(T0 - 5, True), chunk(T0 - 5, False)]), False),
    ("none has them, just finished", run([chunk(T0 - 5)]), False),
    ("none has them, finished long ago", run([chunk(T0 - 901)]), True),
    ("none has them, finished 14 minutes ago", run([chunk(T0 - 840)]), False),
    ("newest chunk decides, not the oldest", run([chunk(T0 - 5000), chunk(T0 - 60)]), False),
    ("one chunk lacks them, all finished long ago", run([chunk(T0 - 2000, True), chunk(T0 - 1000, False)]), True),
    ("still running", run([chunk(T0 - 5000, True, "running")], status="running", running=1), False),
    ("a chunk is still pending", run([chunk(T0 - 5000, True)], pending=1), False),
    ("no chunk details and no timestamps", {"status": "completed", "chunks": {}}, False),
    ("no chunk details, run created long ago", {"status": "completed", "chunks": {}, "created_at": iso(T0 - 5000)}, True),
    ("garbage chunk entries are ignored", run([chunk(T0 - 5, True), "junk", None, 7]), True),
    ("has_allure must be a real true", run([dict(chunk(T0 - 5), has_allure="yes")]), False),
])
def test_settled(label, detail, settled):
    assert grid_data._settled(detail, now=T0) is settled, label


@pytest.mark.parametrize("junk", [None, [], "x", 5, {"chunks": [1], "status": "completed"}, {"chunk_details": "x", "status": "completed"}])
def test_settled_never_raises(junk):
    assert grid_data._settled(junk if isinstance(junk, dict) else {"status": junk}, now=T0) is False


# ── the cache ─────────────────────────────────────────────────────────────────

def test_a_run_that_gains_its_allure_results_after_completing_is_not_stuck_without_them(coordinator, clock):
    """The reported case: a poll lands between 'completed' and the Allure upload; the report button never appears."""
    coordinator.detail = run([chunk(T0 - 1, has_allure=False)])
    first, _ = grid_data.get_run("run-1")
    assert first["chunk_details"][0]["has_allure"] is False

    coordinator.detail = run([chunk(T0 - 1, has_allure=True)])       # the pod's upload finished
    clock.advance(grid_data.LIVE_TTL_SECONDS + 0.1)
    second, _ = grid_data.get_run("run-1")
    assert second["chunk_details"][0]["has_allure"] is True
    assert coordinator.calls == 2


def test_a_run_inside_the_short_ttl_is_not_read_again(coordinator, clock):
    coordinator.detail = run([chunk(T0 - 1)])
    grid_data.get_run("run-1"), grid_data.get_run("run-1")
    clock.advance(grid_data.LIVE_TTL_SECONDS - 0.5)
    grid_data.get_run("run-1")
    assert coordinator.calls == 1


def test_once_every_chunk_has_its_allure_results_the_run_is_cached_for_good(coordinator, clock):
    coordinator.detail = run([chunk(T0 - 1, True), chunk(T0 - 1, True)])
    grid_data.get_run("run-1")
    coordinator.detail = run([chunk(T0 - 1, True)])                    # would be different if it were read again
    clock.advance(10 * 24 * 3600)
    detail, _ = grid_data.get_run("run-1")
    assert len(detail["chunk_details"]) == 2 and coordinator.calls == 1


def test_a_run_whose_pods_never_upload_is_cached_for_good_after_the_grace_period(coordinator, clock):
    coordinator.detail = run([chunk(T0 - 1000)])                       # older than 900 s already, nothing uploaded
    grid_data.get_run("run-1")
    clock.advance(3600)
    grid_data.get_run("run-1")
    assert coordinator.calls == 1


def test_a_run_read_inside_the_grace_period_is_not_cached_for_good_when_the_grace_period_ends_later(coordinator, clock):
    """Settled is judged when the run is READ: an entry stored at the first poll (no allure yet) must not be
    served for ever just because, by now, the run has been finished for a long time."""
    coordinator.detail = run([chunk(T0 - 5)])
    grid_data.get_run("run-1")
    coordinator.detail = run([chunk(T0 - 5, True)])
    clock.advance(grid_data.ALLURE_GRACE_SECONDS + 60)
    detail, _ = grid_data.get_run("run-1")
    assert detail["chunk_details"][0]["has_allure"] is True and coordinator.calls == 2


def test_a_run_that_is_still_in_flight_is_read_again_after_the_ttl(coordinator, clock):
    coordinator.detail = run([chunk(T0 - 5000, True, "running")], status="running", running=1)
    grid_data.get_run("run-1")
    clock.advance(grid_data.LIVE_TTL_SECONDS + 0.1)
    grid_data.get_run("run-1")
    assert coordinator.calls == 2


def test_the_grace_period_is_15_minutes():
    assert grid_data.ALLURE_GRACE_SECONDS == 900


def test_fetch_details_serves_settled_runs_from_the_cache_and_rereads_the_others(coordinator, clock):
    coordinator.detail = run([chunk(T0 - 1, True)])
    assert set(grid_data.fetch_details(["run-1"])) == {"run-1"}
    coordinator.detail = run([chunk(T0 - 1, False)])
    clock.advance(10)
    assert grid_data.fetch_details(["run-1"])["run-1"]["chunk_details"][0]["has_allure"] is True      # cached for good
    assert coordinator.calls == 1


@pytest.mark.parametrize("junk", [None, [], "x", 5, ["a"]])
def test_an_answer_that_is_not_a_run_is_an_error_and_is_not_cached(coordinator, junk):
    coordinator.detail = junk
    detail, error = grid_data.get_run("run-1")
    assert detail is None and error
    assert grid_data._cache == {} or not any(v[1] is junk for v in grid_data._cache.values())


@pytest.mark.parametrize("junk", [None, [], "x", 5, {"runs": "x"}, {"runs": {"a": 1}}, {"runs": None}])
def test_a_run_list_that_is_not_a_list_is_empty(monkeypatch, junk):
    monkeypatch.setattr(grid_data, "coordinator_request", lambda *a, **k: (junk, 200))
    runs, status = grid_data.list_runs()
    assert runs == [] and status["reachable"] is True
