import logging
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Dict, Optional

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, Response
from sqlalchemy import func, or_, text
from sqlalchemy.orm import Session

import artifacts
import live_view
from config import (
    DEFAULT_BROWSER, DEFAULT_CHUNK_SIZE, DEFAULT_ENVIRONMENT, FEATURES, REAPER_INTERVAL_SECONDS,
    SUPPORTED_BROWSERS, SUPPORTED_ENVIRONMENTS,
)
from db import (
    SessionLocal, TestChunkRecord, TestRunRecord, RunnerRecord, _utcnow, get_db, init_db,
)
from leasing import lease_next_chunk, release_chunk_row, require_lease_token
from schemas import (
    RunRequest, TestChunk, TestResultSubmission, require_full_token,
    require_token as require_pod_or_full_token, validate_browser, validate_environment,
)
from services import (
    _iso, _strip_logs, count_outcomes, grid_state, kube_list_pods, requeue_stale_chunks,
    summarize_runs, touch_runner,
)

log = logging.getLogger("coordinator")
init_db()

# Code snapshots, raw Allure archives and on-demand reports (all logic lives in artifacts.py).
artifact_settings = artifacts.Settings.from_env()
store = artifacts.ArtifactStore(artifact_settings)
reports = artifacts.ReportService(artifact_settings, store)


# --- Application -----------------------------------------------------------------
def _reaper_loop(stop: threading.Event) -> None:
    """Background task: requeue chunks stranded by dead runners. Idempotent across replicas."""
    while not stop.wait(REAPER_INTERVAL_SECONDS):
        db = SessionLocal()
        try:
            n = requeue_stale_chunks(db)
            if n:
                log.warning("Requeued %d chunk(s) whose runner stopped heartbeating", n)
        except Exception:
            log.exception("Stale-chunk reaper failed; will retry")
        finally:
            db.close()


def _retention_loop(stop: threading.Event) -> None:
    """Background task: delete expired code snapshots, Allure archives and cached reports."""
    while True:
        try:
            removed = store.purge_expired()
            if any(removed.values()):
                log.info("Retention removed %s", removed)
        except Exception:
            log.exception("Artifact retention sweep failed; will retry")
        if stop.wait(artifacts.RETENTION_SWEEP_SECONDS):
            return


@asynccontextmanager
async def lifespan(_app: FastAPI):
    stop = threading.Event()
    threading.Thread(target=_reaper_loop, args=(stop,), daemon=True).start()
    threading.Thread(target=_retention_loop, args=(stop,), daemon=True).start()
    yield
    stop.set()


app = FastAPI(title="Central Test Coordination Pod (SQL-Backed)", lifespan=lifespan)
# Two routers under /api. `api` (full token only) is the default: a new endpoint is full-only unless
# it is deliberately put on `pod_api` (pod token OR full token), which is exactly what a runner pod
# legitimately calls. Test code in a pod can read the pod token, so keep `pod_api` minimal.
api = APIRouter(prefix="/api", dependencies=[Depends(require_full_token)])
pod_api = APIRouter(prefix="/api", dependencies=[Depends(require_pod_or_full_token)])
live_view.add_routes(api, pod_api)


@app.exception_handler(artifacts.ArtifactError)
async def _artifact_error(_request: Request, exc: artifacts.ArtifactError):
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)


@app.get("/health")
def health_check():
    """Liveness: the process is up."""
    return {"status": "healthy"}


@app.get("/ready")
def readiness_check(db: Session = Depends(get_db)):
    """Readiness: the database is reachable, so the pod can serve leases."""
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        raise HTTPException(status_code=503, detail="Database unavailable")
    return {"status": "ready"}


@api.get("/config")
def get_config():
    """Choices the UI offers when starting a run."""
    return {
        "browsers": list(SUPPORTED_BROWSERS),
        "environments": SUPPORTED_ENVIRONMENTS,
        "default_browser": DEFAULT_BROWSER,
        "default_environment": DEFAULT_ENVIRONMENT,
        "default_chunk_size": DEFAULT_CHUNK_SIZE,
        "features": list(FEATURES),
    }


@api.post("/queue/push")
def push_tests(chunk: TestChunk, db: Session = Depends(get_db)):
    browser = validate_browser(chunk.browser)
    environment = validate_environment(chunk.environment)
    if db.get(TestChunkRecord, chunk.chunk_id) is not None:
        raise HTTPException(status_code=409, detail="Chunk ID already exists")

    db.add(
        TestChunkRecord(
            chunk_id=chunk.chunk_id,
            status="pending",
            browser=browser,
            environment=environment,
            tests=chunk.tests,
        )
    )
    db.commit()
    return {"status": "queued", "chunk_id": chunk.chunk_id, "browser": browser, "environment": environment}


