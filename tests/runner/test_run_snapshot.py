"""run_assigned_tests and poll_and_execute with the REAL pytest against a tiny snapshot in a tmp path."""
import io
import json
import os
import stat
import tarfile

import pytest

from runner_fakes import REPO, SHA, make_tar

SNAPSHOT = {
    "tests/test_snapshot_only.py": b'''import os


def test_passes():
    assert 1 + 1 == 2


def test_fails():
    assert "snapshot" == "runner"


def test_child_env():
    assert "GRID_API_TOKEN" not in os.environ
    assert "COORDINATOR_URL" not in os.environ
    assert os.environ["GRID_POD"] == "pod-test-1"
    assert os.environ["TEST_ENV"] == "qa"
    assert os.environ["BROWSER"] == "chromium"
''',
}
IDS = [f"tests/test_snapshot_only.py::{n}" for n in ("test_passes", "test_fails", "test_child_env")]


def _write_volume_snapshot(root, files=SNAPSHOT, read_only=True):
    snap = root / REPO / SHA
    for name, content in files.items():
        path = snap / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    if read_only:  # like the shared read-only volume
        for path in [snap, *snap.rglob("*")]:
            path.chmod(path.stat().st_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
    return snap


@pytest.fixture
def writable_on_cleanup(tmp_path):
    yield
    for path in [tmp_path, *tmp_path.rglob("*")]:
        if not path.is_symlink():
            path.chmod(path.stat().st_mode | stat.S_IWUSR)


def _outcomes(chunk):
    return {r["nodeid"]: r["outcome"] for r in chunk["test_results"]}


def _allure_results(directory):
    return [json.loads(p.read_text()) for p in sorted(directory.glob("*-result.json"))]


def test_chunk_runs_the_snapshot_tests_and_labels_the_allure_results(runner, tmp_path, writable_on_cleanup):
    snap = _write_volume_snapshot(tmp_path / "code-root")
    allure_dir = tmp_path / "allure"

    (chunk,) = runner.run_assigned_tests(IDS, "qa", REPO, SHA, allure_dir=str(allure_dir))

    # the snapshot's tests ran (not the runner's own tests/), results reported exactly as before
    assert _outcomes(chunk) == {
        IDS[0]: "passed",
        IDS[1]: "failed",
        IDS[2]: "passed",  # the child saw no token / coordinator URL, GRID_POD and TEST_ENV were set
    }
    assert chunk["success"] is False
    assert chunk["environment"] == "qa" and chunk["browser"] == "chromium"
    assert chunk["tests"] == IDS
    assert "test_snapshot_only.py" in chunk["stdout"]
    assert "test_example" not in chunk["stdout"] and "test_sample" not in chunk["stdout"]
    assert "secret-token-value" not in chunk["stdout"] + chunk["stderr"]

    # raw Allure results were written and carry the grid labels
    results = _allure_results(allure_dir)
    assert len(results) == 3
    for result in results:
        labels = {}
        for label in result["labels"]:
            labels.setdefault(label["name"], []).append(label["value"])
        assert "pod-test-1" in labels["host"]
        assert labels["browser"] == ["chromium"]
        assert labels["environment"] == ["qa"]
    assert {r["status"] for r in results} == {"passed", "failed"}

    # the immutable snapshot was not written to (no bytecode, no .pytest_cache)
    assert sorted(p.name for p in snap.rglob("*") if p.is_file() or p.is_dir()) == [
        "test_snapshot_only.py", "tests"]


def test_chunk_without_allure_installed_still_reports_results(runner, tmp_path, monkeypatch, writable_on_cleanup):
    _write_volume_snapshot(tmp_path / "code-root")
    allure_dir = tmp_path / "allure"
    monkeypatch.setattr(runner, "allure_available", lambda: False)

    (chunk,) = runner.run_assigned_tests(IDS[:1], "qa", REPO, SHA, allure_dir=str(allure_dir))

    assert _outcomes(chunk) == {IDS[0]: "passed"}
    assert chunk["success"] is True
    assert list(allure_dir.iterdir()) == []


def test_chunk_from_a_coordinator_download_runs_too(runner, coordinator, tmp_path):
    coordinator.serve_code(REPO, SHA, make_tar(tmp_path / "s.tgz", SNAPSHOT))

    (chunk,) = runner.run_assigned_tests(IDS[:1], "qa", REPO, SHA)

    assert _outcomes(chunk) == {IDS[0]: "passed"}
    assert (tmp_path / "work" / "archives" / REPO / f"{SHA}.tar.gz").is_file()  # kept for the next chunk
    assert list((tmp_path / "work" / "chunks").iterdir()) == []  # the unpacked code is not


def test_run_without_repo_and_sha_keeps_the_baked_behaviour(runner, coordinator, tmp_path, monkeypatch):
    baked = tmp_path / "baked"
    (baked / "tests").mkdir(parents=True)
    (baked / "conftest.py").write_text('pytest_plugins = ["gridtest.results_plugin"]\n')
    (baked / "tests" / "test_baked.py").write_text("def test_ok():\n    assert True\n")
    monkeypatch.chdir(baked)

    (chunk,) = runner.run_assigned_tests(["tests/test_baked.py::test_ok"], "qa", allure_dir=str(tmp_path / "al"))

    assert _outcomes(chunk) == {"tests/test_baked.py::test_ok": "passed"}
    assert chunk["success"] is True
    assert not (tmp_path / "al").exists()  # no allure for runs that carry no code reference
    assert not (baked / ".pytest_cache").exists()  # the cache goes to a temp dir: /app is not writable
    assert coordinator.requests == []


def _lease(chunk_id, repo=REPO, sha=SHA, tests=IDS[:1]):
    return {"chunk_id": chunk_id, "run_id": "run1", "tests": tests, "environment": "qa",
            "browser": "chromium", "repo": repo, "sha": sha}


def test_runner_loop_submits_first_then_uploads_allure(runner, coordinator, tmp_path, keep_sigterm_handler, capsys):
    coordinator.serve_code(REPO, SHA, make_tar(tmp_path / "s.tgz", SNAPSHOT))
    coordinator.leases.append(_lease("run1-chromium-000"))

    runner.poll_and_execute()

    assert [e[0] for e in coordinator.events] == ["submit", "allure"]
    submit = coordinator.events[0][1]
    assert submit["chunk_id"] == "run1-chromium-000" and submit["runner_id"] == "pod-test-1"
    (chunk,) = submit["results"]
    assert chunk["success"] is True and _outcomes(chunk) == {IDS[0]: "passed"}
    _, chunk_id, body = coordinator.events[1]
    assert chunk_id == "run1-chromium-000"
    with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as tar:
        names = tar.getnames()
    assert any(n.endswith("-result.json") for n in names)
    assert all("/" not in n for n in names)  # the directory CONTENTS, no top-level folder
    assert all(r[2].get("X-API-Token") == "secret-token-value" for r in coordinator.requests)
    assert "secret-token-value" not in capsys.readouterr().out


def test_runner_loop_survives_unavailable_code_and_never_uploads_for_it(runner, coordinator, tmp_path,
                                                                        keep_sigterm_handler):
    other_sha = "b" * 40
    coordinator.serve_code(REPO, SHA, make_tar(tmp_path / "s.tgz", SNAPSHOT))
    coordinator.leases.append(_lease("run1-chromium-000", sha=other_sha))  # nobody has this code
    coordinator.leases.append(_lease("run1-chromium-001"))

    runner.poll_and_execute()  # must not raise or exit

    assert [e[0] for e in coordinator.events] == ["submit", "submit", "allure"]
    (failed,) = coordinator.events[0][1]["results"]
    assert failed["success"] is False and failed["test_results"] == []
    assert failed["stderr"].startswith(f"Code for {REPO}@{other_sha[:7]} is not available")
    assert coordinator.events[1][1]["results"][0]["success"] is True
    assert coordinator.events[2][1] == "run1-chromium-001"


def test_a_failing_allure_upload_does_not_fail_the_chunk(runner, coordinator, tmp_path, keep_sigterm_handler):
    coordinator.serve_code(REPO, SHA, make_tar(tmp_path / "s.tgz", SNAPSHOT))
    coordinator.leases.append(_lease("run1-chromium-000"))
    coordinator.allure_statuses = [500, 500, 500]

    runner.poll_and_execute()  # gives up quietly, loop ends on the empty queue

    assert [e[0] for e in coordinator.events] == ["submit", "allure", "allure", "allure"]
    assert coordinator.events[0][1]["results"][0]["success"] is True
