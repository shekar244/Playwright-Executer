"""Code delivery for Grid runs: a snapshot of a repo's committed state, handed to the runner pods.

A run carries (repo, sha). The pods run the leased tests from an immutable copy of that repo at that
commit, taken from a shared read-only volume when one is mounted, otherwise from the coordinator. This
module (no Flask) does the Amplify side:

  inspect_repo     the repo's git top-level, its HEAD sha and whether there are uncommitted changes
  build_snapshot   `git archive --format=tar.gz <sha>` into a temp file (size capped, SHA-256 computed)
  safe_extract     unpack a tarball all-or-nothing, refusing links, devices and path escapes
  deliver          put the snapshot where the pods will find it: the volume, or the coordinator
  prune_volume     after a delivery to the volume: drop snapshots nobody used for GRID_CODE_RETENTION_DAYS
                   and stale half-extracted folders (bounded, never follows a symlink, never raises)

The coordinator calls are passed in (`has_code`, `put_code`) so this module never imports the routes.
Every git call is an argument list with a timeout; git runs with the user's repo as cwd and nothing else does.
"""
from __future__ import annotations

import contextlib
import hashlib
import math
import os
import random
import re
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Iterator

# The same rules as the coordinator and the runners — validated before any path is built from them.
# Always applied with fullmatch: with match(), "$" would let a trailing newline through.
REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")

MAX_SNAPSHOT_BYTES = 100 * 1024 * 1024        # the coordinator refuses more than this (MAX_CODE_BYTES)
MAX_MEMBERS = 100_000                         # entries in a tarball we are willing to unpack (the runners' MAX_TAR_MEMBERS)
MAX_UNPACKED_BYTES = 1024 * 1024 * 1024       # total size of the files in it
GIT_TIMEOUT = 60                              # rev-parse / status
ARCHIVE_TIMEOUT = 120                         # git archive of a big repo (a request may take 300 s: this + the upload)
_CHUNK = 1024 * 1024

DEFAULT_RETENTION_DAYS = 30                   # GRID_CODE_RETENTION_DAYS: unused snapshot folders older than this are pruned
STALE_TMP_SECONDS = 24 * 3600                 # half-extracted folders (.<sha>-xxxx.tmp) older than this are leftovers of a crash
PRUNE_MAX_ENTRIES, PRUNE_MAX_REMOVALS, PRUNE_TIME_BUDGET = 5000, 10, 5.0     # one prune pass never does more than this

# What git must not inherit from the server's environment: the repo is chosen by the working directory.
_GIT_ENV_DROP = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
                 "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE")


