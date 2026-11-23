"""Grid code delivery: snapshot a repo's committed state and unpack it for the pods.

Real git repositories are created on disk (see grid_testkit). Delivery to the coordinator and the
POST /api/grid/runs behaviour are in test_grid_start_run.py.
"""
import gzip
import hashlib
import io
import os
import subprocess
import tarfile
from pathlib import Path

import pytest
from grid_testkit import FILES, git, isolated_git, make_repo, repo, repos, tar_tree, tree  # noqa: F401

from amplify_server import grid_snapshot as gs


# ── repo, sha, dirty ──────────────────────────────────────────────────────────

def test_inspect_gives_the_head_sha_the_repo_name_and_clean_state(repo):
    info = gs.inspect_repo(str(repo))
    assert info.sha == git(repo, "rev-parse", "HEAD") and gs.SHA_RE.fullmatch(info.sha)
    assert info.repo == "tests-repo" and info.dirty is False
    assert info.top == Path(os.path.realpath(repo))


def test_a_folder_inside_the_repo_resolves_to_its_top_level(repo):
    assert gs.inspect_repo(str(repo / "tests")).top == Path(os.path.realpath(repo))


def test_the_sha_follows_new_commits(repo):
    first = gs.inspect_repo(str(repo)).sha
    (repo / "more.py").write_text("x = 1\n")
    git(repo, "add", "-A"), git(repo, "commit", "-q", "-m", "more")
    assert gs.inspect_repo(str(repo)).sha != first


@pytest.mark.parametrize("change", ["modify", "untracked", "staged", "delete"])
def test_uncommitted_changes_make_it_dirty(repo, change):
    if change == "modify":
        (repo / "README.md").write_text("changed\n")
    elif change == "untracked":
        (repo / "scratch.txt").write_text("x")
    elif change == "staged":
        (repo / "new.py").write_text("x")
        git(repo, "add", "new.py")
    else:
        (repo / "README.md").unlink()
    assert gs.inspect_repo(str(repo)).dirty is True


@pytest.mark.parametrize("folder, expected", [
    ("my repo!", "my-repo-"), (".hidden", "hidden"), ("__x", "x"), ("a" * 150, "a" * 100), ("...", "repo"),
    ("ünïcode", "n-code"), ("ok-name_1.2", "ok-name_1.2")])
def test_the_repo_name_is_made_to_fit_the_shared_rule(folder, expected):
    name = gs.repo_name(Path("/work") / folder)
    assert name == expected and gs.REPO_RE.fullmatch(name)


@pytest.mark.parametrize("ok", ["a", "A9", "x.y_z-1", "a" * 100])
def test_shared_repo_rule_accepts(ok):
    assert gs.REPO_RE.fullmatch(ok)


@pytest.mark.parametrize("bad", ["", ".x", "-x", "_x", "a/b", "..", ".", "a b", "a" * 101, "x\n", "é"])
def test_shared_repo_rule_refuses(bad):
    assert not gs.REPO_RE.fullmatch(bad)


@pytest.mark.parametrize("sha, ok", [("a" * 40, True), ("0123456789abcdef" * 2 + "01234567", True), ("A" * 40, False),
                                    ("a" * 39, False), ("a" * 41, False), ("g" * 40, False), ("a" * 40 + "\n", False)])
def test_shared_sha_rule(sha, ok):
    assert bool(gs.SHA_RE.fullmatch(sha)) is ok


# ── what is refused ───────────────────────────────────────────────────────────

def test_a_folder_that_is_not_a_git_work_tree_is_refused(repos):
    plain = repos / "plain"
    plain.mkdir()
    with pytest.raises(gs.SnapshotError, match="git work tree") as err:
        gs.inspect_repo(str(plain))
    assert err.value.status == 400 and str(plain) not in str(err.value)


def test_a_bare_repository_is_refused(repos):
    bare = repos / "bare.git"
    git(repos, "init", "-q", "--bare", str(bare))
    with pytest.raises(gs.SnapshotError, match="git work tree"):
        gs.inspect_repo(str(bare))


@pytest.mark.parametrize("path", ["", "   ", "relative/dir", "~", None, 5, "bad\x00path"])
def test_unusable_paths_are_refused(path):
    with pytest.raises(gs.SnapshotError):
        gs.inspect_repo(path)


