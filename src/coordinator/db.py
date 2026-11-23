"""SQLAlchemy engine, models, and schema bootstrap."""
import logging
import time
from datetime import datetime, timezone

from sqlalchemy import JSON, Column, DateTime, Integer, String, create_engine, inspect, text
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import declarative_base, sessionmaker

from .config import DATABASE_URL, DEFAULT_BROWSER, DEFAULT_ENVIRONMENT

log = logging.getLogger("coordinator")

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --- Database Models -------------------------------------------------------------
class TestRunRecord(Base):
    __tablename__ = "test_runs"

    run_id = Column(String, primary_key=True)
    environment = Column(String, nullable=False)
    browsers = Column(JSON, nullable=False)
    tests = Column(JSON, nullable=False)
    chunk_size = Column(Integer, nullable=False)
    requested_by = Column(String, nullable=True)
    repo = Column(String, nullable=True)  # code snapshot the run's tests execute from
    sha = Column(String, nullable=True)
    pytest_args = Column(JSON, nullable=True)  # [{"flag", "style", "value"}]: extra pytest options of the run
    created_at = Column(DateTime(timezone=True), default=_utcnow, index=True)


class TestChunkRecord(Base):
    __tablename__ = "test_queue"

    chunk_id = Column(String, primary_key=True, index=True)
    run_id = Column(String, nullable=True, index=True)
    status = Column(String, default="pending", index=True)  # pending, running, completed
    browser = Column(String, nullable=False, default=DEFAULT_BROWSER, index=True)
    environment = Column(String, nullable=False, default=DEFAULT_ENVIRONMENT)
    tests = Column(JSON, nullable=False)
    assigned_runner = Column(String, nullable=True)
    results = Column(JSON, nullable=True)
    tests_passed = Column(Integer, nullable=False, default=0)
    tests_failed = Column(Integer, nullable=False, default=0)
    tests_skipped = Column(Integer, nullable=False, default=0)
    repo = Column(String, nullable=True)
    sha = Column(String, nullable=True)
    # Per-lease secret (see leasing.py): set at lease time, kept after completion, NULL while the
    # chunk is pending. Never serialised in any response except the lease response.
    lease_token = Column(String, nullable=True)
    pytest_args = Column(JSON, nullable=True)  # copied from the run: what the lease hands the runner
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class RunnerRecord(Base):
    __tablename__ = "runners"

    runner_id = Column(String, primary_key=True)  # == pod name (hostname)
    browser = Column(String, nullable=True)
    status = Column(String, nullable=False, default="idle")  # idle, busy, exited
    current_chunk = Column(String, nullable=True)
    run_id = Column(String, nullable=True)
    environment = Column(String, nullable=True)
    started_at = Column(DateTime(timezone=True), default=_utcnow)
    last_seen = Column(DateTime(timezone=True), default=_utcnow)
    # Live view (headed browser on a virtual display, served by x11vnc on LIVE_VIEW_PORT): the pod's address as
    # the coordinator saw it when the pod registered, and the VNC password. Never set from what the pod claims.
    view_host = Column(String, nullable=True)
    view_secret = Column(String, nullable=True)


# Columns added after the first release; create_all() never alters existing tables.
_MIGRATIONS = [
    "ALTER TABLE test_queue ADD COLUMN IF NOT EXISTS browser VARCHAR NOT NULL DEFAULT 'edge'",
    "ALTER TABLE test_queue ADD COLUMN IF NOT EXISTS run_id VARCHAR",
    "ALTER TABLE test_queue ADD COLUMN IF NOT EXISTS environment VARCHAR NOT NULL DEFAULT 'qa'",
    "ALTER TABLE test_queue ADD COLUMN IF NOT EXISTS tests_passed INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE test_queue ADD COLUMN IF NOT EXISTS tests_failed INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE test_queue ADD COLUMN IF NOT EXISTS tests_skipped INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE test_runs ADD COLUMN IF NOT EXISTS repo VARCHAR",
    "ALTER TABLE test_runs ADD COLUMN IF NOT EXISTS sha VARCHAR",
    "ALTER TABLE test_queue ADD COLUMN IF NOT EXISTS repo VARCHAR",
    "ALTER TABLE test_queue ADD COLUMN IF NOT EXISTS sha VARCHAR",
    "ALTER TABLE test_queue ADD COLUMN IF NOT EXISTS lease_token VARCHAR",
    "ALTER TABLE test_runs ADD COLUMN IF NOT EXISTS pytest_args JSON",
    "ALTER TABLE test_queue ADD COLUMN IF NOT EXISTS pytest_args JSON",
    "ALTER TABLE runners ADD COLUMN IF NOT EXISTS view_host VARCHAR",
    "ALTER TABLE runners ADD COLUMN IF NOT EXISTS view_secret VARCHAR",
]


# The pods that execute chunks used to be called "workers". Databases created under that name are renamed in
# place (data, primary keys and indexes carry over), once; later starts find nothing to do. It must run BEFORE
# create_all(), which would otherwise create empty "runners" tables next to the old, populated ones.
# One-way: a coordinator that still says "workers" cannot read a migrated database.
_LEGACY_TABLE = ("workers", "runners")
_LEGACY_COLUMNS = [  # (table, old column, new column), applied after the table rename
    ("runners", "worker_id", "runner_id"),
    ("test_queue", "assigned_worker", "assigned_runner"),
]


def migrate_worker_to_runner(bind) -> list[str]:
    """Rename the legacy "worker" schema to "runner". Returns the steps taken: [] on a current or empty database."""
    steps: list[str] = []
    old_table, new_table = _LEGACY_TABLE

    def apply(statement: str, step: str, already_done) -> None:
        try:
            with bind.begin() as conn:
                conn.execute(text(statement))
        except (ProgrammingError, OperationalError):
            if not already_done():       # another coordinator replica renamed it first: fine; anything else is real
                raise
        else:
            steps.append(step)

    tables = set(inspect(bind).get_table_names())
    if old_table in tables and new_table in tables:
        log.warning("Both '%s' and '%s' tables exist; leaving '%s' untouched", old_table, new_table, old_table)
    elif old_table in tables:
        apply(f"ALTER TABLE {old_table} RENAME TO {new_table}", f"table {old_table} -> {new_table}",
              lambda: new_table in set(inspect(bind).get_table_names()))
    for table, old, new in _LEGACY_COLUMNS:
        inspector = inspect(bind)
        if table not in inspector.get_table_names():
            continue
        columns = {c["name"] for c in inspector.get_columns(table)}
        if old in columns and new not in columns:
            apply(f"ALTER TABLE {table} RENAME COLUMN {old} TO {new}", f"column {table}.{old} -> {new}",
                  lambda t=table, n=new: n in {c["name"] for c in inspect(bind).get_columns(t)})
    return steps


def init_db(retries: int = 30, delay: float = 2.0) -> None:
    """Create/upgrade tables, waiting for PostgreSQL to accept connections on cold start."""
    for attempt in range(1, retries + 1):
        try:
            for step in migrate_worker_to_runner(engine):
                log.warning("Schema migration (worker -> runner rename, one-time): %s", step)   # visible: the service logs warnings and up
            Base.metadata.create_all(bind=engine)
            with engine.begin() as conn:
                for stmt in _MIGRATIONS:
                    conn.execute(text(stmt))
            return
        except OperationalError:
            if attempt == retries:
                raise
            log.warning("Database not ready (attempt %d/%d); retrying in %ss", attempt, retries, delay)
            time.sleep(delay)



def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
