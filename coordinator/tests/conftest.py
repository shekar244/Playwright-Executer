import importlib
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# Pure-logic tests: the engine is created lazily, so no database is contacted.
os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost:5432/unused")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from artifacts import ArtifactStore, Settings  # noqa: E402  (needs the sys.path entry above)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(code_dir=tmp_path / "code", allure_dir=tmp_path / "allure",
                    report_dir=tmp_path / "reports", max_code_bytes=2000, max_allure_bytes=20000,
                    max_allure_unpacked_bytes=100000, max_allure_files=50, report_timeout_seconds=20)


@pytest.fixture
def store(settings) -> ArtifactStore:
    return ArtifactStore(settings)


@pytest.fixture(scope="module")
def coordinator():
    """coordinator.py runs init_db() at import: import it once with that single call stubbed out."""
    import db
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(db, "init_db", lambda *a, **k: None)
        return importlib.import_module("coordinator")


@pytest.fixture
def grid(coordinator, store, settings, monkeypatch):
    """The real app (routes, dependencies, header parsing) over in-memory SQLite and tmp artifact dirs."""
    import artifacts
    import db
    from pod_scope_helpers import sqlite_sessions

    sessions = sqlite_sessions()

    def override_get_db():
        with sessions() as session:
            yield session

    coordinator.app.dependency_overrides[db.get_db] = override_get_db
    monkeypatch.setattr(coordinator, "SessionLocal", sessions)
    monkeypatch.setattr(coordinator, "store", store)
    monkeypatch.setattr(coordinator, "reports", artifacts.ReportService(settings, store))
    yield SimpleNamespace(app=coordinator.app, sessions=sessions)
    coordinator.app.dependency_overrides.clear()
