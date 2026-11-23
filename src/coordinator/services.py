"""Run summaries, runner bookkeeping, and Kubernetes pod discovery."""
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import requests
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from .config import (
    KUBE_SA_DIR, LEASE_TIMEOUT_SECONDS, RUNNER_POD_SELECTOR, RUNNER_STALE_SECONDS,
)
from .db import TestChunkRecord, TestRunRecord, RunnerRecord, _utcnow

def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def touch_runner(
    db: Session,
    runner_id: str,
    status: str,
    browser: Optional[str] = None,
    chunk_id: Optional[str] = None,
    run_id: Optional[str] = None,
    environment: Optional[str] = None,
) -> None:
    now = _utcnow()
    runner = db.get(RunnerRecord, runner_id)
    if runner is None:
        runner = RunnerRecord(runner_id=runner_id, started_at=now)
        db.add(runner)
    runner.browser = browser or runner.browser
    runner.status = status
    runner.current_chunk = chunk_id
    runner.run_id = run_id
    runner.environment = environment
    runner.last_seen = now
    if status == "exited":                       # the pod is gone: so is its live view
        runner.view_host = runner.view_secret = None


def count_outcomes(tests: List[str], results: List[Dict[str, Any]]) -> Dict[str, int]:
    per_test = [t for res in results for t in res.get("test_results", [])]
    if per_test:
        outcomes = [t.get("outcome") for t in per_test]
        return {
            "passed": outcomes.count("passed"),
            "failed": outcomes.count("failed") + outcomes.count("error"),
            "skipped": outcomes.count("skipped"),
        }
    # Legacy runners only report a chunk-level success flag.
    ok = bool(results) and all(res.get("success") for res in results)
    return {"passed": len(tests) if ok else 0, "failed": 0 if ok else len(tests), "skipped": 0}


