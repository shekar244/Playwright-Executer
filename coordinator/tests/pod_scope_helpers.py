"""Shared builders for the pod-token / lease-token tests: an in-memory SQLite database that stands in
for PostgreSQL (the ORM endpoints and the release statement run on it unchanged) and a minimal ASGI
driver, so requests go through the real FastAPI routes, dependencies and header parsing without
httpx or a server.

The lease and reaper statements use PostgreSQL-only SQL (FOR UPDATE SKIP LOCKED, make_interval);
those are covered by recording sessions here and by test_lease_db.py against a real database.
"""
import asyncio
import json
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm.attributes import set_committed_value
from sqlalchemy.pool import StaticPool

from db import Base, TestChunkRecord, TestRunRecord, RunnerRecord

FULL_TOKEN, POD_TOKEN = "full-xxx", "pod-yyy"
LEASE = "c0ffee00c0ffee00c0ffee00c0ffee00"   # a lease secret that must never leak
OTHER_LEASE = "deadbeefdeadbeefdeadbeefdeadbeef"
RUN = "run-20260101-000000-abc123"
SHA = "a" * 40


@event.listens_for(RunnerRecord, "load")
def _aware_timestamps(runner, _context):
    """SQLite hands back naive datetimes; PostgreSQL's timestamptz columns are aware (grid_state needs that)."""
    for name in ("started_at", "last_seen"):
        value = getattr(runner, name)
        if value is not None and value.tzinfo is None:
            set_committed_value(runner, name, value.replace(tzinfo=timezone.utc))


def sqlite_sessions() -> sessionmaker:
    """Fresh schema per call. NOW() is registered so the release statement runs on SQLite."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def _now(dbapi_connection, _record):
        dbapi_connection.create_function("NOW", 0, lambda: datetime.now(timezone.utc).isoformat(sep=" "))

    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False)


def chunk(chunk_id: str, status: str, token: Optional[str], runner: Optional[str] = "w1", **kw):
    return TestChunkRecord(chunk_id=chunk_id, run_id=kw.pop("run_id", RUN), status=status, browser="chrome",
                           environment="qa", tests=["tests/test_a.py"], assigned_runner=runner,
                           lease_token=token, **kw)


def seed(sessions: sessionmaker, *records) -> None:
    with sessions() as db:
        db.add_all(records)
        db.commit()


def run_record() -> TestRunRecord:
    return TestRunRecord(run_id=RUN, environment="qa", browsers=["chrome"], tests=["tests/test_a.py"],
                         chunk_size=3)


def runner_record(runner_id: str = "w1", chunk_id: Optional[str] = None) -> RunnerRecord:
    return RunnerRecord(runner_id=runner_id, browser="chrome", status="busy", current_chunk=chunk_id)


def chunk_row(sessions: sessionmaker, chunk_id: str) -> Tuple[str, Optional[str], Optional[str]]:
    """(status, assigned_runner, lease_token) as stored."""
    with sessions() as db:
        c = db.get(TestChunkRecord, chunk_id)
        return c.status, c.assigned_runner, c.lease_token


async def _asgi(app, method: str, path: str, headers: Dict[str, str], body: bytes, query: str,
                client: Tuple[str, int] = ("test", 1)):
    sent, pending = [], [{"type": "http.request", "body": body, "more_body": False}]
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    if body:
        raw.append((b"content-length", str(len(body)).encode()))
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
             "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": query.encode(),
             "headers": raw, "client": client, "server": ("test", 80), "root_path": ""}

    async def receive():
        return pending.pop(0) if pending else {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    return status, b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")


def call(app, method: str, path: str, token: Optional[str] = None, lease: Optional[str] = None,
         query: str = "", body: bytes = b"", extra_headers: Optional[Dict[str, str]] = None,
         client: Tuple[str, int] = ("test", 1)):
    """One request through the whole ASGI stack; returns (status, parsed JSON or raw bytes)."""
    headers = dict(extra_headers or {})
    if token is not None:
        headers["X-API-Token"] = token
    if lease is not None:
        headers["X-Lease-Token"] = lease
    status, raw = asyncio.run(_asgi(app, method, path, headers, body, query, client))
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, raw
