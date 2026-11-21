"""Per-chunk lease secrets: generation, enforcement, and the lease/release statements.

Why: test code runs inside the runner pods and can read the pod's API token, so a pod must only be
able to touch the chunk it currently holds. A runner that asks for a lease with ?lease_tokens=1
gets a random lease_token for that chunk. While the chunk row holds one, the submit, Allure upload
and release endpoints require it in the X-Lease-Token header. Rules:

* a chunk leased without lease_tokens=1 (old runners) has a NULL token and nothing is enforced;
* the token is KEPT after completion (the Allure upload follows the submit; a retried submit by
  the holder still works) and CLEARED whenever the chunk goes back to pending (release endpoint,
  reaper requeue in services.py), so a stale pod cannot write to a re-leased chunk;
* it is never serialised anywhere except the lease response and never logged.

Imports no coordinator code, so the pure parts are testable without a database.
"""
import secrets
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

LEASE_TOKEN_DENIED = "Invalid lease token for this chunk"


def new_lease_token() -> str:
    return secrets.token_hex(16)


def lease_token_ok(stored: Optional[str], presented: Optional[str]) -> bool:
    """True when the chunk is not protected (NULL token) or the presented token equals it."""
    if stored is None:
        return True
    # Bytes: compare_digest raises TypeError on non-ASCII str input.
    return secrets.compare_digest((presented or "").encode("utf-8"), stored.encode("utf-8"))


def require_lease_token(stored: Optional[str], presented: Optional[str]) -> None:
    if not lease_token_ok(stored, presented):
        raise HTTPException(status_code=403, detail=LEASE_TOKEN_DENIED)


# lease_token is always (re)written at lease time: a fresh secret, or NULL for a runner that did
# not ask for one. Atomic row-level lock skipping rows already locked by other runners.
_LEASE = text(
    """
    UPDATE test_queue
    SET status = 'running',
        assigned_runner = :runner_id,
        lease_token = :lease_token,
        updated_at = NOW()
    WHERE chunk_id = (
        SELECT chunk_id
        FROM test_queue
        WHERE status = 'pending' AND browser = :browser
        ORDER BY created_at ASC
        LIMIT 1
        FOR UPDATE SKIP LOCKED
    )
    RETURNING chunk_id, tests, run_id, environment, browser, repo, sha, lease_token;
    """
)

# The token condition is in the UPDATE itself, so a chunk re-leased between the pre-check in
# release_chunk_row() and this statement is still not released by a stale token.
_RELEASE = text(
    """
    UPDATE test_queue
    SET status = 'pending', assigned_runner = NULL, lease_token = NULL, updated_at = NOW()
    WHERE chunk_id = :chunk_id AND status = 'running' AND assigned_runner = :runner_id
      AND (lease_token IS NULL OR lease_token = :presented)
    """
)

_STORED_TOKEN = text("SELECT lease_token FROM test_queue WHERE chunk_id = :chunk_id")


def lease_next_chunk(db: Session, runner_id: str, browser: str, with_token: bool) -> Any:
    """Lease the oldest pending chunk for `browser`; None when the queue has none."""
    params = {"runner_id": runner_id, "browser": browser,
              "lease_token": new_lease_token() if with_token else None}
    return db.execute(_LEASE, params).fetchone()


def release_chunk_row(db: Session, chunk_id: str, runner_id: str, presented: Optional[str]) -> bool:
    """Return a running chunk to the queue; True when a row changed. 403 on a wrong lease token."""
    require_lease_token(db.execute(_STORED_TOKEN, {"chunk_id": chunk_id}).scalar(), presented)
    result = db.execute(_RELEASE, {"chunk_id": chunk_id, "runner_id": runner_id, "presented": presented})
    db.commit()
    return bool(result.rowcount)
