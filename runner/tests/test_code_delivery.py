"""resolve_code (volume, then checked coordinator download, then failure) and safe tar extraction."""
import io
import subprocess
import tarfile

import pytest

from runner_fakes import REPO, SHA, make_tar

GOOD = {"tests/test_snap.py": b"def test_ok():\n    assert True\n", "README.md": b"hello\n"}


def _fresh_dir(found, tmp_path):
    """True when `found` is a per-chunk download: <work>/chunks/<random>/<repo>/<sha>."""
    rel = found.relative_to(tmp_path / "work" / "chunks")
    return len(rel.parts) == 3 and rel.parts[1:] == (REPO, SHA)


def _leftovers(tmp_path):
    chunks = tmp_path / "work" / "chunks"
    return sorted(chunks.iterdir()) if chunks.exists() else []


# ---------------------------------------------------------------- resolve_code

@pytest.mark.parametrize("repo", ["", ".", "..", "../x", "a/b", "-lead", "x" * 101, "a b", "repo\n", None])
def test_invalid_repo_is_refused_before_any_path_or_request(runner, coordinator, repo):
    assert runner.resolve_code(repo, SHA) is None
    assert coordinator.requests == []


@pytest.mark.parametrize("sha", ["", "abc", "A" * 40, "g" * 40, "a" * 39, "a" * 41, "a" * 40 + "\n", "../" + "a" * 37, None])
def test_invalid_sha_is_refused_before_any_path_or_request(runner, coordinator, sha):
    assert runner.resolve_code(REPO, sha) is None
    assert coordinator.requests == []


def test_volume_snapshot_wins_and_the_coordinator_is_not_asked(runner, coordinator, tmp_path):
    snap = tmp_path / "code-root" / REPO / SHA
    snap.mkdir(parents=True)
    (snap / "marker.txt").write_text("from the volume")
    coordinator.serve_code(REPO, SHA, make_tar(tmp_path / "x.tgz", GOOD))

    found = runner.resolve_code(REPO, SHA)

    assert found == snap.resolve()
    assert coordinator.requests == []


def test_volume_entry_that_escapes_the_root_is_ignored(runner, coordinator, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "code-root" / REPO).mkdir()
    (tmp_path / "code-root" / REPO / SHA).symlink_to(outside)
    coordinator.serve_code(REPO, SHA, make_tar(tmp_path / "x.tgz", GOOD))

    found = runner.resolve_code(REPO, SHA)

    assert _fresh_dir(found, tmp_path)  # fell through to the coordinator
    assert (found / "tests" / "test_snap.py").is_file()


def test_download_verifies_extracts_and_keeps_the_archive(runner, coordinator, tmp_path, capsys):
    coordinator.serve_code(REPO, SHA, make_tar(tmp_path / "x.tgz", GOOD))

    found = runner.resolve_code(REPO, SHA)

    assert _fresh_dir(found, tmp_path)
    assert (found / "tests" / "test_snap.py").read_bytes() == GOOD["tests/test_snap.py"]
    (head, get) = [(m, p) for m, p, _, _ in coordinator.requests]
    assert head == ("HEAD", f"/api/code/{REPO}/{SHA}") and get == ("GET", f"/api/code/{REPO}/{SHA}")
    assert all(r[2]["X-API-Token"] == "secret-token-value" for r in coordinator.requests)
    assert "secret-token-value" not in capsys.readouterr().out
    assert sorted(p.name for p in (tmp_path / "work").iterdir()) == ["archives", "chunks"]  # no temp leftovers
    archive = tmp_path / "work" / "archives" / REPO / f"{SHA}.tar.gz"
    assert archive.read_bytes() == (tmp_path / "x.tgz").read_bytes()
    assert [p.name for p in archive.parent.iterdir()] == [archive.name]

    second = runner.resolve_code(REPO, SHA)  # second chunk: asks again, but needs no new download
    assert second != found and _fresh_dir(second, tmp_path)
    assert [m for m, *_ in coordinator.requests] == ["HEAD", "GET", "HEAD"]


@pytest.mark.parametrize("checksum", ["0" * 64, "f" * 63, "not-hex", None])
def test_download_with_a_bad_or_missing_checksum_is_refused(runner, coordinator, tmp_path, checksum):
    coordinator.serve_code(REPO, SHA, make_tar(tmp_path / "x.tgz", GOOD), checksum=checksum)

    assert runner.resolve_code(REPO, SHA) is None
    assert not any(p.is_file() for p in (tmp_path / "work").rglob("*"))  # nothing cached, nothing unpacked
    assert _leftovers(tmp_path) == []