class SnapshotError(ValueError):
    """Something about the repo or the delivery stops the run. The message is fit to show the user (it
    never contains a server path) and `status` is the HTTP status to answer with."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class RepoInfo:
    top: Path        # git top-level directory
    repo: str        # REPO_RE-clean name (the top-level's folder name)
    sha: str         # HEAD commit
    dirty: bool      # uncommitted changes exist (they are NOT in the snapshot)
    subpath: str = ""   # the folder that was asked for, relative to `top` ("" when it IS the top-level)


@dataclass(frozen=True)
class Snapshot:
    path: Path
    size: int
    sha256: str


# ── Git ───────────────────────────────────────────────────────────────────────

# Names of server secrets (GRID_API_TOKEN, JWT_SECRET_KEY, …): git, and anything a repo's config makes it run,
# never needs them.
_SECRET_NAME = re.compile(r"TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|ACCESS_KEY|API_KEY|PRIVATE_KEY|_KEY$", re.I)


def _git_env() -> dict:
    env = {k: v for k, v in os.environ.items() if k not in _GIT_ENV_DROP and not _SECRET_NAME.search(k)}
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_OPTIONAL_LOCKS"] = "0"            # `status` must not take the index lock the user's own git may want
    return env


def _git_cmd(*args: str) -> list[str]:
    # core.fsmonitor=false: a repo's own config must not make `git status` run a program of its choosing.
    return ["git", "-c", "core.fsmonitor=false", *args]


def _git(cwd: Path | str, *args: str, timeout: int = GIT_TIMEOUT) -> tuple[int, str]:
    """(exit code, stdout) of a git command run in `cwd`. Never raises: a missing git or a timeout is code -1."""
    try:
        proc = subprocess.run(_git_cmd(*args), cwd=str(cwd), capture_output=True, timeout=timeout,
                              env=_git_env(), stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return -1, ""
    return proc.returncode, os.fsdecode(proc.stdout).strip()


def _real(path: Path | str) -> Path:
    return Path(os.path.realpath(path))


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _blocked(path: Path, protected: list[Path], allowed: list[Path]) -> bool:
    return any(_inside(path, p) for p in protected) and not any(_inside(path, a) for a in allowed)


def repo_name(top: Path | str) -> str:
    """The top-level folder's name made to fit REPO_RE: illegal characters become '-', and the first
    character must be a letter or digit."""
    name = re.sub(r"[^A-Za-z0-9._-]", "-", Path(top).name).lstrip("._-")[:100]
    return name or "repo"


def inspect_repo(path: str, *, protected_dirs: Iterable[Path | str] = (),
                 allowed_dirs: Iterable[Path | str] = ()) -> RepoInfo:
    """Find the git work tree `path` belongs to and say what a snapshot of it would be.

    `protected_dirs` are Amplify's own data directories: nothing inside them can be snapshotted (they hold
    the database, secrets and other users' work) except what is inside `allowed_dirs` (the caller's own
    clones). Both the given path and the git top-level are checked, after resolving symlinks.
    """
    if not shutil.which("git"):
        raise SnapshotError("git is not installed on this server", 500)
    if not isinstance(path, str) or not path.strip() or "\x00" in path:
        raise SnapshotError("repo_path must be a folder path")
    path = path.strip()
    if not os.path.isabs(path):
        raise SnapshotError("repo_path must be an absolute path")
    given = _real(path)
    if not given.is_dir():
        raise SnapshotError("repo_path is not a folder on this server")
    protected, allowed = [_real(p) for p in protected_dirs], [_real(p) for p in allowed_dirs]
    refusal = "That folder is part of Amplify's own data and can't be sent to the Grid"
    if _blocked(given, protected, allowed):
        raise SnapshotError(refusal)

    code, out = _git(given, "rev-parse", "--show-toplevel")
    if code != 0 or not out:
        raise SnapshotError("repo_path is not inside a git work tree (or git cannot read it)")
    top = _real(out)
    if _blocked(top, protected, allowed):
        raise SnapshotError(refusal)

    code, sha = _git(top, "rev-parse", "HEAD")
    if code != 0:
        raise SnapshotError("The repo has no commits yet — commit your tests so there is something to run")
    if not SHA_RE.fullmatch(sha):
        raise SnapshotError("The repo's commit id is not a 40-character SHA-1 (unsupported object format)")

    code, status = _git(top, "status", "--porcelain")
    dirty = True if code != 0 else bool(status)      # can't tell: warn rather than claim it is clean
    try:
        subpath = given.relative_to(top).as_posix()
    except ValueError:                                  # not under the top-level it reports: never treat it as the root
        subpath = given.name or "?"
    return RepoInfo(top=top, repo=repo_name(top), sha=sha, dirty=dirty, subpath="" if subpath == "." else subpath)


# ── Building the snapshot ─────────────────────────────────────────────────────

@contextlib.contextmanager
def build_snapshot(info: RepoInfo, *, cap: int | None = None, timeout: int | None = None) -> Iterator[Snapshot]:
    """`git archive --format=tar.gz <sha>` into a temp file that is deleted when the block ends.

    Refused (SnapshotError) when the archive passes `cap` bytes, when git fails, or when the tarball
    holds anything the pods would refuse to unpack (a symlink, say) — better told here than as a failed chunk."""
    cap = MAX_SNAPSHOT_BYTES if cap is None else cap
    timeout = ARCHIVE_TIMEOUT if timeout is None else timeout
    fd, name = tempfile.mkstemp(prefix="amplify-snapshot-", suffix=".tar.gz")
    path = Path(name)
    try:
        with os.fdopen(fd, "wb") as out:
            size, digest = _run_archive(info, out, cap, timeout)
        try:
            check_archive(path)
        except ValueError as exc:
            raise SnapshotError(f"The committed files can't be sent to the Grid: {exc}") from None
        yield Snapshot(path, size, digest)
    finally:
        path.unlink(missing_ok=True)


def _run_archive(info: RepoInfo, out, cap: int, timeout: int) -> tuple[int, str]:
    # tar.tar.gz.command is git's own default, stated here so a repo's config cannot swap in another program.
    proc = subprocess.Popen(_git_cmd("-c", "tar.tar.gz.command=gzip -cn", "archive", "--format=tar.gz", info.sha),
                            cwd=str(info.top),
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                            env=_git_env())
    timed_out = threading.Event()
    timer = threading.Timer(timeout, lambda: (timed_out.set(), proc.kill()))
    timer.start()
    digest, size, too_big = hashlib.sha256(), 0, False
    try:
        for block in iter(lambda: proc.stdout.read(_CHUNK), b""):
            size += len(block)
            if size > cap:
                too_big = True
                proc.kill()
                break
            digest.update(block)
            out.write(block)
    finally:
        timer.cancel()
        proc.stdout.close()
        code = proc.wait()
    if too_big:
        raise SnapshotError(f"The repo's committed files are larger than {cap // (1024 * 1024)} MiB "
                            "once packed — trim large files out of the repo or use a smaller test repo")
    if timed_out.is_set():
        raise SnapshotError(f"Packaging the repo took longer than {timeout} s", 500)
    if code != 0:
        raise SnapshotError(f"Could not package the repo (git archive failed with exit code {code})", 500)
    return size, digest.hexdigest()


# ── Safe extraction (the same rules every extractor in this project follows) ──

def _clean_name(name: str) -> str:
    """The member name without a leading './'; ValueError for an absolute path, a '..' segment or a NUL."""
    if "\x00" in name:
        raise ValueError("the archive has an entry with an invalid name")
    flat = name.replace("\\", "/")
    if flat.startswith("/") or re.match(r"^[A-Za-z]:", flat):
        raise ValueError("the archive has an absolute path")
    parts = [p for p in flat.split("/") if p not in ("", ".")]
    if ".." in parts:
        raise ValueError("the archive has a '..' path segment")
    return "/".join(parts)


def _vet_members(tar: tarfile.TarFile, max_members: int, max_bytes: int) -> list[tarfile.TarInfo]:
    """Every member that should be unpacked, checked one by one (stopping at the first violation, so a
    tarball claiming terabytes is refused from its header, before its data is read)."""
    members, total = [], 0
    for member in tar:
        if len(members) >= max_members:                # counted exactly as the runners count: the root entry is free
            raise ValueError("the archive has too many entries")
        if member.type not in (tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE):
            kind = ("a symlink" if member.issym() else "a hard link" if member.islnk()
                    else "a device or fifo" if member.isdev() else "an unsupported entry")
            raise ValueError(f"the archive contains {kind} ({Path(member.name).name or member.name})")
        name = _clean_name(member.name)
        if member.isfile():
            total += member.size
            if total > max_bytes:
                raise ValueError("the archive unpacks to more than the allowed size")
        if not name:                                  # the archive's own root entry ('./')
            continue
        member.name = name
        members.append(member)
    return members


def _open_tar(archive: Path | str) -> tarfile.TarFile:
    try:
        return tarfile.open(archive, mode="r:gz")
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise ValueError("the archive is not a valid .tar.gz") from exc


def check_archive(archive: Path | str, *, max_members: int | None = None, max_bytes: int | None = None) -> None:
    """Raise ValueError unless every entry of the tarball passes the safety rules. Extracts nothing."""
    with _open_tar(archive) as tar:
        try:
            _vet_members(tar, max_members or MAX_MEMBERS, max_bytes or MAX_UNPACKED_BYTES)
        except (tarfile.TarError, OSError, EOFError) as exc:
            raise ValueError("the archive is damaged") from exc


def _chmod_755(path: Path) -> None:
    """Make a folder readable and traversable by every uid, whatever the umask. Best effort: a folder some
    other uid owns can't be changed, and is no worse off than it was."""
    try:
        os.chmod(path, 0o755)
    except OSError:
        pass


