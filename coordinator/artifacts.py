"""Test-code snapshots, raw Allure archives and on-demand Allure 3 reports.

Imports no database code (coordinator.py runs init_db() at import time), so this module can be
imported and tested without PostgreSQL. Every setting comes from an environment variable.
"""
import asyncio
import hashlib
import logging
import os
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from contextlib import suppress
from dataclasses import dataclass, fields
from pathlib import Path
from typing import AsyncIterator, Dict, List, Mapping, Optional, Tuple
from report_environments import CONFIG_NAME, write_environments_config
from report_support import archive_key, etag_matches, strip_analytics  # noqa: F401 (re-exported for tests)
from tar_limits import safe_extract_tar, walk_tar  # noqa: F401 (also the public names the tests use)

log = logging.getLogger("coordinator")

# Shared rules (identical in the runner and Amplify): validate before building any path.
REPO_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$"
SHA_PATTERN = r"^[0-9a-f]{40}$"
_REPO_RE, _SHA_RE = re.compile(REPO_PATTERN), re.compile(SHA_PATTERN)
_NAME_RE, _HEX64_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}"), re.compile(r"[0-9a-f]{64}")

GROUP_BY = "browser"            # verified with Allure 3.16.0; the CLI call falls back to none
RETENTION_SWEEP_SECONDS = 3600  # how often coordinator.py runs purge_expired()
_WORK_MAX_AGE = 86400           # leftovers of a crashed generation are removed after a day
_SUPERSEDED_GRACE = 300         # an older cached report of a run is pruned once this stale
_CLI_ENV_PASSTHROUGH = ("PATH", "LANG", "LC_ALL", "TZ", "NODE_OPTIONS")  # nothing else leaks in
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_ABS_DIR_RE = re.compile(r"(?<![\w:/.])/(?:[^\s/'\"]+/)+")  # "/srv/x/y/file.json" -> "file.json"


def valid_repo(value: object) -> bool:
    return isinstance(value, str) and _REPO_RE.fullmatch(value) is not None


def valid_sha(value: object) -> bool:
    return isinstance(value, str) and _SHA_RE.fullmatch(value) is not None


def safe_name(value: str) -> str:
    """File-system name for an id that may not be path safe (chunk ids are client chosen)."""
    if _NAME_RE.fullmatch(value):
        return value
    return "x-" + hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:32]


class ArtifactError(Exception):
    """A failure carrying the HTTP status to answer with; detail never holds server paths."""

    def __init__(self, status_code: int, detail: str, headers: Optional[Dict[str, str]] = None):
        super().__init__(detail)
        self.status_code, self.detail, self.headers = status_code, detail, headers or {}


# --- Settings --------------------------------------------------------------------
def _num(name: str, raw: str, kind):
    try:
        value = kind(raw)
    except ValueError:
        raise ValueError(f"{name} must be a number, got {raw!r}") from None
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


@dataclass(frozen=True)
class Settings:
    """Each field is read from the environment variable named like the field, upper-cased."""

    code_dir: Path = Path("/data/code")
    allure_dir: Path = Path("/data/allure")
    report_dir: Path = Path("/data/reports")
    max_code_bytes: int = 104857600
    max_allure_bytes: int = 52428800
    max_allure_unpacked_bytes: int = 524288000
    max_allure_files: int = 20000
    allure3_bin: str = "allure"      # the Allure 3 CLI: a command, "npx" (= "npx allure"), or the npm bin folder
    report_timeout_seconds: float = 120
    report_concurrency: int = 1
    code_retention_days: float = 30
    allure_retention_days: float = 14

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "Settings":
        values = {}
        for f in fields(cls):
            name, raw = f.name.upper(), (env.get(f.name.upper()) or "").strip()
            if raw:
                values[f.name] = Path(raw) if f.type is Path else raw if f.type is str else _num(name, raw, f.type)
        return cls(**values)


@dataclass
class Spooled:
    """A request body streamed to a temp file beside its destination (same filesystem)."""

    path: Path
    size: int
    sha256: str

    def discard(self) -> None:
        self.path.unlink(missing_ok=True)


