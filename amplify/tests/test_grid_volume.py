"""The shared tests-store volume (GRID_CODE_DIR) and the archive limits shared with the runner pods:
folder permissions whatever the umask, retention of old snapshots, leftovers of crashed extractions, and the
tar member cap (100,000 — the runners refuse more, so Amplify must too)."""
import os
import tarfile
import time
from pathlib import Path

import pytest
from grid_testkit import FILES, git, isolated_git, make_repo, repo, repos, tree  # noqa: F401

from amplify import grid_snapshot as gs

DAY = 86400
SHA_A, SHA_B, SHA_C = "a" * 40, "b" * 40, "c" * 40


def never(*args, **kwargs):
    raise AssertionError("the coordinator must not be called")


def deliver(info, vol):
    return gs.deliver(info, code_dir=str(vol), has_code=never, put_code=never)


def age(path, days):
    """Make a folder look like it was last used `days` ago."""
    then = time.time() - days * DAY
    os.utime(path, (then, then))


def snapshot_folder(vol, repo_name, sha, days_old, files=None):
    folder = Path(vol) / repo_name / sha
    folder.mkdir(parents=True)
    for name, text in (files or {"tests/test_a.py": "x"}).items():
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text(text)
    age(folder, days_old)
    return folder


@pytest.fixture
def vol(tmp_path):
    folder = tmp_path / "tests-store"
    folder.mkdir()
    return folder


# ── the tar member cap is the runners' ────────────────────────────────────────

def empty_files_tar(path, count):
    with tarfile.open(path, "w:gz") as tar:
        for i in range(count):
            tar.addfile(tarfile.TarInfo(f"d{i // 1000}/f{i}.txt"))
    return path


def test_the_member_cap_is_the_runners_100_000():
    assert gs.MAX_MEMBERS == 100_000


@pytest.fixture(scope="module")
def over_the_cap(tmp_path_factory):
    """A tarball of 100,001 empty files (about 0.5 MiB): one more than the runners will unpack."""
    return empty_files_tar(tmp_path_factory.mktemp("big") / "over.tar.gz", 100_001)


def test_an_archive_one_entry_over_the_runners_cap_is_refused_at_packaging_and_at_extraction(over_the_cap, tmp_path):
    with pytest.raises(ValueError, match="too many"):
        gs.check_archive(over_the_cap)                      # what build_snapshot runs before anything is sent
    with pytest.raises(ValueError, match="too many"):
        gs.safe_extract(over_the_cap, tmp_path / "out" / "snapshot")
    assert not (tmp_path / "out" / "snapshot").exists()


def test_the_cap_is_exactly_the_default_limit(over_the_cap, monkeypatch):
    monkeypatch.setattr(gs, "MAX_MEMBERS", 100_001)         # one more entry allowed and the same archive passes
    gs.check_archive(over_the_cap)


def test_the_root_entry_of_an_archive_is_not_counted_like_the_runners_do(tmp_path):
    archive = tmp_path / "a.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        root = tarfile.TarInfo("./")
        root.type = tarfile.DIRTYPE
        tar.addfile(root)
        for i in range(3):
            tar.addfile(tarfile.TarInfo(f"f{i}.txt"))
    gs.check_archive(archive, max_members=3)
    with pytest.raises(ValueError, match="too many"):
        gs.check_archive(archive, max_members=2)


# ── permissions do not depend on the umask ────────────────────────────────────

def test_the_repo_and_snapshot_folders_are_readable_by_other_users_even_under_a_strict_umask(repo, vol):
    info = gs.inspect_repo(str(repo))
    old = os.umask(0o077)
    try:
        assert deliver(info, vol) == "volume"
    finally:
        os.umask(old)
    repo_dir, snap = vol / "tests-repo", vol / "tests-repo" / info.sha
    assert repo_dir.stat().st_mode & 0o777 == 0o755
    assert snap.stat().st_mode & 0o777 == 0o755
    assert (snap / "tests" / "test_a.py").stat().st_mode & 0o777 == 0o644
    assert (snap / "bin" / "run.sh").stat().st_mode & 0o777 == 0o755


def test_a_repo_folder_an_earlier_strict_umask_left_closed_is_opened(repo, vol):
    (vol / "tests-repo").mkdir(mode=0o700)
    os.chmod(vol / "tests-repo", 0o700)
    deliver(gs.inspect_repo(str(repo)), vol)
    assert (vol / "tests-repo").stat().st_mode & 0o777 == 0o755


# ── a run that reuses a snapshot keeps it alive ───────────────────────────────

def test_reusing_a_snapshot_touches_its_folder(repo, vol):
    info = gs.inspect_repo(str(repo))
    deliver(info, vol)
    snap = vol / "tests-repo" / info.sha
    age(snap, 20)
    before = snap.stat().st_mtime
    assert deliver(info, vol) == "volume"
    assert snap.stat().st_mtime > before + 19 * DAY and abs(snap.stat().st_mtime - time.time()) < 60