def test_a_missing_folder_and_a_file_are_refused(repos, repo):
    for path in (repos / "nope", repo / "README.md"):
        with pytest.raises(gs.SnapshotError, match="not a folder"):
            gs.inspect_repo(str(path))


def test_a_repo_without_commits_is_refused(repos):
    empty = repos / "empty"
    empty.mkdir()
    git(empty, "init", "-q")
    with pytest.raises(gs.SnapshotError, match="no commits"):
        gs.inspect_repo(str(empty))


def test_amplifys_own_data_directory_is_off_limits(repos):
    data = repos / "amplify-data"
    data.mkdir()
    inside = make_repo(data, "secrets")
    for path in (data, inside, inside / "tests"):
        with pytest.raises(gs.SnapshotError, match="Amplify's own data") as err:
            gs.inspect_repo(str(path), protected_dirs=[data])
        assert str(data) not in str(err.value)


def test_a_repo_that_contains_the_data_directory_is_refused_when_pointed_at_it(repo):
    data = repo / "data"
    data.mkdir()
    with pytest.raises(gs.SnapshotError, match="Amplify's own data"):
        gs.inspect_repo(str(data), protected_dirs=[data])


def test_a_symlink_into_the_data_directory_does_not_get_around_it(repos):
    data = repos / "amplify-data"
    data.mkdir()
    inside = make_repo(data, "secrets")
    link = repos / "innocent"
    link.symlink_to(inside)
    with pytest.raises(gs.SnapshotError, match="Amplify's own data"):
        gs.inspect_repo(str(link), protected_dirs=[data])


def test_the_callers_own_clones_inside_the_data_directory_are_allowed_but_nobody_elses(repos):
    data = repos / "amplify-data"
    mine = make_repo(data / "workspaces" / "1" / "repos" if (data / "workspaces" / "1" / "repos").mkdir(parents=True) is None
                     else data, "mine")
    theirs = make_repo(data / "workspaces" / "2" / "repos" if (data / "workspaces" / "2" / "repos").mkdir(parents=True) is None
                       else data, "theirs")
    allowed = [data / "workspaces" / "1" / "repos"]
    assert gs.inspect_repo(str(mine), protected_dirs=[data], allowed_dirs=allowed).repo == "mine"
    with pytest.raises(gs.SnapshotError, match="Amplify's own data"):
        gs.inspect_repo(str(theirs), protected_dirs=[data], allowed_dirs=allowed)


# ── the snapshot ──────────────────────────────────────────────────────────────

def test_the_snapshot_is_what_git_archive_makes_and_leaves_out_everything_uncommitted(repo):
    (repo / "scratch.txt").write_text("untracked")
    (repo / "README.md").write_text("EDITED BUT NOT COMMITTED\n")
    info = gs.inspect_repo(str(repo))
    expected = tar_tree(subprocess.run(["git", "archive", "--format=tar", info.sha], cwd=repo, check=True,
                                       capture_output=True).stdout)
    with gs.build_snapshot(info) as snap:
        got = tar_tree(snap.path.read_bytes())
        assert snap.size == snap.path.stat().st_size
        assert snap.sha256 == hashlib.sha256(snap.path.read_bytes()).hexdigest()
    assert got == expected and "scratch.txt" not in got and got["README.md"] == b"# hello\n"
    assert set(got) == set(FILES)


def test_the_same_commit_always_gives_the_same_checksum(repo):
    info = gs.inspect_repo(str(repo))
    with gs.build_snapshot(info) as a, gs.build_snapshot(info) as b:
        assert a.sha256 == b.sha256