def _temp_in(directory: Path) -> Tuple[int, str]:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        return tempfile.mkstemp(prefix=".upload-", dir=directory)
    except FileNotFoundError:  # the retention sweep removed the empty directory in between
        directory.mkdir(parents=True, exist_ok=True)
        return tempfile.mkstemp(prefix=".upload-", dir=directory)


async def _spool(chunks: AsyncIterator[bytes], directory: Path, max_bytes: int,
                 content_length: Optional[str]) -> Spooled:
    if content_length and content_length.isdigit() and int(content_length) > max_bytes:
        raise ArtifactError(413, f"Upload exceeds the limit of {max_bytes} bytes")
    fd, name = _temp_in(directory)
    out, digest, size = os.fdopen(fd, "wb"), hashlib.sha256(), 0
    try:
        async for chunk in chunks:
            size += len(chunk)
            if size > max_bytes:
                raise ArtifactError(413, f"Upload exceeds the limit of {max_bytes} bytes")
            digest.update(chunk)
            await asyncio.to_thread(out.write, chunk)
        if size == 0:
            raise ArtifactError(400, "Empty request body")
        out.close()
    except BaseException:
        out.close()
        Spooled(Path(name), size, "").discard()
        raise
    return Spooled(Path(name), size, digest.hexdigest())


