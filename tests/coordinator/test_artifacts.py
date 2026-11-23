"""Tests for artifacts.py (names, safe extraction, stores, retention, cache key): no database."""
import asyncio
import gzip
import io
import os
import re
import stat
import tarfile
from pathlib import Path

import pytest

from coordinator import artifacts
from artifact_helpers import SHA, age, body, make_tar, put_allure, result_archive, sha256, write
from coordinator.artifacts import ArtifactError, Settings


# --- names and settings ----------------------------------------------------------
@pytest.mark.parametrize("repo, ok", [("amplify", True), ("my.repo-1_x", True), ("a" * 100, True),
                                      ("a" * 101, False), (".hidden", False), ("-x", False), ("a/b", False),
                                      ("..", False), ("a\n", False), ("", False), ("sp ace", False)])
def test_repo_validation(repo, ok):
    assert artifacts.valid_repo(repo) is ok


@pytest.mark.parametrize("sha, ok", [(SHA, True), ("0123456789abcdef" * 2 + "01234567", True), ("A" * 40, False),
                                     ("a" * 39, False), ("a" * 41, False), ("g" * 40, False), (SHA + "\n", False)])
def test_sha_validation(sha, ok):
    assert artifacts.valid_sha(sha) is ok


def test_safe_name_never_escapes():
    assert artifacts.safe_name("run-20250101-abc") == "run-20250101-abc"
    for bad in ("../../etc/passwd", "a/b", "", ".hidden", "x" * 300):
        name = artifacts.safe_name(bad)
        assert re.fullmatch(r"x-[0-9a-f]{32}", name)


def test_settings_defaults_overrides_and_errors():
    d = Settings.from_env({})
    assert (d.code_dir, d.allure_dir, d.report_dir) == (Path("/data/code"), Path("/data/allure"), Path("/data/reports"))
    assert (d.max_code_bytes, d.max_allure_bytes, d.max_allure_unpacked_bytes, d.max_allure_files) == \
        (104857600, 52428800, 524288000, 20000)
    assert (d.allure3_bin, d.report_timeout_seconds, d.report_concurrency) == ("allure", 120, 1)
    assert (d.code_retention_days, d.allure_retention_days) == (30, 14)
    s = Settings.from_env({"CODE_DIR": "/x/c", "MAX_CODE_BYTES": "5", "ALLURE3_BIN": " npx ", "REPORT_CONCURRENCY": "3"})
    assert (s.code_dir, s.max_code_bytes, s.allure3_bin, s.report_concurrency) == (Path("/x/c"), 5, "npx", 3)
    for bad in ({"MAX_CODE_BYTES": "lots"}, {"REPORT_TIMEOUT_SECONDS": "0"}, {"CODE_RETENTION_DAYS": "-1"}):
        with pytest.raises(ValueError):
            Settings.from_env(bad)


# --- safe tar extraction ---------------------------------------------------------
LIMITS = dict(max_members=100, max_bytes=10_000)


def test_extract_strips_dot_slash_and_neutralises_modes(tmp_path):
    setuid = tarfile.TarInfo("./bin/run.sh")
    setuid.mode, setuid.size = 0o4777, 3
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        root = tarfile.TarInfo(".")
        root.type = tarfile.DIRTYPE
        tar.addfile(root)
        d = tarfile.TarInfo("./bin")
        d.type = tarfile.DIRTYPE
        tar.addfile(d)
        tar.addfile(setuid, io.BytesIO(b"hi\n"))
        f = tarfile.TarInfo("./a.txt")
        f.size = 1
        tar.addfile(f, io.BytesIO(b"x"))
    archive = write(tmp_path / "in" / "a.tar.gz", buf.getvalue())
    dest = tmp_path / "out" / "snap"
    assert artifacts.safe_extract_tar(archive, dest, **LIMITS) == (4, 4)
    assert (dest / "a.txt").read_bytes() == b"x"
    run_sh = dest / "bin" / "run.sh"
    assert run_sh.read_bytes() == b"hi\n" and not run_sh.stat().st_mode & (stat.S_ISUID | stat.S_ISGID)
    assert [p.name for p in (tmp_path / "out").iterdir()] == ["snap"]