def _make_readable(root: Path) -> None:
    """Folders 755, files 755 if the owner could execute them, else 644 — so a reader on another uid can use it."""
    os.chmod(root, 0o755)
    for base, dirs, files in os.walk(root):
        for name in dirs:
            os.chmod(os.path.join(base, name), 0o755)
        for name in files:
            path = os.path.join(base, name)
            os.chmod(path, 0o755 if os.stat(path).st_mode & 0o100 else 0o644)


def safe_extract(archive: Path | str, dest: Path | str, *, max_members: int | None = None,
                 max_bytes: int | None = None) -> bool:
    """Unpack a .tar.gz at `dest`, all or nothing: into a temp folder beside it, then renamed into place.

    Only regular files and folders are allowed (no links, devices or fifos); absolute paths and '..' are
    refused; member count and total size are capped. Any violation raises ValueError and leaves nothing
    behind. Permissions are normalised (see _make_readable). Returns False — without touching it — when `dest` already exists (or appears meanwhile)."""
    dest = Path(dest)
    if dest.exists():
        return False
    dest.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    _chmod_755(dest.parent)                            # mkdir's mode is cut by the umask; the pods run as another user
    tmp = Path(tempfile.mkdtemp(prefix=f".{dest.name}-", suffix=".tmp", dir=dest.parent))
    try:
        with _open_tar(archive) as tar:
            try:
                members = _vet_members(tar, max_members or MAX_MEMBERS, max_bytes or MAX_UNPACKED_BYTES)
                tar.extractall(tmp, members=members, filter="data")
            except (tarfile.TarError, OSError, EOFError) as exc:      # FilterError, truncated data, bad layout
                raise ValueError("the archive could not be unpacked safely") from exc
        _make_readable(tmp)                            # mkdtemp is private; the pods run as another user
        try:
            os.rename(tmp, dest)
        except OSError:
            if dest.is_dir():                          # another request delivered the same snapshot first
                return False
            raise
        _chmod_755(dest)
        return True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── Delivery ──────────────────────────────────────────────────────────────────