class ArtifactStore:
    """CODE_DIR/<repo>/<sha>.tar.gz and ALLURE_DIR/<run or _>/<chunk>.tar.gz, written atomically."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._publish_lock = threading.Lock()  # serialises check-then-publish of code uploads
        self._sums: Dict[str, Tuple[int, int, str]] = {}

    # Code snapshots
    def code_path(self, repo: str, sha: str) -> Path:
        if not (valid_repo(repo) and valid_sha(sha)):
            raise ArtifactError(400, "Invalid repo or sha")
        return self.settings.code_dir / repo / f"{sha}.tar.gz"

    def _checksum(self, path: Path) -> str:
        st = path.stat()
        cached = self._sums.get(str(path))
        if cached and cached[:2] == (st.st_size, st.st_mtime_ns):
            return cached[2]
        digest = hashlib.sha256()
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                digest.update(block)
        if len(self._sums) >= 512:
            self._sums.clear()
        self._sums[str(path)] = (st.st_size, st.st_mtime_ns, digest.hexdigest())
        return digest.hexdigest()

    def _reuse(self, path: Path, digest: str) -> None:
        """A snapshot is being reused: restart its retention clock (the sweep goes by mtime).

        Best effort, never raises. Keeps the checksum cache (keyed by size and mtime) valid.
        """
        with suppress(OSError):
            os.utime(path)
            st = path.stat()
            self._sums[str(path)] = (st.st_size, st.st_mtime_ns, digest)

    def code_file(self, repo: str, sha: str) -> Optional[Tuple[Path, str]]:
        """(path, sha256) of a stored snapshot, or None. Serving it counts as a reuse."""
        path = self.code_path(repo, sha)
        try:
            digest = self._checksum(path)
        except FileNotFoundError:
            return None
        self._reuse(path, digest)
        return path, digest

    async def put_code(self, repo: str, sha: str, expected_sha256: Optional[str],
                       chunks: AsyncIterator[bytes], content_length: Optional[str]) -> Tuple[str, int]:
        """Store a snapshot. Returns ("stored" | "exists", bytes); first writer wins (409 on a clash)."""
        final = self.code_path(repo, sha)
        expected = (expected_sha256 or "").strip().lower()
        if not _HEX64_RE.fullmatch(expected):
            raise ArtifactError(400, "X-Content-SHA256 header (64 hex characters) is required")
        up = await _spool(chunks, final.parent, self.settings.max_code_bytes, content_length)
        try:
            return await asyncio.to_thread(self._publish_code, final, up, expected)
        finally:
            up.discard()

    def _publish_code(self, final: Path, up: Spooled, expected: str) -> Tuple[str, int]:
        if up.sha256 != expected:
            raise ArtifactError(400, "Checksum mismatch: body SHA-256 differs from X-Content-SHA256")
        with self._publish_lock:
            if final.exists():
                if self._checksum(final) == up.sha256:
                    self._reuse(final, up.sha256)
                    return "exists", up.size
                raise ArtifactError(409, f"Code for {final.parent.name}@{final.name[:7]} is already "
                                         "stored with different content")
            os.replace(up.path, final)
        return "stored", up.size

    # Raw Allure results
    def allure_path(self, run_id: Optional[str], chunk_id: str) -> Path:
        return self.settings.allure_dir / (safe_name(run_id) if run_id else "_") / f"{safe_name(chunk_id)}.tar.gz"

    def list_allure(self, run_id: str) -> List[Tuple[str, Path, os.stat_result]]:
        """(chunk name, archive, stat) for every archive of a run, sorted by chunk name."""
        try:
            with os.scandir(self.settings.allure_dir / safe_name(run_id)) as entries:
                found = [e for e in entries if e.name.endswith(".tar.gz") and not e.name.startswith(".")]
        except FileNotFoundError:
            return []
        listed = []
        for e in found:
            with suppress(FileNotFoundError):  # expired by the retention sweep meanwhile
                listed.append((e.name[:-7], Path(e.path), e.stat()))
        return sorted(listed, key=lambda t: t[0])

    def refresh_allure(self, run_id: str, archives: List[Tuple[str, Path, os.stat_result]]
                       ) -> List[Tuple[str, Path, os.stat_result]]:
        """Restart the retention clock of the archives a report is about to be built from (best effort).

        Their mtime is part of the report cache key, so the caller must key by the re-read listing.
        """
        for _chunk, path, _st in archives:
            with suppress(OSError):
                os.utime(path)
        return self.list_allure(run_id)

    async def put_allure(self, run_id: Optional[str], chunk_id: str, chunks: AsyncIterator[bytes],
                         content_length: Optional[str]) -> int:
        """Store a chunk's allure-results archive (overwriting a retry). Returns the byte count."""
        final = self.allure_path(run_id, chunk_id)
        up = await _spool(chunks, final.parent, self.settings.max_allure_bytes, content_length)
        try:
            try:
                await asyncio.to_thread(walk_tar, up.path, self.settings.max_allure_files,
                                        self.settings.max_allure_unpacked_bytes)
            except ValueError as exc:  # keep one bad archive from breaking the whole run's report
                raise ArtifactError(400, f"Invalid Allure archive: {exc}") from None
            os.replace(up.path, final)
            return up.size
        finally:
            up.discard()

    # Retention
    def purge_expired(self, now: Optional[float] = None) -> Dict[str, int]:
        """Delete files older than their retention (by mtime), then directories left empty."""
        now = time.time() if now is None else now
        s, day = self.settings, 86400
        removed = {
            "code": _purge(s.code_dir, now - s.code_retention_days * day),
            "allure": _purge(s.allure_dir, now - s.allure_retention_days * day),
            "reports": _purge(s.report_dir, now - s.allure_retention_days * day, skip=(".work",)),
        }
        work = s.report_dir / ".work"
        if work.is_dir():
            for entry in work.iterdir():
                if entry.stat().st_mtime < now - _WORK_MAX_AGE:
                    shutil.rmtree(entry, ignore_errors=True)
        return removed


def _purge(root: Path, cutoff: float, skip: Tuple[str, ...] = ()) -> int:
    if not root.is_dir():
        return 0
    removed, dirs = 0, []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip]
        dirs.append(dirpath)
        for name in filenames:
            path = os.path.join(dirpath, name)
            with suppress(FileNotFoundError):
                if os.lstat(path).st_mtime < cutoff:
                    os.unlink(path)
                    removed += 1
    for dirpath in reversed(dirs[1:]):  # children first; rmdir only succeeds on empty directories
        with suppress(OSError):
            os.rmdir(dirpath)
    return removed