def test_a_snapshot_a_run_is_using_survives_a_prune_that_would_have_removed_it(repo, vol):
    info = gs.inspect_repo(str(repo))
    deliver(info, vol)
    age(vol / "tests-repo" / info.sha, 90)                  # idle for months…
    assert deliver(info, vol) == "volume"                   # …until a run uses it again
    assert (vol / "tests-repo" / info.sha / "README.md").is_file()


# ── retention ─────────────────────────────────────────────────────────────────

def test_old_snapshots_are_pruned_after_a_delivery_and_recent_ones_and_the_new_one_stay(repo, vol):
    old = snapshot_folder(vol, "tests-repo", SHA_A, 45)
    older_other_repo = snapshot_folder(vol, "other", SHA_B, 400)
    recent = snapshot_folder(vol, "tests-repo", SHA_C, 5)
    info = gs.inspect_repo(str(repo))
    deliver(info, vol)
    assert not old.exists() and not older_other_repo.exists()
    assert recent.is_dir() and (vol / "tests-repo" / info.sha).is_dir()


def test_the_snapshot_just_used_is_never_pruned_even_when_it_looks_ancient(vol):
    target = snapshot_folder(vol, "r", SHA_A, 400)
    assert gs.prune_volume(vol, keep=target) == 0 and target.is_dir()
    assert gs.prune_volume(vol) == 1 and not target.exists()                # without `keep` it would have gone


def test_retention_follows_grid_code_retention_days(vol, monkeypatch):
    a = snapshot_folder(vol, "r", SHA_A, 8)
    b = snapshot_folder(vol, "r", SHA_B, 3)
    monkeypatch.setenv("GRID_CODE_RETENTION_DAYS", "7")
    assert gs.prune_volume(vol) == 1 and not a.exists() and b.is_dir()


@pytest.mark.parametrize("raw, days", [("", 30), ("7", 7), ("0.5", 0.5), ("0", 0), (" 12 ", 12), ("abc", 30), ("-3", 30),
                                       ("nan", 30), ("inf", 30)])
def test_the_retention_setting(monkeypatch, raw, days):
    monkeypatch.setenv("GRID_CODE_RETENTION_DAYS", raw)
    assert gs.retention_days() == days


def test_the_default_retention_is_30_days(vol, monkeypatch):
    monkeypatch.delenv("GRID_CODE_RETENTION_DAYS", raising=False)
    keep, drop = snapshot_folder(vol, "r", SHA_A, 29), snapshot_folder(vol, "r", SHA_B, 31)
    gs.prune_volume(vol)
    assert keep.is_dir() and not drop.exists()


def test_a_retention_of_zero_keeps_snapshots_forever(vol, monkeypatch):
    old = snapshot_folder(vol, "r", SHA_A, 4000)
    monkeypatch.setenv("GRID_CODE_RETENTION_DAYS", "0")
    assert gs.prune_volume(vol) == 0 and old.is_dir()


def test_only_snapshot_folders_are_ever_removed(vol):
    """Anything that is not <repo>/<40 hex> is somebody else's: other folder names, files, the volume's top level."""
    keepers = [snapshot_folder(vol, "r", "notes", 900), snapshot_folder(vol, "r", SHA_A[:39], 900),
               snapshot_folder(vol, "r", SHA_A.upper(), 900), snapshot_folder(vol, "r", ".hidden", 900)]
    (vol / "r" / SHA_B).write_text("a file named like a sha")
    age(vol / "r" / SHA_B, 900)
    (vol / "loose-file").write_text("x")
    top_level_sha_folder = vol / SHA_C
    top_level_sha_folder.mkdir()
    age(top_level_sha_folder, 900)
    deep = vol / "r" / "deeper" / SHA_C
    deep.mkdir(parents=True)
    age(deep, 900)
    gs.prune_volume(vol)
    assert all(k.exists() for k in keepers) and (vol / "r" / SHA_B).is_file() and (vol / "loose-file").is_file()
    assert top_level_sha_folder.is_dir() and deep.is_dir()


def test_stale_half_extracted_folders_are_pruned_after_a_day_but_fresh_ones_are_left_alone(vol):
    stale = snapshot_folder(vol, "r", f".{SHA_A}-x1y2z3.tmp", 2)
    fresh = snapshot_folder(vol, "r", f".{SHA_B}-q9w8e7.tmp", 0.5)
    other_style = snapshot_folder(vol, "r", ".tmp-abc123", 3)
    not_ours = snapshot_folder(vol, "r", "scratch.tmp", 3)               # no leading dot: not made by us
    assert gs.prune_volume(vol) == 2
    assert not stale.exists() and not other_style.exists() and fresh.is_dir() and not_ours.is_dir()


def test_leftovers_go_even_when_snapshot_retention_is_switched_off(vol):
    stale = snapshot_folder(vol, "r", f".{SHA_A}-x1y2z3.tmp", 5)
    assert gs.prune_volume(vol, days=0) == 1 and not stale.exists()


# ── symlinks are never followed or deleted through ────────────────────────────