def volume_target(code_dir: str | Path, repo: str, sha: str) -> Path:
    """<code_dir>/<repo>/<sha> — validated first, and shown to stay inside the volume."""
    if not REPO_RE.fullmatch(repo or "") or not SHA_RE.fullmatch(sha or ""):
        raise SnapshotError("Invalid repo name or commit id")
    root = _real(code_dir)
    target = root / repo / sha
    if root not in target.resolve().parents:
        raise SnapshotError("Invalid repo name or commit id")
    return target


def retention_days() -> float:
    """GRID_CODE_RETENTION_DAYS: how long a snapshot folder that no run has used is kept (default 30; 0 keeps
    them for ever). An unreadable or negative value means the default."""
    raw = os.environ.get("GRID_CODE_RETENTION_DAYS", "").strip()
    try:
        days = float(raw) if raw else DEFAULT_RETENTION_DAYS
    except ValueError:
        return DEFAULT_RETENTION_DAYS
    return days if math.isfinite(days) and days >= 0 else DEFAULT_RETENTION_DAYS


def _real_subdirs(path: Path | str) -> list[os.DirEntry]:
    """The real sub-folders of `path` — a symlink, even one that points at a folder, is not one. Never raises."""
    try:
        with os.scandir(path) as it:
            return [e for e in it if e.is_dir(follow_symlinks=False)]
    except OSError:
        return []


def _is_leftover(name: str) -> bool:
    return name.startswith(".") and (name.endswith(".tmp") or name.startswith(".tmp-"))