@dataclass
class Report:
    key: str
    path: Optional[Path]  # None when the client's If-None-Match already matches (304)


def _merge(src: Path, dst: Path, prefix: str) -> int:
    moved = 0
    for root, _dirs, files in os.walk(src):
        rel = Path(root).relative_to(src)
        (dst / rel).mkdir(parents=True, exist_ok=True)
        for name in files:
            target, n = dst / rel / name, 0
            while target.exists():  # name clash: keep both, the later chunk gets its id as prefix
                target = dst / rel / (f"{prefix}-{name}" if n == 0 else f"{prefix}-{n}-{name}")
                n += 1
            os.replace(Path(root) / name, target)
            moved += 1
    return moved


def assemble_results(archives: List[Tuple[str, Path, os.stat_result]], dest: Path, *,
                     max_members: int, max_bytes: int) -> int:
    """Safely unpack all archives of a run into ONE results directory; limits apply to the sum."""
    dest = Path(dest)
    dest.mkdir(parents=True)
    parts, files = dest.parent / f"{dest.name}-parts", 0
    for chunk, path, _st in archives:
        members, size = safe_extract_tar(path, parts / chunk, max_members=max_members, max_bytes=max_bytes)
        max_members, max_bytes = max_members - members, max_bytes - size
        files += _merge(parts / chunk, dest, chunk)
        shutil.rmtree(parts / chunk)
    return files


def _error_tail(stderr: str, stdout: str) -> str:
    text = _ANSI_RE.sub("", (stderr or "").strip() or (stdout or "").strip())
    return _ABS_DIR_RE.sub("", text)[-300:]