@pytest.mark.parametrize("entries", [
    [("file", "ok.txt", b"1"), ("file", "/etc/evil", b"x")],
    [("file", "ok.txt", b"1"), ("file", "../evil", b"x")],
    [("file", "ok.txt", b"1"), ("file", "a/../../evil", b"x")],
    [("file", "ok.txt", b"1"), ("file", "./../evil", b"x")],
    [("file", "ok.txt", b"1"), ("symlink", "link", "/etc/passwd")],
    [("file", "ok.txt", b"1"), ("symlink", "link", "ok.txt")],
    [("file", "ok.txt", b"1"), ("hardlink", "hl", "ok.txt")],
    [("file", "ok.txt", b"1"), ("chr", "dev", None)],
    [("file", "ok.txt", b"1"), ("fifo", "pipe", None)],
], ids=["absolute", "dotdot", "nested-dotdot", "dot-dotdot", "symlink-abs", "symlink-rel", "hardlink", "device", "fifo"])
def test_extract_rejects_attacks_and_leaves_nothing(tmp_path, entries):
    archive = write(tmp_path / "in" / "a.tar.gz", make_tar(entries))
    dest = tmp_path / "out" / "snap"
    with pytest.raises(ValueError):
        artifacts.safe_extract_tar(archive, dest, **LIMITS)
    assert not dest.exists() and list((tmp_path / "out").iterdir()) == []
    assert not (tmp_path / "evil").exists() and not (tmp_path / "in" / "evil").exists()
    with pytest.raises(ValueError):
        artifacts.walk_tar(archive, **LIMITS)


def test_extract_enforces_size_and_member_limits(tmp_path):
    dest = tmp_path / "out" / "snap"
    bomb = write(tmp_path / "in" / "bomb.tar.gz", make_tar([("file", "zeros", b"\0" * 3_000_000)]))
    assert bomb.stat().st_size < 10_000  # tiny on disk, huge when unpacked
    with pytest.raises(ValueError, match="unpacks to more"):
        artifacts.safe_extract_tar(bomb, dest, max_members=10, max_bytes=1_000_000)
    many = write(tmp_path / "in" / "many.tar.gz", make_tar([("file", f"f{i}", b"x") for i in range(6)]))
    with pytest.raises(ValueError, match="more than 5 members"):
        artifacts.safe_extract_tar(many, dest, max_members=5, max_bytes=10_000)
    assert artifacts.safe_extract_tar(many, dest, max_members=6, max_bytes=6) == (6, 6)  # limits are inclusive
    assert list((tmp_path / "out").iterdir()) == [dest]


