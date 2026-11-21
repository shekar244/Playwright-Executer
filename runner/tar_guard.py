"""Safe extraction of an untrusted .tar.gz snapshot (the runner's code download).

Beyond refusing links, devices, absolute and ".." paths and capping the member count and
the declared unpacked size, this module bounds what tarfile buffers or decompresses without
the member loop ever seeing it:

* GNU long-name/long-link, PAX and sparse extension headers are read whole into memory by
  tarfile (`fileobj.read(size)` inside TarFile.next()), so a ~1 MB gzip bomb can claim a
  1 GiB header. GuardedTarInfo refuses any of them above 64 KiB BEFORE tarfile reads it.
* The decompressed stream itself has a hard cap (declared sizes + a per-member allowance),
  so many individually small extension headers cannot burn unbounded CPU or disk either.
"""
import gzip
import io
import os
import shutil
import tarfile
import tempfile
import zlib
from pathlib import Path

MAX_TAR_MEMBERS = 100_000
MAX_TAR_BYTES = 1024 * 1024 * 1024  # total unpacked size of a snapshot
MAX_EXTENSION_HEADER_BYTES = 64 * 1024  # one long-name / PAX / sparse header (git archive's are ~100 B)
PER_MEMBER_STREAM_BYTES = 4096  # header blocks, padding and a normal extension header, per member
STREAM_SLACK_BYTES = 128 * 1024  # end-of-archive blocks, a global PAX header, rounding

_EXTENSION_TYPES = (tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK,
                    tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.SOLARIS_XHDTYPE)


class ArchiveRefused(ValueError):
    """The archive is not acceptable (the message says why); nothing was extracted."""


class _ReadBudget:
    """Wraps tarfile's file object for one call and refuses reads beyond a byte budget."""

    def __init__(self, fileobj, budget: int):
        self._fileobj, self._left = fileobj, budget

    def read(self, size=-1):
        if size is None or size < 0 or size > self._left:
            raise ArchiveRefused("archive has an oversized tar extension header")
        self._left -= size
        return self._fileobj.read(size)

    def tell(self):
        return self._fileobj.tell()

    def seek(self, *args):
        return self._fileobj.seek(*args)


class GuardedTarInfo(tarfile.TarInfo):
    """TarInfo that refuses oversized extension headers before tarfile buffers them."""

    def _proc_member(self, tarfile_):
        if self.type in _EXTENSION_TYPES and self.size > MAX_EXTENSION_HEADER_BYTES:
            raise ArchiveRefused("archive has an oversized tar extension header")
        if self.type == tarfile.GNUTYPE_SPARSE:
            # The chain of extended sparse blocks has no declared length: read it on a budget.
            real = tarfile_.fileobj
            tarfile_.fileobj = _ReadBudget(real, MAX_EXTENSION_HEADER_BYTES)
            try:
                return super()._proc_member(tarfile_)
            finally:
                tarfile_.fileobj = real
        return super()._proc_member(tarfile_)


class _CappedGzip:
    """Read/seek view of a gzip stream that refuses to decompress more than `cap` bytes.

    tarfile skips member data with seek(); forward seeks decompress and discard, so both the
    requested position and the bytes actually produced are checked here.
    """

    mode = "rb"

    def __init__(self, gz, cap: int):
        self._gz, self._cap = gz, cap

    def _too_large(self):
        return ArchiveRefused("archive is too large when unpacked")

    def read(self, size=-1):
        room = self._cap - self._gz.tell() + 1  # one byte past the cap is enough to notice it
        data = self._gz.read(room if size is None or size < 0 else min(size, room))
        if self._gz.tell() > self._cap:
            raise self._too_large()
        return data

    def tell(self):
        return self._gz.tell()

    def seek(self, offset, whence=io.SEEK_SET):
        if whence != io.SEEK_SET:
            raise io.UnsupportedOperation("only absolute seeks are supported")
        if offset > self._cap:
            raise self._too_large()
        return self._gz.seek(offset)


def _clean_member_name(name: str) -> str:
    """Normalise a member name (drops a leading "./"); refuse absolute paths and ".." segments."""
    if name.startswith("/") or "\x00" in name:
        raise ValueError("archive contains an absolute path")
    parts = []
    for part in name.split("/"):
        if part == "..":
            raise ValueError("archive contains a '..' path segment")
        if part not in ("", "."):
            parts.append(part)
    return "/".join(parts)


def _open_gzip(archive):
    if isinstance(archive, (str, os.PathLike)):
        return gzip.open(archive, "rb")
    return gzip.GzipFile(fileobj=archive, mode="rb")  # a binary file object, e.g. io.BytesIO


def safe_extract_tar(archive, dest, max_members: int = MAX_TAR_MEMBERS, max_bytes: int = MAX_TAR_BYTES) -> Path:
    """Extract a .tar.gz (a path or a binary file object) into dest, all or nothing.

    Only regular files and directories are allowed (no symlinks, hardlinks, devices, fifos),
    names must stay inside dest, the member count, the unpacked size, every extension header
    and the decompressed stream are capped. Anything else raises ValueError and leaves nothing
    behind: extraction goes to a temp dir beside dest and is renamed into place at the end. If
    dest already exists (another extraction won the race) it is kept and the temp dir dropped.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f".{dest.name}.tmp-", dir=dest.parent))
    try:
        try:
            cap = max_bytes + max_members * PER_MEMBER_STREAM_BYTES + STREAM_SLACK_BYTES
            with _open_gzip(archive) as gz, \
                    tarfile.open(fileobj=_CappedGzip(gz, cap), mode="r:", tarinfo=GuardedTarInfo) as tar:
                members, total = [], 0
                for member in tar:  # streams headers: a hostile archive is refused at the first bad one
                    if len(members) >= max_members:
                        raise ValueError("archive has too many members")
                    member.name = _clean_member_name(member.name)
                    if not member.name:
                        continue  # the archive root ("./")
                    if not (member.isreg() or member.isdir()):
                        raise ValueError(f"archive member {member.name!r} is not a regular file or directory")
                    if member.isreg():
                        total += member.size
                        if total > max_bytes:
                            raise ValueError("archive is too large when unpacked")
                    members.append(member)
                tar.extractall(tmp, members=members, filter="data")
        except (tarfile.TarError, EOFError, zlib.error, OSError, RecursionError) as e:
            raise ValueError(f"could not extract archive ({type(e).__name__})") from e
        os.chmod(tmp, 0o755)
        try:
            os.replace(tmp, dest)
        except OSError:
            if not dest.is_dir():
                raise
            shutil.rmtree(tmp, ignore_errors=True)
        return dest
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
