"""The download cache is verified on every chunk and the code is unpacked fresh for each one (W4)."""
import hashlib
import os
import stat

import pytest

import code_download
from runner_fakes import REPO, SHA, make_tar

PRISTINE = {
    "tests/test_pristine.py": b"def test_pristine():\n    assert True\n",
    "README.md": b"pristine\n",
}
POISONED = {
    "tests/test_pristine.py": b"def test_pristine():\n    assert False, 'poisoned'\n",
    "README.md": b"poisoned\n",
}
# Chunk 1's test code, running with the runner's uid, attacks everything a later chunk could reuse.
TAMPER = b'''import os
import shutil
from pathlib import Path


def test_tamper():
    work = Path(os.environ["GRID_WORK_DIR"])
    poison = Path(os.environ["POISON_TGZ"])
    here = Path.cwd()
    (here / "tests" / "test_pristine.py").write_text("def test_pristine():\\n    assert False, 'poisoned dir'\\n")
    for archive in work.glob("archives/*/*.tar.gz"):
        shutil.copy(poison, archive)
    old = work / "code" / os.environ["TAMPER_REPO"] / os.environ["TAMPER_SHA"] / "tests"  # where the old cache lived
    old.mkdir(parents=True)
    (old / "test_pristine.py").write_text("def test_pristine():\\n    assert False, 'poisoned old cache'\\n")
'''
TAMPER_ID = "tests/test_tamper.py::test_tamper"
PRISTINE_ID = "tests/test_pristine.py::test_pristine"


def _work(tmp_path):
    return tmp_path / "work"


def _archive(tmp_path):
    return code_download.archive_path(_work(tmp_path), REPO, SHA)


def _chunk_dirs(tmp_path):
    chunks = _work(tmp_path) / "chunks"
    return sorted(chunks.iterdir()) if chunks.exists() else []


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _methods(coordinator):
    return [m for m, *_ in coordinator.requests]


# ---------------------------------------------------------------- resolve_code level

def test_tampered_archive_and_extraction_between_two_chunks_never_reach_the_second(runner, coordinator, tmp_path):
    original = make_tar(tmp_path / "good.tgz", PRISTINE)
    coordinator.serve_code(REPO, SHA, original)

    first = runner.resolve_code(REPO, SHA)
    (first / "tests" / "test_pristine.py").write_bytes(POISONED["tests/test_pristine.py"])  # tamper: extraction
    (first / "planted.py").write_text("print('planted')")
    make_tar(_archive(tmp_path), POISONED)  # tamper: the cached archive (valid tar, wrong content)
    old_style = _work(tmp_path) / "code" / REPO / SHA  # tamper: plant the old extracted-dir cache
    old_style.mkdir(parents=True)
    (old_style / "planted.py").write_text("print('planted')")

    second = runner.resolve_code(REPO, SHA)

    assert second != first
    assert {p.name for p in second.rglob("*") if p.is_file()} == {"test_pristine.py", "README.md"}
    assert (second / "tests" / "test_pristine.py").read_bytes() == PRISTINE["tests/test_pristine.py"]
    assert (second / "README.md").read_bytes() == PRISTINE["README.md"]
    assert _sha256(_archive(tmp_path)) == hashlib.sha256(original).hexdigest()  # the cache was repaired
    assert _methods(coordinator) == ["HEAD", "GET", "HEAD", "GET"]  # re-downloaded because it no longer matched


def test_an_intact_archive_is_reused_but_every_chunk_still_asks_for_the_checksum(runner, coordinator, tmp_path):
    coordinator.serve_code(REPO, SHA, make_tar(tmp_path / "good.tgz", PRISTINE))

    dirs = {runner.resolve_code(REPO, SHA) for _ in range(3)}

    assert len(dirs) == 3  # a new directory every time
    assert _methods(coordinator) == ["HEAD", "GET", "HEAD", "HEAD"]