def test_download_over_the_size_limit_is_refused(runner, coordinator, tmp_path, monkeypatch):
    coordinator.serve_code(REPO, SHA, make_tar(tmp_path / "x.tgz", GOOD))
    monkeypatch.setattr(runner, "CODE_MAX_BYTES", 10)

    assert runner.resolve_code(REPO, SHA) is None
    assert _leftovers(tmp_path) == []


def test_download_of_a_hostile_archive_with_a_valid_checksum_is_refused(runner, coordinator, tmp_path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo("../escape.txt")
        info.size = 1
        tar.addfile(info, io.BytesIO(b"x"))
    coordinator.serve_code(REPO, SHA, buf.getvalue())  # checksum matches: only the content is bad

    assert runner.resolve_code(REPO, SHA) is None
    assert not (tmp_path / "escape.txt").exists()
    assert _leftovers(tmp_path) == []  # the half-made per-chunk directory is removed again


def test_coordinator_404_and_unreachable_coordinator_give_none(runner, coordinator, monkeypatch):
    assert runner.resolve_code(REPO, SHA) is None  # 404

    monkeypatch.setattr(runner, "COORDINATOR_URL", "http://127.0.0.1:1")  # nothing listens there
    assert runner.resolve_code(REPO, "b" * 40) is None  # connection refused, no exception


def test_unavailable_code_becomes_a_failed_chunk_without_running_pytest(runner, tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("pytest must not be started")

    monkeypatch.setattr(subprocess, "Popen", boom)

    (chunk,) = runner.run_assigned_tests(["tests/test_snap.py::test_ok"], "qa", REPO, SHA)

    assert chunk["success"] is False
    assert chunk["test_results"] == []
    assert chunk["stderr"] == (
        f"Code for {REPO}@{SHA[:7]} is not available "
        f"(looked in {tmp_path / 'code-root'} and on the coordinator)"
    )
    assert chunk["tests"] == ["tests/test_snap.py::test_ok"]
    assert chunk["environment"] == "qa"
    assert chunk["browser"] == "chromium"


def test_failed_chunk_message_is_sanitised_for_a_hostile_reference(runner):
    (chunk,) = runner.run_assigned_tests(["t.py::x"], "", "bad repo\n<script>", "zz")
    assert "<" not in chunk["stderr"] and "\n" not in chunk["stderr"]
    assert chunk["success"] is False


# ---------------------------------------------------------------- safe_extract_tar

def _tar_with(path, build):
    """A .tar.gz whose FIRST member is fine and whose later members come from build(tar)."""
    with tarfile.open(path, "w:gz") as tar:
        good = tarfile.TarInfo("good.txt")
        good.size = 2
        tar.addfile(good, io.BytesIO(b"ok"))
        build(tar)


def _file(name, data=b"x"):
    def build(tar):
        info = tarfile.TarInfo(name)
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return build


def _special(name, kind, linkname=""):
    def build(tar):
        info = tarfile.TarInfo(name)
        info.type = kind
        info.linkname = linkname
        tar.addfile(info)
    return build


def _attacks(tmp_path):
    return {
        "absolute path": _file(str(tmp_path / "abs-escape.txt")),
        "dotdot": _file("../escape.txt"),
        "nested dotdot": _file("a/../../escape.txt"),
        "dotdot directory": _special("../escape-dir", tarfile.DIRTYPE),
        "symlink to absolute": _special("link", tarfile.SYMTYPE, "/etc"),
        "symlink out of the tree": _special("link", tarfile.SYMTYPE, "../outside"),
        "symlink inside the tree": _special("link", tarfile.SYMTYPE, "good.txt"),
        "dotdot that stays inside": _file("a/../inside.txt"),
        "hardlink": _special("hard", tarfile.LNKTYPE, "good.txt"),
        "character device": _special("dev-c", tarfile.CHRTYPE),
        "block device": _special("dev-b", tarfile.BLKTYPE),
        "fifo": _special("pipe", tarfile.FIFOTYPE),
    }


ATTACK_NAMES = ["absolute path", "dotdot", "nested dotdot", "dotdot directory", "symlink to absolute",
                "symlink out of the tree", "symlink inside the tree", "dotdot that stays inside", "hardlink",
                "character device", "block device", "fifo"]


@pytest.mark.parametrize("attack", ATTACK_NAMES)
def test_every_tar_attack_is_refused_and_leaves_nothing_behind(runner, tmp_path, attack):
    out = tmp_path / "out"
    out.mkdir()
    archive = tmp_path / "attack.tar.gz"
    _tar_with(archive, _attacks(tmp_path)[attack])

    with pytest.raises(ValueError):
        runner.safe_extract_tar(archive, out / "snap")

    assert list(out.iterdir()) == []  # not even the good first member, nor a temp dir
    assert not (tmp_path / "abs-escape.txt").exists()
    assert not (tmp_path / "escape.txt").exists()


def test_too_many_members_is_refused(runner, tmp_path):
    out = tmp_path / "out"
    archive = tmp_path / "many.tar.gz"
    make_tar(archive, {f"f{i}.txt": b"x" for i in range(6)})

    with pytest.raises(ValueError, match="too many"):
        runner.safe_extract_tar(archive, out / "snap", max_members=5)

    assert list(out.iterdir()) == []


def test_too_many_unpacked_bytes_is_refused_even_when_the_header_lies_about_size(runner, tmp_path):
    out = tmp_path / "out"
    archive = tmp_path / "big.tar.gz"
    make_tar(archive, {"a.bin": b"x" * 60, "b.bin": b"y" * 60})
    with pytest.raises(ValueError, match="too large"):
        runner.safe_extract_tar(archive, out / "snap", max_bytes=100)
    assert list(out.iterdir()) == []

    huge = tmp_path / "huge.tar.gz"  # header claims 1 TB, almost no data follows
    with tarfile.open(huge, "w:gz") as tar:
        info = tarfile.TarInfo("huge.bin")
        info.size = 10**12
        tar.addfile(info)
    with pytest.raises(ValueError):
        runner.safe_extract_tar(huge, out / "snap")
    assert list(out.iterdir()) == []


@pytest.mark.parametrize("payload", [b"not an archive at all", b"", b"\x1f\x8b\x08\x00garbage-after-the-gzip-magic"])
def test_corrupt_or_foreign_input_is_refused(runner, tmp_path, payload):
    out = tmp_path / "out"
    archive = tmp_path / "bad.tar.gz"
    archive.write_bytes(payload)

    with pytest.raises(ValueError):
        runner.safe_extract_tar(archive, out / "snap")

    assert list(out.iterdir()) == []


def test_truncated_archive_is_refused(runner, tmp_path):
    out = tmp_path / "out"
    archive = tmp_path / "t.tar.gz"
    data = make_tar(archive, {"f.bin": bytes(range(256)) * 400})
    archive.write_bytes(data[: len(data) // 2])

    with pytest.raises(ValueError):
        runner.safe_extract_tar(archive, out / "snap")

    assert list(out.iterdir()) == []


def test_good_archive_extracts_with_leading_dot_slash_stripped(runner, tmp_path):
    archive = tmp_path / "ok.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        root = tarfile.TarInfo("./")
        root.type = tarfile.DIRTYPE
        tar.addfile(root)
        sub = tarfile.TarInfo("./pkg")
        sub.type = tarfile.DIRTYPE
        sub.mode = 0o755
        tar.addfile(sub)
        for name, mode in (("./pkg/mod.py", 0o644), ("./run.sh", 0o4755)):  # setuid must not survive
            info = tarfile.TarInfo(name)
            info.size = 3
            info.mode = mode
            tar.addfile(info, io.BytesIO(b"abc"))

    dest = runner.safe_extract_tar(archive, tmp_path / "out" / "snap")

    assert (dest / "pkg" / "mod.py").read_bytes() == b"abc"
    assert (dest / "run.sh").stat().st_mode & 0o4000 == 0
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == ["snap"]


def test_extracting_onto_an_existing_snapshot_keeps_it(runner, tmp_path):
    first, second = tmp_path / "1.tgz", tmp_path / "2.tgz"
    make_tar(first, {"a.txt": b"first"})
    make_tar(second, {"a.txt": b"second"})
    dest = runner.safe_extract_tar(first, tmp_path / "out" / "snap")

    runner.safe_extract_tar(second, dest)  # lost the race: no error, no leftovers

    assert (dest / "a.txt").read_bytes() == b"first"
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == ["snap"]
