"""Reusing stored data restarts its retention clock (the hourly sweep deletes by mtime)."""
import asyncio
import hashlib
import os
import time
from dataclasses import replace
from unittest import mock

import artifacts
from artifact_helpers import FAKE_OK, SHA, age, body, fake_cli, put_allure, result_archive
from artifacts import ReportService

ALMOST_EXPIRED = 29.99     # days; the code retention is 30
HOUR = 3600


def put_code(store, data=b"snapshot", sha=SHA):
    digest = hashlib.sha256(data).hexdigest()
    return asyncio.run(store.put_code("app", sha, digest, body(data), str(len(data)))), digest


def seconds_since_modified(path) -> float:
    return time.time() - path.stat().st_mtime


def sweep_in_an_hour(store) -> dict:
    return store.purge_expired(time.time() + HOUR)


# --- code snapshots ----------------------------------------------------------------------
def test_without_reuse_an_almost_expired_snapshot_is_swept(store):
    """The control for the tests below: this is the failure being fixed."""
    put_code(store)
    age(store.code_path("app", SHA), ALMOST_EXPIRED)
    assert sweep_in_an_hour(store)["code"] == 1


def test_putting_a_snapshot_that_exists_restarts_its_clock(store):
    (state, _size), digest = put_code(store)
    assert state == "stored"
    path = age(store.code_path("app", SHA), ALMOST_EXPIRED)

    (state, _size), _ = put_code(store)

    assert state == "exists"
    assert seconds_since_modified(path) < 60
    assert sweep_in_an_hour(store)["code"] == 0 and path.is_file()
    assert store.code_file("app", SHA)[1] == digest


def test_serving_a_snapshot_get_or_head_restarts_its_clock(store):
    _, digest = put_code(store)
    path = age(store.code_path("app", SHA), ALMOST_EXPIRED)

    assert store.code_file("app", SHA) == (path, digest)        # what GET and HEAD /api/code call

    assert seconds_since_modified(path) < 60
    assert sweep_in_an_hour(store)["code"] == 0 and path.is_file()


def test_refreshing_does_not_invalidate_the_checksum_cache(store):
    """The cache is keyed by size+mtime: a refresh must not make every HEAD re-hash up to 100 MiB."""
    _, digest = put_code(store)
    age(store.code_path("app", SHA), 5)
    assert store.code_file("app", SHA)[1] == digest             # touches, re-keys the cache entry

    with mock.patch.object(artifacts.hashlib, "sha256", side_effect=AssertionError("snapshot was re-hashed")):
        assert store.code_file("app", SHA)[1] == digest
        assert store.code_file("app", SHA)[1] == digest


def test_a_missing_snapshot_is_still_none(store):
    assert store.code_file("app", SHA) is None


def test_reuse_never_raises_when_the_file_cannot_be_touched(store):
    _, digest = put_code(store)
    path = age(store.code_path("app", SHA), 3)
    before = path.stat().st_mtime

    with mock.patch.object(os, "utime", side_effect=PermissionError("read-only volume")):
        assert store.code_file("app", SHA) == (path, digest)
        (state, _size), _ = put_code(store)
        assert state == "exists"

    assert path.stat().st_mtime == before                       # nothing changed, nothing failed


# --- raw Allure archives -----------------------------------------------------------------------
def counting_service(tmp_path, settings, store) -> tuple:
    runs = tmp_path / "cli-runs"
    script = f'echo run >> "{runs}"\n' + FAKE_OK
    return ReportService(replace(settings, allure3_bin=fake_cli(tmp_path, script)), store), runs


def test_building_a_report_restarts_the_clock_of_the_archives_it_used(tmp_path, settings, store):
    reports, _runs = counting_service(tmp_path, settings, store)
    put_allure(store, "run-1", "c1", result_archive(["test_a"]))
    put_allure(store, "run-1", "c2", result_archive(["test_b"]))
    put_allure(store, "run-2", "c1", result_archive(["test_c"]))
    paths = [age(p, 13.99) for _n, p, _s in store.list_allure("run-1") + store.list_allure("run-2")]

    asyncio.run(reports.render("run-1"))

    run_1, run_2 = paths[:2], paths[2]
    assert all(seconds_since_modified(p) < 60 for p in run_1)
    assert seconds_since_modified(run_2) > 13 * 86400            # a run nobody asked about keeps aging
    removed = sweep_in_an_hour(store)
    assert removed["allure"] == 1 and all(p.is_file() for p in run_1) and not run_2.exists()


def test_touching_the_archives_does_not_defeat_the_report_cache(tmp_path, settings, store):
    """The archives' mtimes are part of the cache key: the key must be taken after the touch."""
    reports, runs = counting_service(tmp_path, settings, store)
    put_allure(store, "run-1", "c1", result_archive(["test_a"]))
    age(store.list_allure("run-1")[0][1], 10)

    first = asyncio.run(reports.render("run-1"))
    assert reports.info("run-1")["cached"] is True              # the key a reader computes now is the one stored
    again = asyncio.run(reports.render("run-1"))
    not_modified = asyncio.run(reports.render("run-1", f'"{first.key}"'))

    assert again.path == first.path and again.key == first.key
    assert not_modified.path is None and not_modified.key == first.key
    assert runs.read_text().count("run") == 1                   # the CLI ran once
    assert seconds_since_modified(store.list_allure("run-1")[0][1]) < 60


def test_a_cached_report_does_not_touch_the_archives_again(tmp_path, settings, store):
    reports, _runs = counting_service(tmp_path, settings, store)
    put_allure(store, "run-1", "c1", result_archive(["test_a"]))
    asyncio.run(reports.render("run-1"))
    path = age(store.list_allure("run-1")[0][1], 2)
    key = artifacts.archive_key(store.list_allure("run-1"))

    # The aged mtime changed the key, so this is a fresh build (and it touches); a repeat is a cache hit.
    asyncio.run(reports.render("run-1"))
    touched = path.stat().st_mtime_ns
    assert artifacts.archive_key(store.list_allure("run-1")) != key
    asyncio.run(reports.render("run-1"))
    assert path.stat().st_mtime_ns == touched


def test_refresh_allure_survives_files_that_vanished_or_cannot_be_touched(store):
    put_allure(store, "run-1", "c1", result_archive(["test_a"]))
    put_allure(store, "run-1", "c2", result_archive(["test_b"]))
    listed = store.list_allure("run-1")
    listed[0][1].unlink()                                       # expired by the sweep meanwhile
    assert [n for n, _p, _s in store.refresh_allure("run-1", listed)] == ["c2"]
    with mock.patch.object(os, "utime", side_effect=PermissionError):
        assert [n for n, _p, _s in store.refresh_allure("run-1", listed)] == ["c2"]