def test_the_temp_file_is_removed_afterwards(repo, tmp_path, monkeypatch):
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    with gs.build_snapshot(gs.inspect_repo(str(repo))) as snap:
        assert snap.path.exists() and snap.path.parent.samefile(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_a_snapshot_over_the_size_cap_is_refused_with_a_clear_message(repos, tmp_path, monkeypatch):
    big = make_repo(repos, "big", {"data/blob.bin": ""})
    (big / "data" / "blob.bin").write_bytes(os.urandom(3_000_000))          # does not compress
    git(big, "add", "-A"), git(big, "commit", "-q", "-m", "blob")
    monkeypatch.setattr(gs, "MAX_SNAPSHOT_BYTES", 1024 * 1024)
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    with pytest.raises(gs.SnapshotError, match=r"larger than 1 MiB") as err:
        with gs.build_snapshot(gs.inspect_repo(str(big))):
            pytest.fail("must not be built")
    assert err.value.status == 400
    assert list(tmp_path.iterdir()) == []


def test_the_default_cap_is_100_mib():
    assert gs.MAX_SNAPSHOT_BYTES == 100 * 1024 * 1024


def test_a_repo_with_a_committed_symlink_is_refused_up_front(repos, tmp_path, monkeypatch):
    linked = make_repo(repos, "linked")
    (linked / "alias").symlink_to("README.md")
    git(linked, "add", "-A"), git(linked, "commit", "-q", "-m", "link")
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    with pytest.raises(gs.SnapshotError, match="symlink") as err:
        with gs.build_snapshot(gs.inspect_repo(str(linked))):
            pytest.fail("must not be built")
    assert err.value.status == 400 and list(tmp_path.iterdir()) == []


def test_git_never_sees_the_servers_secrets(monkeypatch):
    for name in ("GRID_API_TOKEN", "JWT_SECRET_KEY", "JIRA_API_TOKEN", "ZEPHYR_SECRET_KEY", "SOME_PASSWORD"):
        monkeypatch.setenv(name, "hunter2")
    env = gs._git_env()
    assert not [k for k, v in env.items() if v == "hunter2"] and "PATH" in env and env["GIT_TERMINAL_PROMPT"] == "0"


def test_a_repo_cannot_swap_the_archive_program_through_its_own_config(repo, tmp_path):
    marker = tmp_path / "ran-by-config"
    git(repo, "config", "tar.tar.gz.command", f"touch {marker}; gzip -cn")
    git(repo, "config", "core.fsmonitor", f"touch {marker}")
    info = gs.inspect_repo(str(repo))
    with gs.build_snapshot(info) as snap:
        assert snap.size > 0
    assert not marker.exists()


def test_a_git_failure_is_reported_not_swallowed(repo):
    info = gs.RepoInfo(top=Path(os.path.realpath(repo)), repo="tests-repo", sha="0" * 40, dirty=False)
    with pytest.raises(gs.SnapshotError, match="git archive failed") as err:
        with gs.build_snapshot(info):
            pytest.fail("must not be built")
    assert err.value.status == 500


# ── safe extraction ───────────────────────────────────────────────────────────

def member(name, data=b"x", mode=0o644, kind=tarfile.REGTYPE, linkname=""):
    info = tarfile.TarInfo(name)
    info.type, info.mode, info.linkname = kind, mode, linkname
    info.size = len(data) if kind == tarfile.REGTYPE else 0
    return info, (data if kind == tarfile.REGTYPE else None)


def make_tar(path: Path, members) -> Path:
    with tarfile.open(path, "w:gz") as tar:
        for info, data in members:
            tar.addfile(info, io.BytesIO(data) if data is not None else None)
    return path


GOOD = [member("ok.txt", b"fine"), member("pkg/ok2.txt", b"also fine")]


@pytest.mark.parametrize("label, bad", [
    ("absolute path", member("/tmp/evil.txt")),
    ("dot-dot", member("../evil.txt")),
    ("nested dot-dot", member("a/../../evil.txt")),
    ("deep dot-dot", member("a/b/../../../evil.txt")),
    ("backslash dot-dot", member("..\\evil.txt")),
    ("drive letter", member("C:/evil.txt")),
    ("symlink", member("link", kind=tarfile.SYMTYPE, linkname="../outside")),
    ("symlink to absolute", member("link", kind=tarfile.SYMTYPE, linkname="/etc")),
    ("hardlink", member("hard", kind=tarfile.LNKTYPE, linkname="ok.txt")),
    ("character device", member("dev", kind=tarfile.CHRTYPE)),
    ("block device", member("blk", kind=tarfile.BLKTYPE)),
    ("fifo", member("pipe", kind=tarfile.FIFOTYPE)),
])
def test_unsafe_archives_are_refused_and_leave_nothing_behind(tmp_path, label, bad):
    archive = make_tar(tmp_path / "a.tar.gz", GOOD + [bad])          # good files first: nothing may be half-extracted
    out = tmp_path / "out"
    with pytest.raises(ValueError):
        gs.safe_extract(archive, out / "snapshot")
    assert not (out / "snapshot").exists(), label
    assert [p.name for p in out.iterdir()] == [], label                # no temp folder left either
    assert not (tmp_path / "evil.txt").exists() and not (out / "evil.txt").exists()


def test_a_symlink_cannot_be_used_to_write_outside(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    archive = make_tar(tmp_path / "a.tar.gz", [member("link", kind=tarfile.SYMTYPE, linkname=str(outside)),
                                                member("link/pwned.txt", b"x")])
    with pytest.raises(ValueError, match="symlink"):
        gs.safe_extract(archive, tmp_path / "out" / "snapshot")
    assert list(outside.iterdir()) == []


def test_too_many_entries_are_refused(tmp_path):
    archive = make_tar(tmp_path / "a.tar.gz", [member(f"f{i}.txt") for i in range(6)])
    with pytest.raises(ValueError, match="too many"):
        gs.safe_extract(archive, tmp_path / "out" / "s", max_members=5)
    assert not (tmp_path / "out" / "s").exists()
    assert gs.safe_extract(archive, tmp_path / "out" / "s", max_members=6) is True


def test_too_many_bytes_are_refused(tmp_path):
    archive = make_tar(tmp_path / "a.tar.gz", [member("a.bin", b"x" * 8), member("b.bin", b"y" * 8)])
    with pytest.raises(ValueError, match="size"):
        gs.safe_extract(archive, tmp_path / "out" / "s", max_bytes=15)
    assert [p.name for p in (tmp_path / "out").iterdir()] == []


def test_an_entry_that_claims_a_gigantic_size_is_refused_from_its_header(tmp_path):
    info = tarfile.TarInfo("huge.bin")
    info.size = 10 ** 12
    archive = tmp_path / "bomb.tar.gz"
    archive.write_bytes(gzip.compress(info.tobuf(tarfile.GNU_FORMAT) + b"\0" * 1024))
    with pytest.raises(ValueError, match="size"):
        gs.safe_extract(archive, tmp_path / "out" / "s")
    with pytest.raises(ValueError, match="size"):
        gs.check_archive(archive)


@pytest.mark.parametrize("content", [b"not a tarball at all", b"", gzip.compress(b"plain text, not tar")])
def test_files_that_are_not_tarballs_are_refused(tmp_path, content):
    bad = tmp_path / "bad.tar.gz"
    bad.write_bytes(content)
    with pytest.raises(ValueError):
        gs.safe_extract(bad, tmp_path / "out" / "s")
    assert not (tmp_path / "out" / "s").exists()


def test_a_truncated_tarball_leaves_nothing_behind(tmp_path):
    good = make_tar(tmp_path / "a.tar.gz", [member("big.bin", os.urandom(300_000))])
    cut = tmp_path / "cut.tar.gz"
    cut.write_bytes(good.read_bytes()[:150_000])
    with pytest.raises(ValueError):
        gs.safe_extract(cut, tmp_path / "out" / "s")
    assert [p.name for p in (tmp_path / "out").iterdir()] == []


def test_a_good_archive_unpacks_with_readable_permissions(tmp_path):
    dirinfo = tarfile.TarInfo("./pkg")
    dirinfo.type, dirinfo.mode = tarfile.DIRTYPE, 0o700
    archive = make_tar(tmp_path / "a.tar.gz", [
        member("./", kind=tarfile.DIRTYPE), (dirinfo, None), member("./pkg/mod.py", b"x = 1\n", mode=0o600),
        member("./run.sh", b"#!/bin/sh\n", mode=0o755), member("deep/er/file.txt", b"deep")])
    dest = tmp_path / "out" / "snapshot"
    assert gs.safe_extract(archive, dest) is True
    assert tree(dest) == {"pkg/mod.py": b"x = 1\n", "run.sh": b"#!/bin/sh\n", "deep/er/file.txt": b"deep"}
    assert dest.stat().st_mode & 0o777 == 0o755                        # the pods run as another user
    assert (dest / "run.sh").stat().st_mode & 0o777 == 0o755                  # executable stays executable
    assert (dest / "pkg" / "mod.py").stat().st_mode & 0o777 == 0o644          # a 0600 file is made readable
    assert (dest / "pkg").stat().st_mode & 0o777 == 0o755
    assert [p.name for p in dest.parent.iterdir()] == ["snapshot"]
    assert gs.safe_extract(archive, dest) is False                     # already there: untouched


# ── delivery ──────────────────────────────────────────────────────────────────

def never(*args, **kwargs):
    raise AssertionError("the coordinator must not be called")


def test_volume_delivery_unpacks_the_commit_into_repo_slash_sha(repo, tmp_path):
    vol = tmp_path / "tests-store"
    vol.mkdir()
    (repo / "scratch.txt").write_text("untracked")
    info = gs.inspect_repo(str(repo))
    assert gs.deliver(info, code_dir=str(vol), has_code=never, put_code=never) == "volume"
    snapshot = vol / "tests-repo" / info.sha
    assert tree(snapshot) == {k: v.encode() for k, v in FILES.items()}
    assert snapshot.stat().st_mode & 0o755 == 0o755 and (vol / "tests-repo").stat().st_mode & 0o055 == 0o055
    assert [p.name for p in (vol / "tests-repo").iterdir()] == [info.sha]       # no temp leftovers


def test_volume_delivery_skips_a_snapshot_that_is_already_there(repo, tmp_path, monkeypatch):
    vol = tmp_path / "tests-store"
    vol.mkdir()
    info = gs.inspect_repo(str(repo))
    gs.deliver(info, code_dir=str(vol), has_code=never, put_code=never)
    before = (vol / "tests-repo" / info.sha / "README.md").stat().st_mtime_ns
    monkeypatch.setattr(gs, "build_snapshot", never)                            # not even packaged again
    assert gs.deliver(info, code_dir=str(vol), has_code=never, put_code=never) == "volume"
    assert (vol / "tests-repo" / info.sha / "README.md").stat().st_mtime_ns == before


def test_two_commits_sit_side_by_side_on_the_volume(repo, tmp_path):
    vol = tmp_path / "tests-store"
    vol.mkdir()
    first = gs.inspect_repo(str(repo))
    gs.deliver(first, code_dir=str(vol), has_code=never, put_code=never)
    (repo / "README.md").write_text("v2\n")
    git(repo, "add", "-A"), git(repo, "commit", "-q", "-m", "v2")
    second = gs.inspect_repo(str(repo))
    gs.deliver(second, code_dir=str(vol), has_code=never, put_code=never)
    assert (vol / "tests-repo" / first.sha / "README.md").read_text() == "# hello\n"
    assert (vol / "tests-repo" / second.sha / "README.md").read_text() == "v2\n"


def test_a_file_where_the_snapshot_folder_should_be_is_an_error_not_a_silent_success(repo, tmp_path):
    vol = tmp_path / "tests-store"
    info = gs.inspect_repo(str(repo))
    (vol / "tests-repo").mkdir(parents=True)
    (vol / "tests-repo" / info.sha).write_text("in the way")
    with pytest.raises(gs.SnapshotError) as err:
        gs.deliver(info, code_dir=str(vol), has_code=never, put_code=never)
    assert err.value.status == 500


def test_a_code_dir_that_is_not_a_folder_is_a_server_error_without_the_path(repo, tmp_path):
    missing = tmp_path / "not-mounted"
    with pytest.raises(gs.SnapshotError) as err:
        gs.deliver(gs.inspect_repo(str(repo)), code_dir=str(missing), has_code=never, put_code=never)
    assert err.value.status == 500 and str(missing) not in str(err.value)


@pytest.mark.parametrize("repo_name, sha", [("..", "a" * 40), ("a/b", "a" * 40), ("ok", "../" + "a" * 37),
                                            ("ok", "A" * 40), ("", "a" * 40), (".x", "a" * 40)])
def test_the_volume_path_is_never_built_from_unchecked_names(tmp_path, repo_name, sha):
    with pytest.raises(gs.SnapshotError):
        gs.volume_target(tmp_path, repo_name, sha)


def test_the_volume_path_cannot_leave_the_volume_through_a_symlinked_repo_folder(tmp_path):
    vol, elsewhere = tmp_path / "vol", tmp_path / "elsewhere"
    vol.mkdir(), elsewhere.mkdir()
    (vol / "sneaky").symlink_to(elsewhere)
    with pytest.raises(gs.SnapshotError):
        gs.volume_target(vol, "sneaky", "a" * 40)
