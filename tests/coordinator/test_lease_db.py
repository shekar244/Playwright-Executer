"""The lease / release / reaper statements and the migration against a real PostgreSQL.

Skipped unless GRID_TEST_DATABASE_URL points at a SCRATCH database, e.g.
  GRID_TEST_DATABASE_URL=postgresql://postgres:<pw>@127.0.0.1:55433/grid_pod_scope pytest tests/test_lease_db.py
The tests create and delete their own rows ("pgt-" ids) and refuse to run when the queue already
holds pending chunks, so a database that is in real use is left alone.
"""
import os
import re

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from coordinator import db
from coordinator import leasing
from coordinator import services
from coordinator.db import TestChunkRecord, RunnerRecord

URL = os.environ.get("GRID_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set GRID_TEST_DATABASE_URL to a scratch PostgreSQL database")
BROWSER = "chrome"
HEX32 = re.compile(r"[0-9a-f]{32}")


@pytest.fixture
def engine():
    engine = create_engine(URL)
    db.Base.metadata.create_all(engine)
    with engine.begin() as conn:
        for statement in db._MIGRATIONS:
            conn.execute(text(statement))
    yield engine
    engine.dispose()


@pytest.fixture
def sessions(engine):
    maker = sessionmaker(bind=engine)
    with maker() as s:
        if s.query(TestChunkRecord).filter(TestChunkRecord.status == "pending").count():
            pytest.skip("the queue holds pending chunks: not a scratch database")
    yield maker
    with maker() as s:
        s.query(TestChunkRecord).filter(TestChunkRecord.chunk_id.like("pgt-%")).delete(synchronize_session=False)
        s.query(RunnerRecord).filter(RunnerRecord.runner_id.like("pgt-%")).delete(synchronize_session=False)
        s.commit()


def _queue(s, chunk_id="pgt-1"):
    s.add(TestChunkRecord(chunk_id=chunk_id, status="pending", browser=BROWSER, environment="qa", tests=["t.py"]))
    s.commit()


def _row(s, chunk_id="pgt-1"):
    s.expire_all()
    c = s.get(TestChunkRecord, chunk_id)
    return c.status, c.assigned_runner, c.lease_token


def test_a_lease_with_lease_tokens_stores_and_returns_a_fresh_secret(sessions):
    with sessions() as s:
        _queue(s)
        row = leasing.lease_next_chunk(s, "pgt-w1", BROWSER, True)
        s.commit()
        assert row.chunk_id == "pgt-1" and HEX32.fullmatch(row.lease_token)
        assert _row(s) == ("running", "pgt-w1", row.lease_token)


def test_a_lease_without_lease_tokens_leaves_the_token_null(sessions):
    with sessions() as s:
        _queue(s)
        row = leasing.lease_next_chunk(s, "pgt-w1", BROWSER, False)
        s.commit()
        assert row.lease_token is None and _row(s) == ("running", "pgt-w1", None)


def test_an_empty_queue_leases_nothing(sessions):
    with sessions() as s:
        assert leasing.lease_next_chunk(s, "pgt-w1", BROWSER, True) is None


def test_release_clears_the_token_and_a_re_lease_generates_a_fresh_one(sessions):
    with sessions() as s:
        _queue(s)
        first = leasing.lease_next_chunk(s, "pgt-w1", BROWSER, True).lease_token
        s.commit()
        with pytest.raises(HTTPException) as exc:
            leasing.release_chunk_row(s, "pgt-1", "pgt-w1", "wrong")
        assert exc.value.status_code == 403 and _row(s)[0] == "running"
        assert leasing.release_chunk_row(s, "pgt-1", "pgt-w1", first) is True
        assert _row(s) == ("pending", None, None)
        second = leasing.lease_next_chunk(s, "pgt-w2", BROWSER, True).lease_token
        s.commit()
        assert HEX32.fullmatch(second) and second != first
        with pytest.raises(HTTPException):
            leasing.release_chunk_row(s, "pgt-1", "pgt-w2", first)   # the previous holder's secret is dead


def test_the_reaper_requeue_clears_the_token(sessions):
    with sessions() as s:
        _queue(s)
        leasing.lease_next_chunk(s, "pgt-dead", BROWSER, True)
        s.execute(text("UPDATE test_queue SET updated_at = NOW() - interval '1 hour' WHERE chunk_id = 'pgt-1'"))
        s.commit()
        assert services.requeue_stale_chunks(s) >= 1
        assert _row(s) == ("pending", None, None)