def test_extract_rejects_garbage_and_truncated_archives(tmp_path):
    good = make_tar([("file", "a", b"x" * 5000)])
    plain = io.BytesIO()
    with tarfile.open(fileobj=plain, mode="w") as tar:  # a valid tar that is not gzip-compressed
        tar.addfile(tarfile.TarInfo("a"))
    for name, data in (("text", b"not an archive"), ("empty", b""), ("raw-tar", plain.getvalue()),
                       ("truncated", good[: len(good) // 2]), ("gz-not-tar", gzip.compress(b"hello" * 100))):
        dest = tmp_path / "out" / name
        with pytest.raises(ValueError):
            artifacts.safe_extract_tar(write(tmp_path / "in" / f"{name}.tar.gz", data), dest, **LIMITS)
        assert not dest.exists()
    assert list((tmp_path / "out").iterdir()) == []


def test_extract_refuses_existing_destination(tmp_path):
    archive = write(tmp_path / "a.tar.gz", make_tar([("file", "a", b"x")]))
    (tmp_path / "snap").mkdir()
    with pytest.raises(FileExistsError):
        artifacts.safe_extract_tar(archive, tmp_path / "snap", **LIMITS)


# --- code store ------------------------------------------------------------------
def put_code(store, data, repo="app", sha=SHA, header="auto", content_length="auto"):
    if header == "auto":
        header = sha256(data)
    cl = str(len(data)) if content_length == "auto" else content_length
    return asyncio.run(store.put_code(repo, sha, header, body(data), cl))


def test_code_put_is_atomic_idempotent_and_readable(store, settings):
    data = os.urandom(1500)
    assert put_code(store, data) == ("stored", 1500)
    final = settings.code_dir / "app" / f"{SHA}.tar.gz"
    assert final.read_bytes() == data
    assert os.listdir(final.parent) == [final.name]  # no temp files left behind
    assert put_code(store, data) == ("exists", 1500)
    assert store.code_file("app", SHA) == (final, sha256(data))
    assert store.code_file("app", "b" * 40) is None


def test_code_put_rejections_store_nothing(store, settings):
    data = b"x" * 500
    cases = [
        (dict(header="0" * 64), 400),                      # checksum mismatch
        (dict(header=None), 400),                          # header is required
        (dict(header="nothex"), 400),
        (dict(repo="../evil"), 400), (dict(repo="a/b"), 400), (dict(sha="A" * 40), 400), (dict(sha="abc"), 400),
        (dict(content_length="999999"), 413),              # declared size over the limit
    ]
    for kwargs, status in cases:
        with pytest.raises(ArtifactError) as exc:
            put_code(store, data, **kwargs)
        assert exc.value.status_code == status, kwargs
    # Over the limit without Content-Length (chunked): cut off while streaming.
    big = b"y" * 2500
    with pytest.raises(ArtifactError) as exc:
        put_code(store, big, content_length=None)
    assert exc.value.status_code == 413
    with pytest.raises(ArtifactError) as exc:
        put_code(store, b"")
    assert exc.value.status_code == 400
    assert not any(settings.code_dir.rglob("*.tar.gz")) and not any(settings.code_dir.rglob(".upload-*"))


def test_code_put_failure_mid_stream_leaves_nothing(store, settings):
    async def broken():
        yield b"x" * 100
        raise ConnectionError("client went away")

    with pytest.raises(ConnectionError):
        asyncio.run(store.put_code("app", SHA, "0" * 64, broken(), None))
    assert not any(p.is_file() for p in settings.code_dir.rglob("*"))


def test_code_put_with_different_content_for_same_commit_conflicts(store, settings):
    put_code(store, b"first" * 50)
    with pytest.raises(ArtifactError) as exc:
        put_code(store, b"second" * 50)
    assert exc.value.status_code == 409 and str(settings.code_dir) not in exc.value.detail
    assert (settings.code_dir / "app" / f"{SHA}.tar.gz").read_bytes() == b"first" * 50
    assert os.listdir(settings.code_dir / "app") == [f"{SHA}.tar.gz"]


def test_code_path_cannot_leave_code_dir(store, settings):
    for repo, sha in (("..", SHA), ("a/../..", SHA), ("app", "../" + "a" * 37)):
        with pytest.raises(ArtifactError):
            store.code_path(repo, sha)
    assert store.code_path("app", SHA).parent.parent == settings.code_dir


# --- allure store ----------------------------------------------------------------
def test_allure_put_overwrite_and_listing(store, settings):
    first = result_archive(["test_a"])
    assert put_allure(store, "run-1", "run-1-chrome-001", first) == len(first)
    second = result_archive(["test_a", "test_b"])
    assert put_allure(store, "run-1", "run-1-chrome-001", second) == len(second)  # a retry overwrites
    put_allure(store, None, "legacy chunk id", first)
    listed = store.list_allure("run-1")
    assert [name for name, _p, _s in listed] == ["run-1-chrome-001"]
    assert listed[0][1].read_bytes() == second
    assert os.listdir(settings.allure_dir / "run-1") == ["run-1-chrome-001.tar.gz"]
    assert (settings.allure_dir / "_").is_dir()
    assert store.list_allure("run-none") == []


def test_allure_put_rejects_hostile_and_oversize_archives(store, settings):
    bad = make_tar([("file", "ok-result.json", b"{}"), ("symlink", "x-attachment.png", "/etc/shadow")])
    with pytest.raises(ArtifactError) as exc:
        put_allure(store, "run-1", "c1", bad)
    assert exc.value.status_code == 400 and "Invalid Allure archive" in exc.value.detail
    with pytest.raises(ArtifactError) as exc:
        put_allure(store, "run-1", "c1", b"junk" * 10)
    assert exc.value.status_code == 400
    big = make_tar([("file", "a-result.json", os.urandom(30000))])
    with pytest.raises(ArtifactError) as exc:
        put_allure(store, "run-1", "c1", big)
    assert exc.value.status_code == 413
    assert store.list_allure("run-1") == [] and not any(settings.allure_dir.rglob(".upload-*"))


def test_allure_path_cannot_leave_allure_dir(store, settings):
    for run, chunk in (("../../etc", "../passwd"), ("run-1", "a/../../b"), ("run-1", "x/y")):
        path = store.allure_path(run, chunk).resolve()
        assert settings.allure_dir.resolve() in path.parents


# --- retention -------------------------------------------------------------------
def test_retention_deletes_only_old_data(store, settings):
    code_old = age(write(settings.code_dir / "app" / ("1" * 40 + ".tar.gz"), b"o"), 31)
    code_young = age(write(settings.code_dir / "app" / ("2" * 40 + ".tar.gz"), b"y"), 29.5)
    code_stale_dir = age(write(settings.code_dir / "gone" / ("3" * 40 + ".tar.gz"), b"o"), 90)
    allure_old = age(write(settings.allure_dir / "run-a" / "c1.tar.gz", b"o"), 15)
    allure_young = age(write(settings.allure_dir / "run-a" / "c2.tar.gz", b"y"), 13.5)
    allure_stale_dir = age(write(settings.allure_dir / "run-b" / "c1.tar.gz", b"o"), 20)
    report_old = age(write(settings.report_dir / "run-a" / ("k" * 64 + ".html"), b"o"), 15)
    report_young = age(write(settings.report_dir / "run-a" / ("j" * 64 + ".html"), b"y"), 1)
    work_old = age(write(settings.report_dir / ".work" / "gen-old" / "x", b"o"), 2)
    age(settings.report_dir / ".work" / "gen-old", 2)
    work_new = write(settings.report_dir / ".work" / "gen-new" / "x", b"y")

    removed = store.purge_expired()

    assert removed == {"code": 2, "allure": 2, "reports": 1}
    for gone in (code_old, code_stale_dir, allure_old, allure_stale_dir, report_old, work_old):
        assert not gone.exists(), gone
    for kept in (code_young, allure_young, report_young, work_new):
        assert kept.is_file(), kept
    assert not code_stale_dir.parent.exists() and not allure_stale_dir.parent.exists()  # empty dirs go too
    assert (settings.report_dir / ".work").is_dir()
    assert store.purge_expired() == {"code": 0, "allure": 0, "reports": 0}


def test_retention_on_missing_directories_is_a_noop(store):
    assert store.purge_expired() == {"code": 0, "allure": 0, "reports": 0}


def test_retention_keeps_everything_when_nothing_is_older_than_the_limit(store, settings):
    paths = [age(write(settings.code_dir / "app" / (str(i) * 40 + ".tar.gz"), b"x"), 29.9) for i in range(3)]
    paths += [age(write(settings.allure_dir / "r" / "c.tar.gz", b"x"), 13.9)]
    store.purge_expired()
    assert all(p.is_file() for p in paths)


# --- cache key and ETag ----------------------------------------------------------
def test_cache_key_tracks_the_runs_archives(store, settings):
    a = result_archive(["test_a"])
    put_allure(store, "run-1", "c1", a)
    key1 = artifacts.archive_key(store.list_allure("run-1"))
    assert key1 == artifacts.archive_key(store.list_allure("run-1"))
    assert key1 == artifacts.archive_key(list(reversed(store.list_allure("run-1"))))  # order independent
    put_allure(store, "run-1", "c2", result_archive(["test_b"]))
    key2 = artifacts.archive_key(store.list_allure("run-1"))
    assert key2 != key1                                   # more chunks arrived
    path = store.allure_path("run-1", "c1")
    put_allure(store, "run-1", "c1", result_archive(["test_a", "test_c"]))
    key3 = artifacts.archive_key(store.list_allure("run-1"))
    assert key3 not in (key1, key2)                       # an archive changed
    os.utime(path, ns=(1, 1))
    assert artifacts.archive_key(store.list_allure("run-1")) != key3  # even only its mtime
    assert re.fullmatch(r"[0-9a-f]{64}", key1)


@pytest.mark.parametrize("header, expected", [
    ('"k"', True), ('W/"k"', True), ('"x", "k"', True), ('"x","k" ', True),
    ('"x"', False), ("k", False), ("", False), (None, False), ("*", False), ('"kk"', False),
])
def test_etag_matching(header, expected):
    assert artifacts.etag_matches(header, "k") is expected


def test_error_tail_hides_paths_ansi_and_is_bounded():
    text = "\x1b[31mNo results found matching pattern: /srv/data/reports/.work/gen-1/results\x1b[0m"
    assert artifacts._error_tail(text, "") == "No results found matching pattern: results"
    assert artifacts._error_tail("", "see https://example.com/a/b ok").endswith("https://example.com/a/b ok")
    assert len(artifacts._error_tail("x" * 1000, "")) == 300


# --- assembling results ----------------------------------------------------------
def test_assemble_keeps_both_files_on_name_collision(store, tmp_path):
    put_allure(store, "run-1", "c1", result_archive(["test_a"], browser="chrome"))
    put_allure(store, "run-1", "c2", result_archive(["test_b"], browser="firefox"))
    archives = store.list_allure("run-1")
    dest = tmp_path / "work" / "results"
    n = artifacts.assemble_results(archives, dest, max_members=50, max_bytes=100_000)
    names = sorted(p.name for p in dest.iterdir())
    assert n == 4 and len(names) == 4
    assert "environment.properties" in names and "c2-environment.properties" in names
    assert (dest / "environment.properties").read_text() == "browser=chrome\n"
    assert (dest / "c2-environment.properties").read_text() == "browser=firefox\n"
    assert not any((dest.parent / "results-parts").iterdir())  # per-chunk staging dirs were emptied


def test_assemble_limits_apply_to_the_whole_run(store, tmp_path):
    for c in ("c1", "c2", "c3"):
        put_allure(store, "run-1", c, result_archive(["test_a"]))  # 2 files each
    archives = store.list_allure("run-1")
    one = artifacts.walk_tar(archives[0][1], 100, 100_000)[1]  # unpacked bytes of a single chunk
    with pytest.raises(ValueError, match="more than"):
        artifacts.assemble_results(archives, tmp_path / "r1", max_members=5, max_bytes=100_000)
    with pytest.raises(ValueError, match="unpacks to more"):
        artifacts.assemble_results(archives, tmp_path / "r2", max_members=100, max_bytes=one * 2 + 1)


