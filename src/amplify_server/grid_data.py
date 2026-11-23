"""Reading Grid runs from the coordinator for the dashboard: the run list, and run details (cached).

A run that is finished *and settled* never changes, so its detail is kept for the life of the process
(bounded); a run still in flight, or finished a moment ago, is re-read every few seconds. Settled means no
more Allure results can arrive: a pod uploads them just AFTER its chunk is reported completed, so for a short
while a completed run still says has_allure=false (and has no report button). Such a run is settled once every
chunk has its Allure results, or its newest chunk update is older than ALLURE_GRACE_SECONDS (a pod that never
uploads — an old runner, a crash — must not make the run re-read forever). The cache is keyed by coordinator
URL, so pointing Amplify at a different coordinator never shows the old one's runs.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

from amplify_server.consolidated import GRID_LIST_LIMIT
from amplify_server.payload_coerce import dicts, iso_to_ms
from amplify_server.routes.grid import coordinator_request
from amplify_server.server_secrets import load_grid_config

LIST_LIMIT = GRID_LIST_LIMIT   # the coordinator's maximum
LIVE_TTL_SECONDS = 3.0
ALLURE_GRACE_SECONDS = 900        # how long after its last chunk update a finished run may still gain Allure results
CACHE_MAX = 500
_FETCH_THREADS = 8
_FETCH_TIMEOUT = 8

_cache: "OrderedDict[tuple[str, str], tuple[float, dict, bool]]" = OrderedDict()   # fetched at, detail, settled
_lock = threading.Lock()


def list_runs() -> tuple[list[dict], dict]:
    """(runs newest-first as the coordinator lists them, status for the UI)."""
    data, code = coordinator_request("GET", "/api/runs", {"limit": LIST_LIMIT}, timeout=_FETCH_TIMEOUT)
    data = data if isinstance(data, dict) else {}
    if code == 503 and data.get("configured") is False:
        return [], {"configured": False, "reachable": False, "error": None}
    if code != 200:
        return [], {"configured": True, "reachable": False, "error": data.get("error") or f"HTTP {code}"}
    runs = data.get("runs")
    return (runs if isinstance(runs, list) else []), {"configured": True, "reachable": True, "error": None}


def _finished(detail: dict) -> bool:
    chunks = detail.get("chunks")
    chunks = chunks if isinstance(chunks, dict) else {}
    return detail.get("status") in ("completed", "cancelled") and not chunks.get("pending") and not chunks.get("running")


def _settled(detail: dict, now: float | None = None) -> bool:
    """A finished run whose Allure results can no longer arrive (see the module comment)."""
    if not _finished(detail):
        return False
    chunks = dicts(detail.get("chunk_details"))
    if chunks and all(c.get("has_allure") is True or c.get("status") == "cancelled" for c in chunks):   # a cancelled chunk uploads nothing
        return True
    stamps = [ms for ms in (iso_to_ms(c.get("updated_at")) for c in chunks) if ms is not None]
    newest = max(stamps) if stamps else iso_to_ms(detail.get("created_at"))
    if newest is None:                                  # no way to tell how long ago it finished
        return False
    return (time.time() if now is None else now) * 1000 - newest > ALLURE_GRACE_SECONDS * 1000


def get_run(run_id: str) -> tuple[dict | None, str | None]:
    """(run detail, error). Settled runs come from the cache."""
    key = (load_grid_config()["coordinator_url"], run_id)
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        if hit and (hit[2] or now - hit[0] < LIVE_TTL_SECONDS):
            _cache.move_to_end(key)
            return hit[1], None
    data, code = coordinator_request("GET", f"/api/runs/{run_id}", timeout=_FETCH_TIMEOUT)
    if code != 200 or not isinstance(data, dict):
        return None, (data.get("error") if isinstance(data, dict) else None) or f"HTTP {code}"
    settled = _settled(data)                            # judged on what was just read, not on a later clock
    with _lock:
        _cache[key] = (now, data, settled)
        _cache.move_to_end(key)
        while len(_cache) > CACHE_MAX:
            _cache.popitem(last=False)
    return data, None


def fetch_details(run_ids: list[str]) -> dict[str, dict]:
    """{run id: detail} for the runs that could be read; unreadable ones are simply absent."""
    if not run_ids:
        return {}
    with ThreadPoolExecutor(max_workers=min(_FETCH_THREADS, len(run_ids))) as pool:
        results = list(pool.map(lambda rid: (rid, get_run(rid)[0]), run_ids))
    return {rid: detail for rid, detail in results if detail}


def clear_cache() -> None:
    with _lock:
        _cache.clear()
