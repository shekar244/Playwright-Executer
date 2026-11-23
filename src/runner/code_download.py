"""Checked snapshot download for the runner: coordinator -> cached archive -> fresh per-chunk directory.

Test code runs with the runner's own uid, so anything it leaves in GRID_WORK_DIR (a modified
archive, a modified extraction) must never reach a later chunk. For EVERY chunk:

  (a) the coordinator is asked for the expected SHA-256 (HEAD /api/code/<repo>/<sha>),
  (b) the cached archive is read once and hashed against it (re-downloaded on a mismatch or
      when missing) and the code is unpacked from those verified bytes, not from the file,
  (c) the code is unpacked into a brand-new directory that the caller deletes after the chunk.
"""
import hashlib
import io
import os
import re
import shutil
import stat
import tempfile
from pathlib import Path
from urllib.parse import quote

import requests

from .tar_guard import safe_extract_tar

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ARCHIVES_DIR = "archives"  # <work>/archives/<repo>/<sha>.tar.gz: the verified-on-use download cache
CHUNKS_DIR = "chunks"  # <work>/chunks/<random>/<repo>/<sha>/: one unpacked snapshot per chunk


def archive_path(work_dir: Path, repo: str, sha: str) -> Path:
    return Path(work_dir) / ARCHIVES_DIR / repo / f"{sha}.tar.gz"


def force_rmtree(path) -> None:
    """rmtree that also removes directories test code made unreadable or read-only. Never raises."""
    stack = [os.fspath(path)]
    while stack:  # open every directory up first: rmtree cannot list or empty a mode 000 one
        current = stack.pop()
        try:
            if os.path.islink(current):
                continue
            os.chmod(current, 0o700)
            with os.scandir(current) as entries:
                stack.extend(e.path for e in entries if e.is_dir(follow_symlinks=False))
        except OSError:
            pass
    shutil.rmtree(path, ignore_errors=True)


def discard_snapshot(path, work_dir: Path) -> None:
    """Delete a per-chunk snapshot made by fetch_snapshot; any other path (the shared volume) is left alone."""
    root = Path(work_dir) / CHUNKS_DIR
    try:
        parts = Path(path).relative_to(root).parts
    except (ValueError, TypeError):
        return
    if parts:
        force_rmtree(root / parts[0])


def _checksum_of(resp) -> str:
    return (resp.headers.get("X-Content-SHA256") or "").strip().lower()


def _expected_checksum(url, headers, label, log) -> str | None:
    """The coordinator's SHA-256 of this snapshot (HEAD), or None when it has none to give."""
    # No redirects: the token header must only ever go to the coordinator itself.
    with requests.head(url, headers=headers, timeout=(10, 30), allow_redirects=False) as resp:
        if resp.status_code != 200:
            log(f"Coordinator has no code for {label} (HTTP {resp.status_code}).")
            return None
        expected = _checksum_of(resp)
        if not SHA256_RE.fullmatch(expected):
            log("Coordinator sent no valid X-Content-SHA256; refusing the code download.")
            return None
        return expected


def _read_cached(archive: Path, expected: str, max_bytes: int) -> bytes | None:
    """The cached archive's bytes when they hash to `expected`; None when missing, odd or different."""
    try:
        # A regular file only, never a symlink or a fifo that test code put there (a read would block).
        fd = os.open(archive, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > max_bytes:
            return None
        with os.fdopen(fd, "rb", closefd=False) as f:
            data = f.read(max_bytes + 1)
    except OSError:
        return None
    finally:
        os.close(fd)
    return data if len(data) <= max_bytes and hashlib.sha256(data).hexdigest() == expected else None


def _download(url, headers, expected, max_bytes, log) -> bytes | None:
    """GET the snapshot; its bytes only when they hash to `expected` (what HEAD announced)."""
    with requests.get(url, headers=headers, stream=True, timeout=(10, 60), allow_redirects=False) as resp:
        if resp.status_code != 200:
            log(f"Code download failed (HTTP {resp.status_code}).")
            return None
        if _checksum_of(resp) != expected:
            log("Coordinator sent a different X-Content-SHA256 than announced; refusing the code download.")
            return None
        buf = bytearray()
        for block in resp.raw.stream(1 << 20, decode_content=False):
            buf += block
            if len(buf) > max_bytes:
                log("Code download exceeds the size limit; refusing it.")
                return None
    data = bytes(buf)
    if hashlib.sha256(data).hexdigest() != expected:
        log("Code download failed its SHA-256 check; refusing it.")
        return None
    return data


def _store_archive(archive: Path, data: bytes) -> None:
    """Atomically (re)place the cached archive, even over something test code left there."""
    archive.parent.mkdir(parents=True, exist_ok=True)
    if archive.is_dir() and not archive.is_symlink():
        force_rmtree(archive)
    fd, tmp = tempfile.mkstemp(prefix=".dl-", dir=archive.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, archive)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def fetch_snapshot(repo: str, sha: str, work_dir: Path, *, base_url: str, headers: dict,
                   max_bytes: int, log) -> Path | None:
    """A fresh directory holding the checksum-verified snapshot of repo@sha, or None.

    The caller owns the directory and removes it after the chunk (discard_snapshot).
    repo and sha must already be validated (no path characters).
    """
    work_dir = Path(work_dir)
    label = f"{re.sub(r'[^A-Za-z0-9._-]', '?', repo)}@{sha[:7]}"
    url = f"{base_url}/api/code/{quote(repo, safe='')}/{sha}"
    expected = _expected_checksum(url, headers, label, log)
    if expected is None:
        return None
    archive = archive_path(work_dir, repo, sha)
    data = _read_cached(archive, expected, max_bytes)
    if data is None:
        data = _download(url, headers, expected, max_bytes, log)
        if data is None:
            return None
        try:
            _store_archive(archive, data)
        except OSError as e:  # not cached: the next chunk downloads again
            log(f"Could not cache the code archive ({type(e).__name__}).")
    chunks = work_dir / CHUNKS_DIR
    chunks.mkdir(parents=True, exist_ok=True)
    chunk_root = Path(tempfile.mkdtemp(prefix="chunk-", dir=chunks))
    try:
        return safe_extract_tar(io.BytesIO(data), chunk_root / repo / sha)
    except ValueError as e:
        log(f"Code download refused: {e}.")
        force_rmtree(chunk_root)
        return None
    except BaseException:
        force_rmtree(chunk_root)
        raise