def _strip_logs(results: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    return [{k: v for k, v in r.items() if k not in ("stdout", "stderr")} for r in results or []]


def summarize_runs(db: Session, runs: List[TestRunRecord]) -> List[Dict[str, Any]]:
    ids = [r.run_id for r in runs]
    agg: Dict[str, Dict[str, int]] = {i: {} for i in ids}
    if ids:
        rows = (
            db.query(
                TestChunkRecord.run_id,
                TestChunkRecord.status,
                func.count(),
                func.coalesce(func.sum(TestChunkRecord.tests_passed), 0),
                func.coalesce(func.sum(TestChunkRecord.tests_failed), 0),
                func.coalesce(func.sum(TestChunkRecord.tests_skipped), 0),
            )
            .filter(TestChunkRecord.run_id.in_(ids))
            .group_by(TestChunkRecord.run_id, TestChunkRecord.status)
            .all()
        )
        for run_id, status, n, passed, failed, skipped in rows:
            a = agg[run_id]
            a[status] = a.get(status, 0) + n
            a["passed"] = a.get("passed", 0) + int(passed)
            a["failed"] = a.get("failed", 0) + int(failed)
            a["skipped"] = a.get("skipped", 0) + int(skipped)

    out = []
    for run in runs:
        a = agg[run.run_id]
        pending, running, done = a.get("pending", 0), a.get("running", 0), a.get("completed", 0)
        cancelled = a.get("cancelled", 0)
        total = pending + running + done + cancelled
        if cancelled and not (pending or running):
            status = "cancelled"          # cancel_run leaves nothing queued or running; finished chunks keep their results
        else:
            status = "completed" if total and done == total else ("running" if running or done else "queued")
        outcome = "failed" if a.get("failed") else ("passed" if status == "completed" else None)
        out.append(
            {
                "run_id": run.run_id,
                "environment": run.environment,
                "browsers": run.browsers,
                "tests": run.tests,
                "total_tests": len(run.tests) * len(run.browsers),
                "chunk_size": run.chunk_size,
                "requested_by": run.requested_by,
                "repo": run.repo,
                "sha": run.sha,
                "pytest_args": run.pytest_args or [],
                "created_at": _iso(run.created_at),
                "status": status,
                "outcome": outcome,
                "chunks": {"total": total, "pending": pending, "running": running, "completed": done,
                           "cancelled": cancelled},
                "passed": a.get("passed", 0),
                "failed": a.get("failed", 0),
                "skipped": a.get("skipped", 0),
            }
        )
    return out


def kube_list_pods() -> Dict[str, Any]:
    """List runner pods through the in-cluster API. Disabled outside a cluster."""
    token_path = os.path.join(KUBE_SA_DIR, "token")
    if not os.path.exists(token_path):
        return {"enabled": False, "error": None, "pods": {}}
    try:
        with open(token_path) as f:
            token = f.read().strip()
        namespace = os.getenv("KUBE_NAMESPACE")
        if not namespace:
            with open(os.path.join(KUBE_SA_DIR, "namespace")) as f:
                namespace = f.read().strip()
        host = os.getenv("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc")
        port = os.getenv("KUBERNETES_SERVICE_PORT", "443")
        resp = requests.get(
            f"https://{host}:{port}/api/v1/namespaces/{namespace}/pods",
            params={"labelSelector": RUNNER_POD_SELECTOR},
            headers={"Authorization": f"Bearer {token}"},
            verify=os.path.join(KUBE_SA_DIR, "ca.crt"),
            timeout=3,
        )
        resp.raise_for_status()
    except Exception as exc:  # surfaced to the UI instead of failing the whole call
        return {"enabled": True, "error": str(exc), "pods": {}}

    pods = {}
    for item in resp.json().get("items", []):
        meta, status = item.get("metadata", {}), item.get("status", {})
        containers = status.get("containerStatuses") or [{}]
        state = containers[0].get("state", {})
        reason = next((s.get("reason") for s in state.values() if isinstance(s, dict)), None)
        pods[meta["name"]] = {
            "phase": status.get("phase"),
            "browser": (meta.get("labels") or {}).get("browser"),
            "node": item.get("spec", {}).get("nodeName"),
            "started_at": status.get("startTime") or meta.get("creationTimestamp"),
            "restarts": containers[0].get("restartCount", 0),
            "reason": reason,
        }
    return {"enabled": True, "error": None, "pods": pods}


def grid_state(runner: Optional[RunnerRecord], phase: Optional[str], now: datetime) -> str:
    """Single status word for the dashboard: starting, idle, busy, lost, failed, exited."""
    if phase == "Failed":
        return "failed"
    if runner is None:
        return "exited" if phase == "Succeeded" else "starting"
    if runner.status == "exited" or phase == "Succeeded":
        return "exited"
    if (now - runner.last_seen) > timedelta(seconds=RUNNER_STALE_SECONDS):
        return "lost"
    return runner.status


def requeue_stale_chunks(db: Session) -> int:
    """Return chunks to the queue whose runner is gone (SIGKILL, OOMKill, node loss).

    A running chunk is stale when it has not been touched for LEASE_TIMEOUT_SECONDS and its
    runner has no recent heartbeat. Runners that are alive keep their chunks, however long
    pytest runs, because the heartbeat thread keeps runners.last_seen fresh. The lease token is
    cleared with the requeue, so the dead (or merely stalled) pod's secret stops working.
    """
    result = db.execute(
        text(
            """
            UPDATE test_queue q
            SET status = 'pending', assigned_runner = NULL, lease_token = NULL, updated_at = NOW()
            WHERE q.status = 'running'
              AND q.updated_at < NOW() - make_interval(secs => :timeout)
              AND NOT EXISTS (
                  SELECT 1 FROM runners w
                  WHERE w.runner_id = q.assigned_runner
                    AND w.status <> 'exited'
                    AND w.last_seen > NOW() - make_interval(secs => :timeout)
              )
            """
        ),
        {"timeout": LEASE_TIMEOUT_SECONDS},
    )
    db.commit()
    return result.rowcount