@api.post("/runs")
def create_run(req: RunRequest, db: Session = Depends(get_db)):
    """Create a run: chunk the selected tests and queue them once per chosen browser."""
    environment = validate_environment(req.environment)
    browsers = list(dict.fromkeys(validate_browser(b) for b in req.browsers))
    tests = list(dict.fromkeys(req.tests))
    pytest_args = [a.model_dump() for a in req.pytest_args] or None

    run_id = f"run-{_utcnow():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    run = TestRunRecord(
        run_id=run_id,
        environment=environment,
        browsers=browsers,
        tests=tests,
        chunk_size=req.chunk_size,
        requested_by=req.requested_by,
        repo=req.repo,
        sha=req.sha,
        pytest_args=pytest_args,
    )
    db.add(run)
    chunks = [tests[i : i + req.chunk_size] for i in range(0, len(tests), req.chunk_size)]
    for browser in browsers:
        for n, chunk_tests in enumerate(chunks, start=1):
            db.add(
                TestChunkRecord(
                    chunk_id=f"{run_id}-{browser}-{n:03d}",
                    run_id=run_id,
                    status="pending",
                    browser=browser,
                    environment=environment,
                    tests=chunk_tests,
                    repo=req.repo,
                    sha=req.sha,
                    pytest_args=pytest_args,
                )
            )
    db.commit()
    return {"run_id": run_id, "chunks_queued": len(chunks) * len(browsers), "browsers": browsers,
            "environment": environment, "total_tests": len(tests) * len(browsers),
            "repo": req.repo, "sha": req.sha, "pytest_args": pytest_args or []}


@api.get("/runs")
def list_runs(limit: int = Query(25, ge=1, le=200), db: Session = Depends(get_db)):
    runs = db.query(TestRunRecord).order_by(TestRunRecord.created_at.desc()).limit(limit).all()
    return {"runs": summarize_runs(db, runs)}


