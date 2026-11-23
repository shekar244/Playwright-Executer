"""Vetting and extracting untrusted .tar.gz uploads with bounded memory and CPU.

Why this exists: tarfile reads a GNU long-name/long-link ('L'/'K') or PAX ('x'/'g') header payload
fully into memory (fileobj.read(<declared size>)) BEFORE it yields a member. Those headers are not
members, and a gzip of zeros shrinks ~1000:1, so a ~1 MiB upload could make the coordinator allocate
GiBs while the member count and unpacked-size limits never saw anything. Defences, cheapest first:

1. BoundedTarInfo refuses an extension header whose payload exceeds 64 KiB before tarfile reads it,
   caps how many extension headers may chain, and refuses GNU sparse members (never legitimate here).
2. CappedReader stops the gunzipped stream after members*2048 + max_bytes + 1 MiB bytes, so no other
   construct can make tarfile consume (and buffer) more than the limits could ever justify.

Imports no database or web code. Every refusal is a ValueError with a static message (no paths).
"""
import gzip
import os
import shutil
import tarfile
import tempfile
import zlib
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Iterator, Optional, Tuple

MAX_EXTENSION_BYTES = 64 * 1024  # largest GNU long-name/link or PAX header payload accepted
MAX_EXTENSION_CHAIN = 8          # legitimate archives chain at most ~3 (pax global, pax, long name, ...)
BYTES_PER_MEMBER = 2048          # header + padding + a pax/long-name header and its payload block
STREAM_SLACK_BYTES = 1 << 20     # end-of-archive blocks, record padding, a pax global header

# tarfile's private hooks BoundedTarInfo overrides. If a Python upgrade renames one, the limits would
# silently stop applying, so refuse to import instead (a test pins this against the running Python).
_HOOKS = ("_proc_gnulong", "_proc_pax", "_proc_sparse",
          "_proc_gnusparse_00", "_proc_gnusparse_01", "_proc_gnusparse_10")
_missing = [name for name in _HOOKS if not hasattr(tarfile.TarInfo, name)]
if _missing:  # pragma: no cover
    raise ImportError(f"tarfile.TarInfo lost {_missing}: update coordinator/tar_limits.py before upgrading Python")


class TarLimitError(ValueError):
    """An archive exceeds a structural limit (extension header, nesting, sparse member, stream size)."""


class BoundedTarInfo(tarfile.TarInfo):
    """TarInfo that refuses tar extension headers tarfile would otherwise buffer without any limit."""

    def _guarded(self, proc, tar):
        if not 0 <= self.size <= MAX_EXTENSION_BYTES:
            raise TarLimitError(f"Archive has an oversized tar extension header (over {MAX_EXTENSION_BYTES} bytes)")
        depth = getattr(tar, "_extension_depth", 0)
        if depth >= MAX_EXTENSION_CHAIN:  # each chained header recurses inside tarfile
            raise TarLimitError("Archive chains too many tar extension headers")
        tar._extension_depth = depth + 1
        try:
            return proc(tar)
        finally:
            tar._extension_depth = depth

    def _proc_gnulong(self, tar):
        return self._guarded(super()._proc_gnulong, tar)

    def _proc_pax(self, tar):
        return self._guarded(super()._proc_pax, tar)

    def _refuse_sparse(self, *_args):
        raise TarLimitError("Archive contains a GNU sparse member")

    # Old-format ('S') sparse headers chain unbounded extension blocks; the PAX 1.0 map is read from
    # the data stream with an attacker-chosen length. Neither git archive nor allure-results has any.
    _proc_sparse = _proc_gnusparse_00 = _proc_gnusparse_01 = _proc_gnusparse_10 = _refuse_sparse


class CappedReader:
    """Read-only file wrapper that raises once more than `limit` bytes have been read through it."""

    def __init__(self, fileobj: BinaryIO, limit: int):
        self._file, self._left = fileobj, limit

    def read(self, size: int = -1) -> bytes:
        want = self._left + 1 if size is None or size < 0 else min(size, self._left + 1)
        data = self._file.read(want)
        self._left -= len(data)
        if self._left < 0:
            raise TarLimitError("Archive expands to more than its limits allow")
        return data


def stream_limit(max_members: int, max_bytes: int) -> int:
    """Most decompressed bytes a tar within these member/byte limits can legitimately occupy."""
    return max(0, max_members) * BYTES_PER_MEMBER + max(0, max_bytes) + STREAM_SLACK_BYTES


@contextmanager
def open_tar_gz(archive: Path, max_members: int, max_bytes: int) -> Iterator[tarfile.TarFile]:
    """Stream-open a .tar.gz with every limit above in force."""
    with gzip.open(archive, "rb") as gz:
        capped = CappedReader(gz, stream_limit(max_members, max_bytes))
        with tarfile.open(fileobj=capped, mode="r|", tarinfo=BoundedTarInfo) as tar:
            yield tar


def walk_tar(archive: Path, max_members: int, max_bytes: int, dest: Optional[Path] = None) -> Tuple[int, int]:
    """Vet every member of a .tar.gz, and extract it into dest when given. Raises ValueError.

    Only regular files and directories pass: absolute paths, ".." segments, links, devices and
    fifos are refused, and member count / declared unpacked bytes are capped as the headers
    stream by, so a decompression bomb is stopped before its data is expanded.
    """
    members = total = 0
    try:
        with open_tar_gz(archive, max_members, max_bytes) as tar:
            for member in tar:
                members += 1
                if members > max_members:
                    raise ValueError(f"Archive has more than {max_members} members")
                name = member.name
                while name.startswith("./"):
                    name = name[2:]
                if name.startswith("/") or ".." in name.split("/"):
                    raise ValueError("Archive contains an absolute or parent-relative path")
                if member.type in (tarfile.REGTYPE, tarfile.AREGTYPE):
                    total += member.size
                elif member.type != tarfile.DIRTYPE:
                    raise ValueError("Archive contains a link, device or other special member")
                if total > max_bytes:
                    raise ValueError(f"Archive unpacks to more than {max_bytes} bytes")
                if dest is not None and name not in ("", "."):
                    member.name = name
                    tar.extract(member, dest, filter="data")
    except (tarfile.TarError, EOFError, zlib.error, gzip.BadGzipFile, RecursionError):
        raise ValueError("Archive is corrupt, not a tar.gz, or has a disallowed member") from None
    return members, total


def safe_extract_tar(archive: Path, dest: Path, *, max_members: int, max_bytes: int) -> Tuple[int, int]:
    """Extract into the new directory dest, all or nothing. Returns (members, bytes).

    Files land in a temp directory beside dest and are renamed into place only after every
    member passed, so a rejected archive leaves nothing behind. Raises ValueError.
    """
    dest = Path(dest)
    if dest.exists():
        raise FileExistsError(dest.name)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f".{dest.name}.part-", dir=dest.parent))
    try:
        stats = walk_tar(Path(archive), max_members, max_bytes, tmp)
        os.replace(tmp, dest)
        return stats
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