def prune_volume(code_dir: str | Path, keep: Path | None = None, *, now: float | None = None,
                 days: float | None = None) -> int:
    """Delete what has outlived its use under `code_dir`: snapshot folders (<repo>/<40-hex sha>) not touched for
    `days` (retention_days(); each delivery touches the folder it uses), never `keep`, and half-extracted
    .tmp folders older than a day. Returns how many folders it removed. Opportunistic and bounded — it looks at
    PRUNE_MAX_ENTRIES entries, removes PRUNE_MAX_REMOVALS folders and stops after PRUNE_TIME_BUDGET seconds, so
    a big backlog is cleared over several deliveries. It only ever looks at real folders at the two levels
    below `code_dir`: symlinks are neither followed nor removed, so nothing outside the volume can be reached
    through one. Never raises."""
    removed = 0
    try:
        root = _real(code_dir)
        now = time.time() if now is None else now
        days = retention_days() if days is None else days
        snapshot_cutoff = now - days * 86400 if days > 0 else None
        tmp_cutoff = now - STALE_TMP_SECONDS
        keep_path = Path(os.path.normpath(keep)) if keep else None
        deadline, seen = time.monotonic() + PRUNE_TIME_BUDGET, 0
        repo_dirs = _real_subdirs(root)
        random.shuffle(repo_dirs)                       # a backlog is cleared evenly, not one repo after another
        for repo_dir in repo_dirs:
            for entry in _real_subdirs(repo_dir.path):
                seen += 1
                if seen > PRUNE_MAX_ENTRIES or removed >= PRUNE_MAX_REMOVALS or time.monotonic() > deadline:
                    return removed
                if keep_path is not None and Path(os.path.normpath(entry.path)) == keep_path:
                    continue
                try:
                    mtime = entry.stat(follow_symlinks=False).st_mtime
                except OSError:
                    continue
                old_snapshot = snapshot_cutoff is not None and SHA_RE.fullmatch(entry.name) and mtime < snapshot_cutoff
                if old_snapshot or (_is_leftover(entry.name) and mtime < tmp_cutoff):
                    shutil.rmtree(entry.path, ignore_errors=True)       # refuses a symlink; never follows one inside
                    removed += 1
    except Exception:                                   # noqa: BLE001 - housekeeping must never fail a delivery
        pass
    return removed


def _touch(path: Path) -> None:
    """Mark a snapshot folder as used just now (its mtime is what retention goes by). Best effort."""
    try:
        os.utime(path)
    except OSError:
        pass


def deliver(info: RepoInfo, *, code_dir: str | None,
            has_code: Callable[[str, str], bool],
            put_code: Callable[[str, str, Snapshot], None]) -> str:
    """Make the snapshot available to the pods; returns where it went: "volume", "cached" or "coordinator".

    With `code_dir` (the shared tests-store volume) the snapshot is unpacked there; without it the
    coordinator is asked whether it already has this commit (`has_code`) and sent it if not (`put_code`).
    Both callables raise SnapshotError on failure. On the volume, the folder is touched whenever a run uses
    it, and old folders nobody has used for GRID_CODE_RETENTION_DAYS are pruned afterwards (prune_volume)."""
    if code_dir:
        if not Path(code_dir).is_dir():
            raise SnapshotError("GRID_CODE_DIR is set but is not a folder on this server", 500)
        target = volume_target(code_dir, info.repo, info.sha)
        if not target.is_dir():
            with build_snapshot(info) as snap:
                try:
                    safe_extract(snap.path, target)
                except ValueError as exc:
                    raise SnapshotError(f"The snapshot could not be stored: {exc}", 500) from None
                except OSError:
                    raise SnapshotError("The snapshot could not be written to the shared tests volume", 500) from None
            if not target.is_dir():                     # something that is not a folder is in the way
                raise SnapshotError("The snapshot could not be written to the shared tests volume", 500)
        _touch(target)
        prune_volume(code_dir, keep=target)
        return "volume"

    if has_code(info.repo, info.sha):
        return "cached"
    with build_snapshot(info) as snap:
        put_code(info.repo, info.sha, snap)
    return "coordinator"