@api.get("/runs/{run_id}")
def get_run(run_id: str, db: Session = Depends(get_db)):
    run = db.get(TestRunRecord, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    chunks = (
        db.query(TestChunkRecord)
        .filter(TestChunkRecord.run_id == run_id)
        .order_by(TestChunkRecord.browser, TestChunkRecord.chunk_id)
        .all()
    )
    with_allure = {name for name, _path, _st in store.list_allure(run_id)}
    return {
        **summarize_runs(db, [run])[0],
        "chunk_details": [
            {
                "chunk_id": c.chunk_id,
                "browser": c.browser,
                "status": c.status,
                "tests": c.tests,
                "assigned_runner": c.assigned_runner,
                "passed": c.tests_passed,
                "failed": c.tests_failed,
                "skipped": c.tests_skipped,
                "updated_at": _iso(c.updated_at),
                "results": _strip_logs(c.results),
                "has_allure": artifacts.safe_name(c.chunk_id) in with_allure,
            }
            for c in chunks
        ],
    }


@api.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str, db: Session = Depends(get_db)):
    """Stop a run: its queued and running chunks become cancelled; finished chunks keep their results.

    A cancelled chunk is never leased again, so the queue (and KEDA's pending count) shrinks at once. A runner that is
    already in the middle of a chunk finishes its pytest, but the results it then submits are refused with 403, which a
    runner already treats as "this pod no longer holds the chunk": it drops them and carries on. Idempotent: cancelling
    a finished or already cancelled run changes nothing. Full token only (a pod cannot cancel runs)."""
    run = db.get(TestRunRecord, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    cancelled = (
        db.query(TestChunkRecord)
        .filter(TestChunkRecord.run_id == run_id, TestChunkRecord.status.in_(("pending", "running")))
        .update({"status": "cancelled", "updated_at": _utcnow()}, synchronize_session=False)
    )
    db.commit()
    return {"cancelled_chunks": cancelled, **summarize_runs(db, [run])[0]}


@api.get("/chunks/{chunk_id}")
def get_chunk(chunk_id: str, db: Session = Depends(get_db)):
    """Full chunk record including pytest stdout/stderr."""
    c = db.get(TestChunkRecord, chunk_id)
    if c is None:
        raise HTTPException(status_code=404, detail="Chunk ID not found")
    return {
        "chunk_id": c.chunk_id, "run_id": c.run_id, "browser": c.browser,
        "environment": c.environment, "status": c.status, "tests": c.tests,
        "assigned_runner": c.assigned_runner, "results": c.results,
    }


@pod_api.get("/runner/lease")
def lease_chunk(runner_id: str, browser: str = DEFAULT_BROWSER, lease_tokens: bool = False,
                db: Session = Depends(get_db)):
    """Lease the oldest pending chunk for this browser. lease_tokens=1 adds a per-chunk lease_token."""
    browser = validate_browser(browser)
    # A runner only ever leases chunks queued for its own browser.
    row = lease_next_chunk(db, runner_id, browser, lease_tokens)

    if not row:
        touch_runner(db, runner_id, "idle", browser=browser)
        db.commit()
        raise HTTPException(status_code=404, detail="No tests available in queue")

    touch_runner(db, runner_id, "busy", browser=browser, chunk_id=row.chunk_id,
                 run_id=row.run_id, environment=row.environment)
    db.commit()
    leased = {
        "chunk_id": row.chunk_id,
        "tests": row.tests,
        "run_id": row.run_id,
        "environment": row.environment,
        "browser": row.browser,
        "repo": row.repo,
        "sha": row.sha,
        "pytest_args": row.pytest_args or [],
    }
    if row.lease_token is not None:  # the only response that ever carries it; absent for old runners
        leased["lease_token"] = row.lease_token
    return leased


@pod_api.post("/chunks/{chunk_id}/release")
def release_chunk(chunk_id: str, runner_id: str, db: Session = Depends(get_db),
                  x_lease_token: Optional[str] = Header(default=None)):
    """Return an unfinished chunk to the queue (runner is shutting down, e.g. SIGTERM)."""
    released = release_chunk_row(db, chunk_id, runner_id, x_lease_token)
    return {"status": "released" if released else "unchanged", "chunk_id": chunk_id}


@pod_api.post("/runners/{runner_id}/heartbeat")
def runner_heartbeat(runner_id: str, db: Session = Depends(get_db)):
    runner = db.get(RunnerRecord, runner_id)
    if runner is None:
        touch_runner(db, runner_id, "idle")
    else:
        runner.last_seen = _utcnow()
    db.commit()
    return {"status": "ok"}


@pod_api.post("/runners/{runner_id}/exit")
def runner_exit(runner_id: str, db: Session = Depends(get_db)):
    touch_runner(db, runner_id, "exited")
    db.commit()
    return {"status": "exited"}


@api.get("/pods")
def list_pods(exited_minutes: int = Query(60, ge=0, le=1440), db: Session = Depends(get_db)):
    """Runner pods and their status: Kubernetes phase (when available) merged with grid state."""
    now = _utcnow()
    cutoff = now - timedelta(minutes=exited_minutes)
    runners = {
        w.runner_id: w
        for w in db.query(RunnerRecord)
        .filter(or_(RunnerRecord.status != "exited", RunnerRecord.last_seen >= cutoff))
        .all()
    }
    kube = kube_list_pods()

    pods = []
    for name in sorted(set(runners) | set(kube["pods"])):
        w, k = runners.get(name), kube["pods"].get(name, {})
        pods.append(
            {
                "name": name,
                "browser": (w.browser if w else None) or k.get("browser"),
                "state": grid_state(w, k.get("phase"), now),
                "phase": k.get("phase"),
                "reason": k.get("reason"),
                "node": k.get("node"),
                "restarts": k.get("restarts"),
                "current_chunk": w.current_chunk if w else None,
                "run_id": w.run_id if w else None,
                "environment": w.environment if w else None,
                "started_at": k.get("started_at") or (_iso(w.started_at) if w else None),
                "last_seen": _iso(w.last_seen) if w else None,
                "live_view": live_view.available(w),
            }
        )
    counts: Dict[str, int] = {}
    for p in pods:
        counts[p["state"]] = counts.get(p["state"], 0) + 1
    return {
        "pods": pods,
        "counts": counts,
        "kubernetes": {"enabled": kube["enabled"], "error": kube["error"]},
    }


@pod_api.post("/results/submit")
def submit_results(payload: TestResultSubmission, db: Session = Depends(get_db),
                   x_lease_token: Optional[str] = Header(default=None)):
    # Row lock: the token check and the write happen on the same row version (no re-lease between).
    record = db.get(TestChunkRecord, payload.chunk_id, with_for_update=True)
    if not record:
        raise HTTPException(status_code=404, detail="Chunk ID not found")
    if record.status == "cancelled":   # before the token check: a chunk without a token would otherwise accept the write
        raise HTTPException(status_code=403, detail="This chunk's run was cancelled")
    require_lease_token(record.lease_token, x_lease_token)

    counts = count_outcomes(record.tests, payload.results)
    record.status = "completed"
    record.assigned_runner = payload.runner_id
    record.results = payload.results
    record.tests_passed = counts["passed"]
    record.tests_failed = counts["failed"]
    record.tests_skipped = counts["skipped"]
    touch_runner(db, payload.runner_id, "idle", browser=record.browser)
    db.commit()

    return {"status": "recorded", "chunk_id": payload.chunk_id}


@api.get("/results/summary")
def get_summary(db: Session = Depends(get_db)):
    def count(status: str) -> int:
        return db.query(TestChunkRecord).filter(TestChunkRecord.status == status).count()

    pending_by_browser = dict(
        db.query(TestChunkRecord.browser, func.count())
        .filter(TestChunkRecord.status == "pending")
        .group_by(TestChunkRecord.browser)
        .all()
    )
    completed_records = db.query(TestChunkRecord).filter(TestChunkRecord.status == "completed").all()
    details = {
        rec.chunk_id: {
            "run_id": rec.run_id,
            "browser": rec.browser,
            "environment": rec.environment,
            "runner_id": rec.assigned_runner,
            "results": rec.results,
        }
        for rec in completed_records
    }
    return {
        "pending_by_browser": pending_by_browser,
        "pending_chunks": count("pending"),
        "running_chunks": count("running"),
        "completed_chunks": len(completed_records),
        "cancelled_chunks": count("cancelled"),
        "details": details,
    }


# --- Code snapshots ----------------------------------------------------------------
@api.put("/code/{repo}/{sha}")
async def put_code(repo: str, sha: str, request: Request,
                   x_content_sha256: Optional[str] = Header(default=None)):
    """Store a gzip tarball of <repo> at <sha> (git archive layout); idempotent."""
    state, size = await store.put_code(repo, sha, x_content_sha256, request.stream(),
                                       request.headers.get("content-length"))
    if state == "exists":
        return {"status": "exists"}
    return JSONResponse({"status": "stored", "bytes": size}, status_code=201)


@pod_api.api_route("/code/{repo}/{sha}", methods=["GET", "HEAD"])
def get_code(repo: str, sha: str):
    found = store.code_file(repo, sha)
    if found is None:
        raise HTTPException(status_code=404, detail="Code snapshot not found")
    path, checksum = found
    return FileResponse(path, media_type="application/gzip", headers={"X-Content-SHA256": checksum})


# --- Allure results and reports ------------------------------------------------------
def _chunk_state(chunk_id: str):
    with SessionLocal() as db:  # short-lived: no pooled connection is held while the body uploads
        c = db.get(TestChunkRecord, chunk_id)
        return None if c is None else (c.status, c.run_id, c.lease_token)


@pod_api.put("/chunks/{chunk_id}/allure")
async def put_chunk_allure(chunk_id: str, request: Request,
                           x_lease_token: Optional[str] = Header(default=None)):
    """Store a pod's raw allure-results (tar.gz of the directory contents) for a completed chunk."""
    state = await run_in_threadpool(_chunk_state, chunk_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Chunk ID not found")
    require_lease_token(state[2], x_lease_token)  # before the body is read
    if state[0] != "completed":
        raise HTTPException(status_code=409, detail="Chunk has not completed yet")
    size = await store.put_allure(state[1], chunk_id, request.stream(), request.headers.get("content-length"))
    return {"status": "stored", "bytes": size}


@api.get("/runs/{run_id}/report/info")
def report_info(run_id: str, db: Session = Depends(get_db)):
    """Cheap: whether a report can be built and whether it is already cached. Never generates."""
    info = reports.info(run_id)
    total = db.query(TestChunkRecord).filter(TestChunkRecord.run_id == run_id).count()
    return {"available": info["available"], "chunks_with_allure": info["chunks_with_allure"],
            "chunks_total": total, "cached": info["cached"]}


@api.get("/runs/{run_id}/report")
async def get_report(run_id: str, request: Request):
    """Single-file Allure 3 report for the run, generated on demand and cached."""
    report = await reports.render(run_id, request.headers.get("if-none-match"))
    headers = {"ETag": f'"{report.key}"', "Cache-Control": "no-store"}
    if report.path is None:
        return Response(status_code=304, headers=headers)
    return FileResponse(report.path, media_type="text/html; charset=utf-8", headers=headers)


app.include_router(api)
app.include_router(pod_api)