def test_the_reaper_leaves_a_live_runners_chunk_and_its_token_alone(sessions):
    with sessions() as s:
        _queue(s)
        token = leasing.lease_next_chunk(s, "pgt-live", BROWSER, True).lease_token
        s.add(RunnerRecord(runner_id="pgt-live", browser=BROWSER, status="busy", current_chunk="pgt-1"))
        s.execute(text("UPDATE test_queue SET updated_at = NOW() - interval '1 hour' WHERE chunk_id = 'pgt-1'"))
        s.commit()
        services.requeue_stale_chunks(s)
        assert _row(s) == ("running", "pgt-live", token)


def test_the_endpoints_enforce_the_token_end_to_end(sessions, coordinator):
    from coordinator.schemas import TestResultSubmission
    with sessions() as s:
        _queue(s)
        leased = coordinator.lease_chunk(runner_id="pgt-w1", browser=BROWSER, lease_tokens=True, db=s)
        token = leased["lease_token"]
        payload = TestResultSubmission(chunk_id="pgt-1", runner_id="pgt-w1", results=[{"success": True}])
        with pytest.raises(HTTPException) as exc:
            coordinator.submit_results(payload, s, None)
        assert exc.value.status_code == 403 and exc.value.detail == "Invalid lease token for this chunk"
        s.rollback()
        assert coordinator.submit_results(payload, s, token) == {"status": "recorded", "chunk_id": "pgt-1"}
        assert coordinator.submit_results(payload, s, token)["status"] == "recorded"   # retry
        assert _row(s) == ("completed", "pgt-w1", token)                               # kept after completion
        assert coordinator.get_chunk("pgt-1", s)["status"] == "completed"
        assert token not in str(coordinator.get_chunk("pgt-1", s))


def test_the_migration_adds_the_nullable_column_to_an_existing_table_and_is_idempotent(engine):
    statement = [m for m in db._MIGRATIONS if "lease_token" in m]
    assert len(statement) == 1 and "ADD COLUMN IF NOT EXISTS lease_token VARCHAR" in statement[0]
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS pgt_mig CASCADE"))
        conn.execute(text("CREATE SCHEMA pgt_mig"))
    try:
        with engine.begin() as conn:
            conn.execute(text("SET LOCAL search_path TO pgt_mig"))
            conn.execute(text("CREATE TABLE test_queue (chunk_id VARCHAR PRIMARY KEY)"))
            conn.execute(text(statement[0]))
            conn.execute(text(statement[0]))
            column = conn.execute(text(
                "SELECT data_type, is_nullable FROM information_schema.columns "
                "WHERE table_schema = 'pgt_mig' AND table_name = 'test_queue' AND column_name = 'lease_token'"
            )).one()
        assert tuple(column) == ("character varying", "YES")
    finally:
        with engine.begin() as conn:
            conn.execute(text("DROP SCHEMA IF EXISTS pgt_mig CASCADE"))

def test_a_cancelled_chunk_is_never_leased_and_a_release_does_not_bring_it_back(sessions):
    with sessions() as s:
        _queue(s)
        _queue(s, "pgt-2")
        s.query(TestChunkRecord).filter(TestChunkRecord.chunk_id == "pgt-1").update({"status": "cancelled"})
        s.commit()

        row = leasing.lease_next_chunk(s, "pgt-w1", BROWSER, True)
        s.commit()
        assert row.chunk_id == "pgt-2"                                      # the cancelled one was skipped
        assert leasing.lease_next_chunk(s, "pgt-w2", BROWSER, True) is None   # and nothing else is queued
        assert leasing.release_chunk_row(s, "pgt-1", "pgt-w1", None) is False
        assert _row(s, "pgt-1")[0] == "cancelled"


def test_a_lease_returns_the_chunks_pytest_arguments_from_postgresql(sessions):
    args = [{"flag": "--env", "style": "separate", "value": "qa3"}, {"flag": "--lf", "style": "switch", "value": None}]
    with sessions() as s:
        s.add(TestChunkRecord(chunk_id="pgt-args", status="pending", browser=BROWSER, environment="qa3", tests=["t.py"],
                              pytest_args=args))
        s.commit()
        row = leasing.lease_next_chunk(s, "pgt-w1", BROWSER, True)
        s.commit()
        assert row.chunk_id == "pgt-args" and row.pytest_args == args      # JSON comes back as a list, not text
