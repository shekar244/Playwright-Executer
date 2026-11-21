"""Child environment, pytest arguments, Allure packing and upload (no real pytest runs here)."""
import io
import json
import os
import sys
import tarfile
from pathlib import Path

import pytest

PROJECT_SRC = str(Path(__file__).resolve().parents[2] / "src")


def _members(data: bytes) -> dict:
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        return {m.name: tar.extractfile(m).read() for m in tar.getmembers() if m.isfile()}


# ---------------------------------------------------------------- child environment

def test_child_env_drops_the_grid_credentials_and_sets_the_grid_variables(runner, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "/already/here")
    monkeypatch.setenv("UNRELATED", "kept")

    env = runner.build_test_env("qa", "/tmp/results.json", snapshot=True)

    assert "GRID_API_TOKEN" not in env
    assert "COORDINATOR_URL" not in env
    assert env["GRID_POD"] == "pod-test-1"
    assert env["BROWSER"] == "chromium"
    assert env["TEST_ENV"] == "qa"
    assert env["GRID_RESULTS_FILE"] == "/tmp/results.json"
    assert env["UNRELATED"] == "kept"
    assert env["PYTHONPATH"].split(os.pathsep) == [PROJECT_SRC, "/already/here"]
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"
    assert os.environ["GRID_API_TOKEN"] == "secret-token-value"  # the runner's own environment is untouched


def test_child_env_without_environment_or_snapshot(runner):
    env = runner.build_test_env("", "/tmp/r.json")

    assert "TEST_ENV" not in env
    assert "PYTHONDONTWRITEBYTECODE" not in env
    assert env["PYTHONPATH"] == PROJECT_SRC


def test_framework_src_dir_finds_the_checkout_layout(runner):
    assert runner.framework_src_dir() == PROJECT_SRC


# ---------------------------------------------------------------- pytest arguments

TESTS = ["tests/a.py::test_a", "tests/b.py::test_b"]


def test_snapshot_args_have_both_plugins_and_alluredir_when_allure_is_available(runner, monkeypatch):
    monkeypatch.setattr(runner, "allure_available", lambda: True)

    args = runner.build_pytest_args(TESTS, snapshot=True, alluredir="/tmp/al", cache_dir="/tmp/cache")

    assert args[:3] == [sys.executable, "-m", "pytest"]
    assert "-n=2" in args
    for plugin in ("gridtest.results_plugin", "gridtest.allure_labels"):
        assert args[args.index(plugin) - 1] == "-p"
    assert "--alluredir=/tmp/al" in args
    assert "cache_dir=/tmp/cache" in args
    assert args[args.index("--") + 1:] == TESTS


def test_snapshot_args_have_no_alluredir_without_allure(runner, monkeypatch):
    monkeypatch.setattr(runner, "allure_available", lambda: False)

    args = runner.build_pytest_args(TESTS, snapshot=True, alluredir="/tmp/al")

    assert not any(a.startswith("--alluredir") for a in args)
    assert "gridtest.results_plugin" in args and "gridtest.allure_labels" in args


def test_allure_available_follows_importability(runner, monkeypatch):
    assert runner.allure_available() is True  # installed in the test environment
    monkeypatch.setitem(sys.modules, "allure_pytest", None)  # makes the import fail
    assert runner.allure_available() is False


def test_args_cap_parallelism_and_keep_test_ids_positional(runner):
    ids = [f"t.py::t{i}" for i in range(5)] + ["--collect-only"]

    args = runner.build_pytest_args(ids, snapshot=True)

    assert "-n=3" in args
    assert args[args.index("--") + 1:] == ids
    assert runner.build_pytest_args(["t.py::only"])[4] == "-n=1"


def test_baked_run_args_are_unchanged(runner):
    assert runner.build_pytest_args(TESTS, alluredir="/tmp/al") == [
        sys.executable, "-m", "pytest", "-v", "-n=2", "--", *TESTS,
    ]


def test_baked_run_args_only_gain_a_cache_dir(runner):
    # /app is not writable by the test uid: .pytest_cache must go to a temp dir for baked runs too
    assert runner.build_pytest_args(TESTS, alluredir="/tmp/al", cache_dir="/tmp/c") == [
        sys.executable, "-m", "pytest", "-v", "-n=2", "-o", "cache_dir=/tmp/c", "--", *TESTS,
    ]


# ---------------------------------------------------------------- pack_allure