class ReportService:
    """One single-file Allure 3 report per run, generated on demand and cached by archive key."""

    def __init__(self, settings: Settings, store: ArtifactStore):
        self.settings, self.store = settings, store
        self._slots = asyncio.Semaphore(settings.report_concurrency)

    def _cache_path(self, run_id: str, key: str) -> Path:
        return self.settings.report_dir / safe_name(run_id) / f"{key}.html"

    def info(self, run_id: str) -> Dict[str, object]:
        """Cheap status; never generates."""
        archives = self.store.list_allure(run_id)
        cached = bool(archives) and self._cache_path(run_id, archive_key(archives)).is_file()
        return {"available": bool(archives), "chunks_with_allure": len(archives), "cached": cached}

    def _cli_argv(self) -> List[str]:
        argv = shlex.split(self.settings.allure3_bin) or ["allure"]
        if len(argv) == 1 and os.path.isdir(argv[0]):  # the folder npm installs global CLIs into: $(npm config get prefix)/bin
            argv[0] = os.path.join(argv[0], "allure")
        if os.path.basename(argv[0]) == "npx" and len(argv) == 1:
            argv.append("allure")
        if shutil.which(argv[0]) is None:
            raise ArtifactError(503, "Allure CLI is not installed on the coordinator")
        return argv

    async def render(self, run_id: str, if_none_match: Optional[str] = None) -> Report:
        """The run's report (cached or freshly generated); path is None for a 304."""
        archives = await asyncio.to_thread(self.store.list_allure, run_id)
        if not archives:
            raise ArtifactError(404, "No Allure data for this run")
        key = archive_key(archives)
        if etag_matches(if_none_match, key):
            return Report(key, None)
        cached = self._cache_path(run_id, key)
        if cached.is_file():
            return Report(key, cached)
        argv = self._cli_argv()
        try:
            await asyncio.wait_for(self._slots.acquire(), self.settings.report_timeout_seconds)
        except asyncio.TimeoutError:
            raise ArtifactError(503, "Report generator is busy, retry shortly", {"Retry-After": "5"}) from None
        # The slot is freed when the thread finishes, even if this request is cancelled meanwhile.
        task = asyncio.ensure_future(asyncio.to_thread(self._build, run_id, argv))
        task.add_done_callback(lambda t: (self._slots.release(), t.cancelled() or t.exception()))
        return await asyncio.shield(task)

    def _build(self, run_id: str, argv: List[str]) -> Report:
        archives = self.store.list_allure(run_id)  # re-read: chunks may have arrived while queued
        if not archives:
            raise ArtifactError(404, "No Allure data for this run")
        key = archive_key(archives)
        final = self._cache_path(run_id, key)
        if final.is_file():
            return Report(key, final)
        archives = self.store.refresh_allure(run_id, archives)  # being used: keep them past the sweep
        if not archives:
            raise ArtifactError(404, "No Allure data for this run")
        key = archive_key(archives)
        final = self._cache_path(run_id, key)
        s = self.settings
        (s.report_dir / ".work").mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix="gen-", dir=s.report_dir / ".work"))
        try:
            try:
                files = assemble_results(archives, work / "results", max_members=s.max_allure_files,
                                         max_bytes=s.max_allure_unpacked_bytes)
            except ValueError as exc:
                raise ArtifactError(502, f"Report generation failed: {exc}") from None
            except OSError:
                log.exception("Could not unpack Allure archives of %s", run_id)
                raise ArtifactError(502, "Report generation failed: results could not be unpacked") from None
            if not files:
                raise ArtifactError(404, "No Allure data for this run")
            self._run_cli(argv, work, safe_name(run_id))
            index = work / "out" / "index.html"
            if not index.is_file() or index.stat().st_size == 0:
                raise ArtifactError(502, "Report generation failed: the Allure CLI produced no report")
            strip_analytics(index)                       # Allure 3 bakes Google Tag Manager into every report
            final.parent.mkdir(parents=True, exist_ok=True)
            os.replace(index, final)
            self._prune_superseded(final)
            return Report(key, final)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _run_cli(self, argv: List[str], work: Path, name: str) -> None:
        """allure awesome <results> --output <out> --single-file --report-name <name> [--config | --group-by].

        Preferred: --config with one Allure environment per browser (report_environments.py). Fail soft: if that
        cannot be written or the CLI rejects it, retry with --group-by browser, then with neither."""
        env = {k: os.environ[k] for k in _CLI_ENV_PASSTHROUGH if k in os.environ}
        env.update(HOME=str(work / "home"), TMPDIR=str(work / "tmp"), NO_COLOR="1")
        for sub in ("home", "tmp"):
            (work / sub).mkdir(exist_ok=True)
        deadline, tail = time.monotonic() + self.settings.report_timeout_seconds, ""
        config = write_environments_config(work / "results", work / CONFIG_NAME)
        for extra in ([["--config", str(config)]] if config else []) + [["--group-by", GROUP_BY], []]:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ArtifactError(504, "Report generation timed out")
            shutil.rmtree(work / "out", ignore_errors=True)
            cmd = [*argv, "awesome", str(work / "results"), "--output", str(work / "out"),
                   "--single-file", "--report-name", name, *extra]
            try:
                proc = subprocess.Popen(cmd, cwd=work, env=env, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                        errors="replace", start_new_session=True)
            except FileNotFoundError:
                raise ArtifactError(503, "Allure CLI is not installed on the coordinator") from None
            try:
                stdout, stderr = proc.communicate(timeout=remaining)
            except subprocess.TimeoutExpired:
                with suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGKILL)  # the CLI may have spawned children
                proc.communicate()
                raise ArtifactError(504, "Report generation timed out") from None
            if proc.returncode == 0:
                return
            tail = _error_tail(stderr, stdout)
        raise ArtifactError(502, f"Report generation failed: {tail}")

    def _prune_superseded(self, current: Path) -> None:
        """Drop older cached reports of the run (stale ones only: a reader may still hold one)."""
        cutoff = time.time() - _SUPERSEDED_GRACE
        for old in current.parent.glob("*.html"):
            with suppress(OSError):  # best effort: housekeeping never fails a finished report
                if old != current and old.stat().st_mtime < cutoff:
                    old.unlink()
