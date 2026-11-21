"""Shared contract with Amplify: GET /api/config lists "features"; POST /api/runs echoes repo and sha.

coordinator.py runs init_db() at import, so conftest.py imports it with that one call stubbed out (the
`coordinator` fixture); the endpoint functions are then called directly with a recording stand-in for
the database session.
"""
import config
from db import TestChunkRecord, TestRunRecord
from schemas import RunRequest

SHA = "b" * 40


class RecordingSession:
    def __init__(self):
        self.added, self.commits = [], 0

    def add(self, record):
        self.added.append(record)

    def commit(self):
        self.commits += 1


def test_config_lists_the_features_clients_can_probe(coordinator):
    features = coordinator.get_config()["features"]
    assert features == ["code-delivery", "allure-reports", "lease-tokens", "pod-token"]
    assert isinstance(features, list) and all(isinstance(f, str) for f in features)


def test_config_keeps_its_existing_keys_and_hands_out_a_copy(coordinator):
    first = coordinator.get_config()
    assert {"browsers", "environments", "default_browser", "default_environment",
            "default_chunk_size"} <= first.keys()
    first["features"].append("tampered")
    assert coordinator.get_config()["features"] == config.FEATURES
    assert {"lease-tokens", "pod-token"} <= set(config.FEATURES)


def test_create_run_echoes_repo_and_sha_and_stores_them(coordinator):
    db = RecordingSession()
    request = RunRequest(tests=["tests/test_a.py", "tests/test_b.py"], repo="demo-repo", sha=SHA, browsers=["chrome"])

    response = coordinator.create_run(request, db)

    assert response["repo"] == "demo-repo" and response["sha"] == SHA
    assert response["chunks_queued"] == 1 and response["total_tests"] == 2 and response["browsers"] == ["chrome"]
    assert response["run_id"].startswith("run-") and db.commits == 1
    run = next(r for r in db.added if isinstance(r, TestRunRecord))
    chunks = [r for r in db.added if isinstance(r, TestChunkRecord)]
    assert (run.repo, run.sha) == ("demo-repo", SHA)
    assert [(c.repo, c.sha) for c in chunks] == [("demo-repo", SHA)]


def test_create_run_without_a_code_reference_answers_null_not_a_missing_key(coordinator):
    response = coordinator.create_run(RunRequest(tests=["tests/test_a.py"]), RecordingSession())
    assert "repo" in response and "sha" in response
    assert response["repo"] is None and response["sha"] is None
