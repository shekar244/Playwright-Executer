"""Extra pytest arguments on a run: validated at the boundary, stored on the run and its chunks, handed to the runner
with the lease and shown with the run."""
import pytest
from pydantic import ValidationError

from coordinator import pytest_args as pa
from coordinator import schemas
from pod_scope_helpers import FULL_TOKEN, POD_TOKEN, call


def run_request(**kw):
    return schemas.RunRequest(tests=["tests/test_a.py"], environment="qa", **kw)


@pytest.mark.parametrize("arg", [
    {"flag": "--env", "style": "separate", "value": "qa3"},
    {"flag": "--env", "style": "joined", "value": "qa3"},
    {"flag": "-m", "style": "separate", "value": "smoke and not slow"},
    {"flag": "-c", "value": "config/pytest.ini"},                       # style defaults to separate
    {"flag": "--lf", "style": "switch"},
    {"flag": "--tracing", "style": "joined", "value": "-on"},           # joined: a leading '-' is unambiguous
])
def test_acceptable_arguments(arg):
    assert run_request(pytest_args=[arg]).pytest_args[0].flag == arg["flag"]


@pytest.mark.parametrize("arg, why", [
    ({"flag": "env", "value": "qa"}, "not a pytest option"),
    ({"flag": "--", "value": "x"}, "not a pytest option"),
    ({"flag": "-mfoo", "value": "x"}, "not a pytest option"),
    ({"flag": "--env=qa", "value": "x"}, "not a pytest option"),
    ({"flag": "--e v", "value": "x"}, "not a pytest option"),
    ({"flag": "-p", "value": "no:gridtest.results_plugin"}, "set by the Grid runner"),
    ({"flag": "--alluredir", "value": "/tmp/x"}, "set by the Grid runner"),
    ({"flag": "-o", "value": "cache_dir=/app"}, "set by the Grid runner"),
    ({"flag": "-n", "value": "64"}, "set by the Grid runner"),
    ({"flag": "--headed", "style": "switch"}, "set by the Grid runner"),
    ({"flag": "--env", "style": "weird", "value": "x"}, "style must be"),
    ({"flag": "--lf", "style": "switch", "value": "tests/other.py"}, "takes no value"),
    ({"flag": "-m", "style": "joined", "value": "smoke"}, "short option"),
    ({"flag": "--env", "value": None}, "value of --env"),
    ({"flag": "--env", "value": ""}, "value of --env"),
    ({"flag": "--env", "value": "x" * 201}, "value of --env"),
    ({"flag": "--env", "value": "qa\n--lf"}, "value of --env"),
    ({"flag": "--env", "value": "--collect-only"}, "cannot start with '-'"),
])
def test_unacceptable_arguments_are_refused(arg, why):
    with pytest.raises(ValidationError, match=why):
        run_request(pytest_args=[arg])


def test_an_option_may_be_given_once_and_the_list_is_bounded():
    with pytest.raises(ValidationError, match="once"):
        run_request(pytest_args=[{"flag": "--env", "value": "a"}, {"flag": "--env", "value": "b"}])
    with pytest.raises(ValidationError, match="at most"):
        run_request(pytest_args=[{"flag": f"--o{i}", "style": "switch"} for i in range(pa.MAX_ARGS + 1)])


def test_without_arguments_nothing_changes():
    assert run_request().pytest_args == []


# ── through the API ───────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def tokens(monkeypatch):
    monkeypatch.setattr(schemas, "API_TOKEN", FULL_TOKEN)
    monkeypatch.setattr(schemas, "POD_API_TOKEN", POD_TOKEN)


ARGS = [{"flag": "--env", "style": "separate", "value": "qa3"}, {"flag": "--lf", "style": "switch", "value": None}]


def create(grid, args=ARGS):
    import json
    body = json.dumps({"tests": ["tests/test_a.py", "tests/test_b.py"], "environment": "qa", "browsers": ["chromium"],
                       "chunk_size": 1, "requested_by": "shekar", "pytest_args": args}).encode()
    return call(grid.app, "POST", "/api/runs", token=FULL_TOKEN, body=body, extra_headers={"Content-Type": "application/json"})


def test_a_run_keeps_its_arguments_and_shows_them(grid, monkeypatch):
    status, created = create(grid)

    assert status == 200 and created["pytest_args"] == ARGS
    _, run = call(grid.app, "GET", f"/api/runs/{created['run_id']}", token=FULL_TOKEN)
    assert run["pytest_args"] == ARGS
    _, runs = call(grid.app, "GET", "/api/runs", token=FULL_TOKEN)
    assert runs["runs"][0]["pytest_args"] == ARGS


def test_every_chunk_carries_the_runs_arguments(grid):
    from coordinator.db import TestChunkRecord
    _, created = create(grid)

    with grid.sessions() as db:
        chunks = db.query(TestChunkRecord).filter(TestChunkRecord.run_id == created["run_id"]).all()
        assert len(chunks) == 2 and all(c.pytest_args == ARGS for c in chunks)


def test_a_bad_argument_is_a_422_and_creates_nothing(grid):
    status, _ = create(grid, [{"flag": "-p", "value": "evil"}])

    assert status == 422
    assert call(grid.app, "GET", "/api/runs", token=FULL_TOKEN)[1] == {"runs": []}


def test_a_run_without_arguments_answers_an_empty_list(grid):
    status, created = create(grid, [])

    assert status == 200 and created["pytest_args"] == []


def test_the_lease_hands_the_runner_the_arguments(coordinator):
    from types import SimpleNamespace
    row = SimpleNamespace(chunk_id="c1", tests=["t.py"], run_id="r", environment="qa", browser="chromium", repo=None,
                          sha=None, lease_token=None, pytest_args=ARGS)

    class Db:
        def commit(self):
            pass

    import coordinator.coordinator as module
    leased = None
    original = module.lease_next_chunk
    try:
        module.lease_next_chunk = lambda *a, **k: row
        module.touch_runner = (lambda *a, **k: None)
        leased = module.lease_chunk(runner_id="w1", browser="chromium", lease_tokens=False, db=Db())
    finally:
        module.lease_next_chunk = original
    assert leased["pytest_args"] == ARGS


def test_the_feature_is_announced():
    from coordinator import config
    assert "pytest-args" in config.FEATURES