def test_a_symlinked_repo_folder_pointing_outside_the_volume_is_left_alone(tmp_path, vol, repo):
    outside = tmp_path / "outside"
    victim = snapshot_folder(outside, "inner", SHA_A, 900)
    stray = outside / f".{SHA_B}-zzz.tmp"
    stray.mkdir()
    age(stray, 900)
    (outside / SHA_C).mkdir()
    age(outside / SHA_C, 900)
    (vol / "linked-repo").symlink_to(outside, target_is_directory=True)
    deliver(gs.inspect_repo(str(repo)), vol)
    assert victim.is_dir() and stray.is_dir() and (outside / SHA_C).is_dir()
    assert (vol / "linked-repo").is_symlink()


def test_a_symlink_named_like_a_snapshot_is_neither_followed_nor_removed(tmp_path, vol):
    outside = tmp_path / "precious"
    outside.mkdir()
    (outside / "data.txt").write_text("keep me")
    age(outside, 900)
    (vol / "r").mkdir()
    link = vol / "r" / SHA_A
    link.symlink_to(outside, target_is_directory=True)
    tmp_link = vol / "r" / f".{SHA_B}-aaa.tmp"
    tmp_link.symlink_to(outside, target_is_directory=True)
    assert gs.prune_volume(vol) == 0
    assert (outside / "data.txt").read_text() == "keep me" and link.is_symlink() and tmp_link.is_symlink()


def test_a_symlink_inside_an_old_snapshot_is_not_followed_when_the_snapshot_is_removed(tmp_path, vol):
    outside = tmp_path / "precious"
    outside.mkdir()
    (outside / "data.txt").write_text("keep me")
    old = snapshot_folder(vol, "r", SHA_A, 900)
    (old / "dir-link").symlink_to(outside, target_is_directory=True)
    (old / "file-link").symlink_to(outside / "data.txt")
    age(old, 900)                                           # adding the links touched the folder
    assert gs.prune_volume(vol) == 1 and not old.exists()
    assert (outside / "data.txt").read_text() == "keep me"


def test_a_code_dir_that_is_itself_a_symlink_is_pruned_through_its_real_location(tmp_path, vol):
    old = snapshot_folder(vol, "r", SHA_A, 900)
    alias = tmp_path / "alias"
    alias.symlink_to(vol, target_is_directory=True)
    assert gs.prune_volume(alias) == 1 and not old.exists()


# ── bounded, and it never raises ──────────────────────────────────────────────

def test_one_pass_removes_at_most_a_few_folders_and_the_rest_wait_for_the_next(vol, monkeypatch):
    monkeypatch.setattr(gs, "PRUNE_MAX_REMOVALS", 3)
    shas = [f"{i:040x}" for i in range(1, 9)]
    for sha in shas:
        snapshot_folder(vol, "r", sha, 900)
    assert gs.prune_volume(vol) == 3 and len(list((vol / "r").iterdir())) == 5
    assert gs.prune_volume(vol) == 3 and gs.prune_volume(vol) == 2 and list((vol / "r").iterdir()) == []


def test_one_pass_looks_at_a_bounded_number_of_entries(vol, monkeypatch):
    monkeypatch.setattr(gs, "PRUNE_MAX_ENTRIES", 4)
    for i in range(1, 9):
        snapshot_folder(vol, "r", f"{i:040x}", 900)
    assert gs.prune_volume(vol) <= 4


def test_one_pass_stops_when_its_time_is_up(vol, monkeypatch):
    for i in range(1, 6):
        snapshot_folder(vol, "r", f"{i:040x}", 900)
    monkeypatch.setattr(gs, "PRUNE_TIME_BUDGET", -1)
    assert gs.prune_volume(vol) == 0 and len(list((vol / "r").iterdir())) == 5


def test_pruning_never_raises(tmp_path, vol, monkeypatch):
    assert gs.prune_volume(tmp_path / "missing") == 0
    assert gs.prune_volume(vol / "nope", keep=tmp_path) == 0
    snapshot_folder(vol, "r", SHA_A, 900)
    monkeypatch.setattr(gs.shutil, "rmtree", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk on fire")))
    assert gs.prune_volume(vol) == 0


def test_a_prune_that_blows_up_does_not_fail_the_delivery(repo, vol, monkeypatch):
    monkeypatch.setattr(gs.os, "scandir", lambda *a, **k: (_ for _ in ()).throw(PermissionError("denied")))
    info = gs.inspect_repo(str(repo))
    assert deliver(info, vol) == "volume"
    assert tree(vol / "tests-repo" / info.sha) == {k: v.encode() for k, v in FILES.items()}


def test_pruning_stays_inside_the_volume_when_the_repo_name_is_odd(repo, tmp_path, vol):
    outside = tmp_path / "bystander"
    old_outside = snapshot_folder(outside, "tests-repo", SHA_A, 900)
    deliver(gs.inspect_repo(str(repo)), vol)
    assert old_outside.is_dir()
