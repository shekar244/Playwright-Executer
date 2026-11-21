"""The worker -> runner rename of the database (db.migrate_worker_to_runner).

A database created before the rename has table "workers" (column worker_id) and test_queue.assigned_worker.
The migration renames them in place so run history survives; it must be safe to run on every start, on a
brand-new database, and when another replica already did it. Run on SQLite, which supports the same
RENAME TABLE / RENAME COLUMN statements the PostgreSQL migration uses.
"""
import logging
from types import SimpleNamespace

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import StaticPool

import db

LEGACY_STEPS = ["table workers -> runners", "column runners.worker_id -> runner_id",
                "column test_queue.assigned_worker -> assigned_runner"]


def _engine():
    return create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)


def _legacy_database():
    """A database as the coordinator created it before the rename, with one runner and one finished chunk."""
    engine = _engine()
    db.Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE runners RENAME COLUMN runner_id TO worker_id"))
        conn.execute(text("ALTER TABLE runners RENAME TO workers"))
        conn.execute(text("ALTER TABLE test_queue RENAME COLUMN assigned_runner TO assigned_worker"))
        conn.execute(text("INSERT INTO workers (worker_id, browser, status) VALUES ('pw-chromium-1', 'chromium', 'exited')"))
        conn.execute(text(
            "INSERT INTO test_queue (chunk_id, run_id, status, browser, environment, tests, assigned_worker,"
            " tests_passed, tests_failed, tests_skipped) VALUES"
            " ('c-1', 'run-1', 'completed', 'chromium', 'qa', '[]', 'pw-chromium-1', 3, 0, 0)"))
    return engine


def _columns(engine, table):
    return {c["name"] for c in inspect(engine).get_columns(table)}


def test_a_legacy_database_is_renamed_in_place_and_keeps_its_rows():
    engine = _legacy_database()

    assert db.migrate_worker_to_runner(engine) == LEGACY_STEPS

    tables = set(inspect(engine).get_table_names())
    assert "runners" in tables and "workers" not in tables
    assert "runner_id" in _columns(engine, "runners") and "worker_id" not in _columns(engine, "runners")
    assert "assigned_runner" in _columns(engine, "test_queue") and "assigned_worker" not in _columns(engine, "test_queue")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT runner_id, status FROM runners")).all() == [("pw-chromium-1", "exited")]
        assert conn.execute(text("SELECT chunk_id, assigned_runner, tests_passed FROM test_queue")).all() == \
            [("c-1", "pw-chromium-1", 3)]


def test_the_migrated_database_works_with_the_current_models():
    engine = _legacy_database()
    db.migrate_worker_to_runner(engine)
    db.Base.metadata.create_all(engine)          # what init_db does next: must find every table already there

    with db.sessionmaker(bind=engine)() as session:
        assert session.get(db.RunnerRecord, "pw-chromium-1").status == "exited"
        assert session.get(db.TestChunkRecord, "c-1").assigned_runner == "pw-chromium-1"


def test_running_it_again_does_nothing():
    engine = _legacy_database()
    db.migrate_worker_to_runner(engine)

    assert db.migrate_worker_to_runner(engine) == []


def test_a_brand_new_database_has_nothing_to_migrate():
    engine = _engine()

    assert db.migrate_worker_to_runner(engine) == []
    db.Base.metadata.create_all(engine)
    assert db.migrate_worker_to_runner(engine) == []


def test_when_both_tables_exist_it_warns_and_leaves_the_old_one_alone(caplog):
    engine = _legacy_database()
    db.RunnerRecord.__table__.create(engine)     # what create_all() would have done had it run first

    with caplog.at_level(logging.WARNING, logger="coordinator"):
        steps = db.migrate_worker_to_runner(engine)

    assert "table workers -> runners" not in steps      # never merge or overwrite
    assert "Both 'workers' and 'runners' tables exist" in caplog.text
    assert "workers" in set(inspect(engine).get_table_names())


def test_init_db_migrates_before_it_creates_tables_and_says_so(monkeypatch, caplog):
    """create_all() first would put an empty "runners" table next to the old, populated "workers".
    The service logs warnings and up, so a one-way rename must be logged at that level to be seen."""
    order = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, statement):
            order.append("alter")

    monkeypatch.setattr(db, "migrate_worker_to_runner", lambda bind: order.append("migrate") or ["table workers -> runners"])
    monkeypatch.setattr(db.Base.metadata, "create_all", lambda bind: order.append("create_all"))
    monkeypatch.setattr(db, "engine", SimpleNamespace(begin=Connection))

    with caplog.at_level(logging.WARNING, logger="coordinator"):
        db.init_db(retries=1, delay=0)

    assert order[:2] == ["migrate", "create_all"]
    assert "Schema migration" in caplog.text and "table workers -> runners" in caplog.text


def test_a_failed_rename_that_another_replica_already_did_is_not_an_error(monkeypatch):
    engine = _legacy_database()
    real_begin, calls = engine.begin, []

    def racing_begin():
        if not calls:                            # the first statement: lose the race to another replica
            calls.append(1)
            with real_begin() as conn:
                conn.execute(text("ALTER TABLE workers RENAME TO runners"))
            return real_begin()
        return real_begin()

    monkeypatch.setattr(engine, "begin", racing_begin)

    steps = db.migrate_worker_to_runner(engine)      # the table rename now fails, but "runners" exists: carry on

    assert "table workers -> runners" not in steps
    assert "runner_id" in _columns(engine, "runners")
