"""A Grid run's Allure report for the dashboard, fetched from the coordinator and cached on disk.

The coordinator generates ONE single-file Allure 3 report per run, on demand, and answers with an ETag
that changes whenever more of the run's results arrive. Amplify keeps what it downloaded as

    <data dir>/grid_reports/<run id>/<etag>/report.html

one folder per run *and* version: the signed viewer link (report_viewer) exposes the report's whole
folder, so with this layout a link reaches that one file and nothing else. Asking again sends the newest
cached ETag as If-None-Match, so an unchanged report is not downloaded twice. Entries not used for a week
are deleted. No Flask here; the route turns ReportError into a JSON answer.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

from amplify import report_viewer
from amplify.config import settings
from amplify.routes.grid import coordinator_download

CACHE_DAYS = 7
REPORT_FILE = "report.html"
# One path segment: the coordinator's run ids (run-20261120-101500-ab12cd) fit; "." and ".." and "/" do not.
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_ETAG = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


class ReportError(Exception):
    """The report can't be had. `status` is the HTTP status to answer with: 404 nothing to show,
    503 the coordinator can't generate right now (no Allure CLI, busy, not configured), 502 anything else."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.message, self.status = message, status


def cache_root() -> Path:
    return Path(settings.DATABASE_PATH).parent / "grid_reports"


# Grid reports are shown in a sandboxed frame (no allow-same-origin). There, merely reading window.localStorage,
# sessionStorage or document.cookie throws, and Allure's report reads them unguarded at start-up and stays blank.
# This script goes first in <head> and swaps in in-memory versions ONLY when the real ones are blocked.
STORAGE_SHIM = (
    b"<script>(function(){"
    b"function mem(){var m={};return{getItem:function(k){return Object.prototype.hasOwnProperty.call(m,k)?m[k]:null},"
    b"setItem:function(k,v){m[k]=String(v)},removeItem:function(k){delete m[k]},clear:function(){m={}},"
    b"key:function(i){return Object.keys(m)[i]||null},get length(){return Object.keys(m).length}}}"
    b"var names=['localStorage','sessionStorage'];"
    b"names.forEach(function(n){try{window[n].getItem('x')}catch(e){"
    b"try{Object.defineProperty(window,n,{value:mem(),configurable:true})}catch(e2){}}});"
    b"try{void document.cookie}catch(e){try{var c='';Object.defineProperty(document,'cookie',"
    b"{get:function(){return c},set:function(v){c=v},configurable:true})}catch(e3){}}"
    b"})();</script>"
)
_HEAD = re.compile(rb"<head\b[^>]*>", re.I)


def with_storage_shim(body: bytes) -> bytes:
    """The report with STORAGE_SHIM first in <head> (unchanged if it has no <head> near the top: a script placed
    before the doctype would switch the page into quirks mode)."""
    head = _HEAD.search(body, 0, 4096)
    return body if head is None else body[:head.end()] + STORAGE_SHIM + body[head.end():]


def _etag(headers: dict, body: bytes) -> str:
    """The coordinator's ETag as a folder name; a hash of the content when it sent none (or an odd one)."""
    raw = (headers.get("etag") or "").strip()
    if raw.startswith("W/"):
        raw = raw[2:]
    raw = raw.strip('"')
    return raw if _ETAG.fullmatch(raw) else hashlib.sha256(body).hexdigest()


def _newest(run_dir: Path) -> tuple[str, Path] | None:
    """(etag, report file) of the most recently used cached version of this run, if any."""
    best = None
    try:
        entries = list(run_dir.iterdir())
    except OSError:
        return None
    for entry in entries:
        report = entry / REPORT_FILE
        try:
            if entry.is_symlink() or not report.is_file() or not _ETAG.fullmatch(entry.name):
                continue
            mtime = report.stat().st_mtime
        except OSError:
            continue
        if best is None or mtime > best[0]:
            best = (mtime, entry.name, report)
    return (best[1], best[2]) if best else None


def prune(root: Path | None = None, *, max_age_s: float = CACHE_DAYS * 86400, now: float | None = None) -> None:
    """Delete cached reports not used for `max_age_s` seconds (and run folders left empty). Never raises."""
    root = root or cache_root()
    cutoff = (time.time() if now is None else now) - max_age_s
    try:
        run_dirs = [d for d in root.iterdir() if d.is_dir() and not d.is_symlink()]
    except OSError:
        return
    for run_dir in run_dirs:
        try:
            for entry in run_dir.iterdir():
                if entry.is_symlink() or not entry.is_dir():
                    continue
                report = entry / REPORT_FILE
                used = (report if report.exists() else entry).stat().st_mtime
                if used < cutoff:
                    shutil.rmtree(entry, ignore_errors=True)
            run_dir.rmdir()                         # only succeeds when nothing is left in it
        except OSError:
            continue


def _drop_superseded(run_dir: Path, keep: str) -> None:
    """Older versions of this run whose viewer links have expired are no longer needed."""
    cutoff = time.time() - report_viewer.TTL_SECONDS
    try:
        for entry in run_dir.iterdir():
            report = entry / REPORT_FILE
            if entry.name != keep and entry.is_dir() and not entry.is_symlink() and report.exists() \
                    and report.stat().st_mtime < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
    except OSError:
        pass


def _store(run_dir: Path, etag: str, body: bytes) -> Path:
    """Write the report atomically into its own folder (a temp name never ends in .html, so it is never served)."""
    folder = run_dir / etag
    folder.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".report-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(body)
        os.replace(tmp, folder / REPORT_FILE)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return folder / REPORT_FILE


def _failure(reply) -> ReportError:
    status = reply.status if reply.status in (404, 503) else 502
    message = (reply.error or f"The coordinator answered HTTP {reply.status}")[:300]
    if status == 404 and message.strip().lower() in ("not found", ""):
        message = "The coordinator has no report for this run"
    return ReportError(message, status)


def fetch_report(run_id: str) -> Path:
    """The cached report file for a Grid run, downloaded first if the coordinator has a newer version.
    Raises ReportError."""
    if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
        raise ReportError("Run not found", 404)
    prune()
    run_dir = cache_root() / run_id
    cached = _newest(run_dir)
    headers = {"Accept": "text/html"}
    if cached:
        headers["If-None-Match"] = f'"{cached[0]}"'
    reply = coordinator_download(f"/api/runs/{run_id}/report", headers=headers)

    if reply.status == 304 and cached:
        os.utime(cached[1])                          # used just now: keep it past the weekly clean-up
        return cached[1]
    if not reply.ok or reply.status != 200:
        raise _failure(reply)
    if not reply.body:
        raise ReportError("The coordinator returned an empty report", 502)

    etag = _etag(reply.headers, reply.body)
    target = run_dir / etag / REPORT_FILE
    if target.is_file():
        os.utime(target)
    else:
        try:
            target = _store(run_dir, etag, with_storage_shim(reply.body))
        except OSError:
            raise ReportError("The report could not be saved on this server", 500) from None
    _drop_superseded(run_dir, etag)
    return target