@pytest.mark.parametrize("damage", ["truncated", "empty", "garbage", "oversized", "directory", "fifo", "symlink"])
def test_every_kind_of_damaged_cache_entry_is_replaced_by_a_download(runner, coordinator, tmp_path, damage,
                                                                      monkeypatch):
    good = make_tar(tmp_path / "good.tgz", PRISTINE)
    coordinator.serve_code(REPO, SHA, good)
    runner.resolve_code(REPO, SHA)
    archive = _archive(tmp_path)
    if damage == "truncated":
        archive.write_bytes(good[: len(good) // 2])
    elif damage == "empty":
        archive.write_bytes(b"")
    elif damage == "garbage":
        archive.write_bytes(os.urandom(len(good)))
    elif damage == "oversized":
        archive.write_bytes(good + b"\0" * 64)
        monkeypatch.setattr(runner, "CODE_MAX_BYTES", len(good))  # the only way it can exceed the limit
    else:
        archive.unlink()
        if damage == "directory":
            (archive / "sub").mkdir(parents=True)
        elif damage == "fifo":
            os.mkfifo(archive)  # a plain read of this would block the runner forever
        else:
            (tmp_path / "other.tgz").write_bytes(good)
            archive.symlink_to(tmp_path / "other.tgz")  # even intact bytes behind a link are not trusted

    found = runner.resolve_code(REPO, SHA)

    assert found is not None
    assert (found / "tests" / "test_pristine.py").read_bytes() == PRISTINE["tests/test_pristine.py"]
    assert stat.S_ISREG(archive.lstat().st_mode) and archive.read_bytes() == good


def test_the_cached_archive_is_not_trusted_when_the_coordinator_announces_another_checksum(runner, coordinator,
                                                                                            tmp_path):
    good = make_tar(tmp_path / "good.tgz", PRISTINE)
    coordinator.serve_code(REPO, SHA, good)
    runner.resolve_code(REPO, SHA)  # cached and intact: it matches what was announced back then
    coordinator.serve_code(REPO, SHA, good, head_checksum="0" * 64)  # now HEAD says something else
    before, dirs_before = len(coordinator.requests), _chunk_dirs(tmp_path)

    assert runner.resolve_code(REPO, SHA) is None  # it re-downloaded, but that does not match either

    assert [m for m, *_ in coordinator.requests[before:]] == ["HEAD", "GET"]
    assert _chunk_dirs(tmp_path) == dirs_before  # nothing new was unpacked


def test_a_changed_checksum_with_matching_new_bytes_replaces_the_cache(runner, coordinator, tmp_path):
    coordinator.serve_code(REPO, SHA, make_tar(tmp_path / "old.tgz", POISONED))
    runner.resolve_code(REPO, SHA)
    coordinator.serve_code(REPO, SHA, make_tar(tmp_path / "new.tgz", PRISTINE))  # the coordinator now has this

    found = runner.resolve_code(REPO, SHA)

    assert (found / "tests" / "test_pristine.py").read_bytes() == PRISTINE["tests/test_pristine.py"]
    assert _archive(tmp_path).read_bytes() == (tmp_path / "new.tgz").read_bytes()


def test_a_get_that_announces_another_checksum_than_head_is_refused(runner, coordinator, tmp_path):
    good = make_tar(tmp_path / "good.tgz", PRISTINE)
    coordinator.serve_code(REPO, SHA, good, checksum="1" * 64, head_checksum=hashlib.sha256(good).hexdigest())

    assert runner.resolve_code(REPO, SHA) is None
    assert not _archive(tmp_path).exists()
    assert _chunk_dirs(tmp_path) == []


def test_without_a_checksum_from_the_coordinator_an_intact_cache_is_not_used(runner, coordinator, tmp_path):
    coordinator.serve_code(REPO, SHA, make_tar(tmp_path / "good.tgz", PRISTINE))
    runner.resolve_code(REPO, SHA)
    del coordinator.code[(REPO, SHA)]  # HEAD now answers 404: nothing to verify against

    assert runner.resolve_code(REPO, SHA) is None
    assert _archive(tmp_path).is_file()  # kept: it is simply not used


def test_the_extraction_comes_from_the_verified_bytes_not_from_the_file_on_disk(runner, coordinator, tmp_path,
                                                                                 monkeypatch):
    coordinator.serve_code(REPO, SHA, make_tar(tmp_path / "good.tgz", PRISTINE))
    runner.resolve_code(REPO, SHA)
    real_extract = code_download.safe_extract_tar

    def swap_then_extract(archive, dest, *args, **kwargs):
        make_tar(_archive(tmp_path), POISONED)  # swapped after the check, before the unpacking
        return real_extract(archive, dest, *args, **kwargs)

    monkeypatch.setattr(code_download, "safe_extract_tar", swap_then_extract)

    found = runner.resolve_code(REPO, SHA)

    assert (found / "tests" / "test_pristine.py").read_bytes() == PRISTINE["tests/test_pristine.py"]


def test_a_cache_that_cannot_be_written_does_not_fail_the_chunk(runner, coordinator, tmp_path, monkeypatch):
    coordinator.serve_code(REPO, SHA, make_tar(tmp_path / "good.tgz", PRISTINE))

    def disk_full(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(code_download, "_store_archive", disk_full)

    found = runner.resolve_code(REPO, SHA)

    assert (found / "tests" / "test_pristine.py").is_file()
    assert not _archive(tmp_path).exists()


# ---------------------------------------------------------------- cleanup helpers

def test_force_rmtree_removes_what_test_code_made_unreadable_or_read_only(tmp_path):
    tree = tmp_path / "tree"
    (tree / "locked" / "deeper").mkdir(parents=True)
    (tree / "locked" / "deeper" / "f.txt").write_text("x")
    (tree / "readonly").mkdir()
    (tree / "readonly" / "g.txt").write_text("y")
    (tree / "link").symlink_to(tmp_path)  # must not be followed
    for path in ((tree / "locked" / "deeper"), (tree / "locked"), (tree / "readonly")):
        path.chmod(0o500 if path.name == "readonly" else 0o000)

    code_download.force_rmtree(tree)

    assert not tree.exists()
    assert tmp_path.exists()
    code_download.force_rmtree(tmp_path / "does-not-exist")  # never raises


def test_discard_snapshot_only_touches_per_chunk_directories(tmp_path):
    work = tmp_path / "work"
    chunk = work / "chunks" / "chunk-abc" / REPO / SHA
    chunk.mkdir(parents=True)
    volume = tmp_path / "code-root" / REPO / SHA
    volume.mkdir(parents=True)
    (volume / "keep.txt").write_text("shared volume")

    code_download.discard_snapshot(volume, work)
    code_download.discard_snapshot(work / "chunks", work)  # the root itself is not a chunk
    code_download.discard_snapshot(None, work)
    assert (volume / "keep.txt").is_file() and chunk.is_dir()

    code_download.discard_snapshot(chunk, work)
    assert not (work / "chunks" / "chunk-abc").exists()


# ---------------------------------------------------------------- real pytest, two chunks of one run

def _stage(coordinator, tmp_path, monkeypatch):
    files = {**PRISTINE, "tests/test_tamper.py": TAMPER}
    coordinator.serve_code(REPO, SHA, make_tar(tmp_path / "snap.tgz", files))
    make_tar(tmp_path / "poison.tgz", {**files, **POISONED})
    monkeypatch.setenv("POISON_TGZ", str(tmp_path / "poison.tgz"))
    monkeypatch.setenv("TAMPER_REPO", REPO)
    monkeypatch.setenv("TAMPER_SHA", SHA)


def test_chunk_two_gets_pristine_code_after_chunk_one_tampered_with_every_cache(runner, coordinator, tmp_path,
                                                                                monkeypatch):
    _stage(coordinator, tmp_path, monkeypatch)

    (first,) = runner.run_assigned_tests([TAMPER_ID], "", REPO, SHA)
    # the hostile chunk really did tamper (otherwise the second chunk proves nothing)
    assert {r["nodeid"]: r["outcome"] for r in first["test_results"]} == {TAMPER_ID: "passed"}
    assert _sha256(_archive(tmp_path)) == _sha256(tmp_path / "poison.tgz")
    assert (_work(tmp_path) / "code" / REPO / SHA / "tests" / "test_pristine.py").is_file()
    assert _chunk_dirs(tmp_path) == []  # chunk 1's unpacked (and modified) code is gone

    (second,) = runner.run_assigned_tests([PRISTINE_ID], "", REPO, SHA)

    assert {r["nodeid"]: r["outcome"] for r in second["test_results"]} == {PRISTINE_ID: "passed"}
    assert second["success"] is True and "poisoned" not in second["stdout"]
    assert _sha256(_archive(tmp_path)) == _sha256(tmp_path / "snap.tgz")  # the cache was repaired
    assert _chunk_dirs(tmp_path) == []


def test_the_chunk_directory_is_removed_even_when_pytest_cannot_start(runner, coordinator, tmp_path, monkeypatch):
    coordinator.serve_code(REPO, SHA, make_tar(tmp_path / "snap.tgz", PRISTINE))

    def cannot_start(*args, **kwargs):
        raise FileNotFoundError("the snapshot disappeared")

    monkeypatch.setattr(runner.chunk_runner, "run_pytest", cannot_start)

    (chunk,) = runner.run_assigned_tests([PRISTINE_ID], "", REPO, SHA)

    assert chunk["success"] is False and chunk["stderr"] == "Could not start pytest (FileNotFoundError)"
    assert _chunk_dirs(tmp_path) == []


def test_the_chunk_directory_is_removed_when_the_run_is_interrupted(runner, coordinator, tmp_path, monkeypatch):
    coordinator.serve_code(REPO, SHA, make_tar(tmp_path / "snap.tgz", PRISTINE))

    def sigterm(*args, **kwargs):
        raise SystemExit(143)  # what the runner's SIGTERM handler raises

    monkeypatch.setattr(runner.chunk_runner, "run_pytest", sigterm)

    with pytest.raises(SystemExit):
        runner.run_assigned_tests([PRISTINE_ID], "", REPO, SHA)

    assert _chunk_dirs(tmp_path) == []
    assert sorted(p.name for p in _work(tmp_path).iterdir()) == ["archives", "chunks"]  # no temp leftovers