def _results_dir(tmp_path, attachment_bytes=0):
    d = tmp_path / "allure"
    d.mkdir()
    (d / "u1-result.json").write_text(json.dumps({"name": "t", "status": "passed"}))
    (d / "u1-container.json").write_text("{}")
    (d / "environment.properties").write_text("a=b\n")
    if attachment_bytes:
        (d / "u1-attachment.png").write_bytes(os.urandom(attachment_bytes))
        (d / "u2-attachment").write_bytes(os.urandom(attachment_bytes))  # no extension
    return d


def test_pack_allure_archives_the_directory_contents_flat(runner, tmp_path):
    d = _results_dir(tmp_path, attachment_bytes=100)

    names = set(_members(runner.pack_allure(str(d), 10_000_000)))

    assert names == {"u1-result.json", "u1-container.json", "environment.properties",
                     "u1-attachment.png", "u2-attachment"}


def test_pack_allure_drops_attachments_only_when_over_the_cap(runner, tmp_path):
    d = _results_dir(tmp_path, attachment_bytes=50_000)

    data = runner.pack_allure(str(d), 5_000)

    assert set(_members(data)) == {"u1-result.json", "u1-container.json", "environment.properties"}
    assert len(data) <= 5_000


def test_pack_allure_skips_when_still_over_the_cap(runner, tmp_path, capsys):
    d = _results_dir(tmp_path)
    (d / "big-result.json").write_bytes(os.urandom(20_000))  # incompressible and not an attachment

    assert runner.pack_allure(str(d), 5_000) is None
    assert "skipping the upload" in capsys.readouterr().out


def test_pack_allure_ignores_symlinks_and_empty_or_missing_dirs(runner, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert runner.pack_allure(str(empty), 10_000) is None
    assert runner.pack_allure(str(tmp_path / "missing"), 10_000) is None

    d = _results_dir(tmp_path)
    secret = tmp_path / "secret.txt"
    secret.write_text("do not upload")
    (d / "link-result.json").symlink_to(secret)
    assert "link-result.json" not in _members(runner.pack_allure(str(d), 1_000_000))


def test_allure_size_cap_comes_from_the_environment(runner, monkeypatch):
    assert runner._allure_max_bytes() == 52428800
    monkeypatch.setenv("GRID_ALLURE_MAX_BYTES", "1234")
    assert runner._allure_max_bytes() == 1234
    monkeypatch.setenv("GRID_ALLURE_MAX_BYTES", "lots")
    assert runner._allure_max_bytes() == 52428800


# ---------------------------------------------------------------- upload_allure

def test_upload_sends_the_archive_with_the_token(runner, coordinator, capsys):
    assert runner.upload_allure("run1-chromium-000", b"archive-bytes") is True

    (method, path, headers, body), = coordinator.requests
    assert (method, path) == ("PUT", "/api/chunks/run1-chromium-000/allure")
    assert body == b"archive-bytes"
    assert headers["X-API-Token"] == "secret-token-value"
    assert headers["Content-Type"] == "application/gzip"
    assert "secret-token-value" not in capsys.readouterr().out


def test_upload_retries_a_transient_failure(runner, coordinator):
    coordinator.allure_statuses = [500, 503]  # the third attempt gets 200

    assert runner.upload_allure("c1", b"x") is True
    assert len(coordinator.requests) == 3


def test_upload_gives_up_after_three_attempts_without_raising(runner, coordinator):
    coordinator.allure_statuses = [500] * 10

    assert runner.upload_allure("c1", b"x") is False
    assert len(coordinator.requests) == 3


@pytest.mark.parametrize("status", [404, 413])
def test_upload_does_not_retry_permanent_errors(runner, coordinator, status):
    coordinator.allure_statuses = [status]

    assert runner.upload_allure("c1", b"x") is False
    assert len(coordinator.requests) == 1


def test_upload_to_an_unreachable_coordinator_returns_false(runner, monkeypatch):
    monkeypatch.setattr(runner, "COORDINATOR_URL", "http://127.0.0.1:1")

    assert runner.upload_allure("c1", b"x") is False


def test_publish_allure_never_raises(runner, tmp_path, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("disk exploded")

    monkeypatch.setattr(runner, "pack_allure", broken)
    runner.publish_allure("c1", str(tmp_path))  # must simply return


def test_publish_allure_with_nothing_to_send_is_quiet(runner, coordinator, tmp_path):
    runner.publish_allure("c1", str(tmp_path / "does-not-exist"))

    assert coordinator.requests == []
