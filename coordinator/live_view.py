"""Live view: watching a runner pod's browser while its tests run.

A pod started with GRID_LIVE_VIEW=1 runs the browser headed on a virtual display (Xvfb) and serves that display
with x11vnc on LIVE_VIEW_PORT, behind a random password. It registers here; Amplify asks for the address and the
password (full token only) and proxies the user's viewer to the pod. The pod's address is the one this coordinator
saw the registration come from, never one the pod names, and the port is fixed: test code can read the pod token,
so a registration must not be able to point Amplify at an arbitrary host or port.

  POST /api/runners/{runner_id}/view   pod token   {"password": ...}: this pod serves a live view
  GET  /api/runners/{runner_id}/view   full token  address, port and password of a pod's live view (404 if none)
"""
import ipaddress
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from db import RunnerRecord, _utcnow, get_db
from services import grid_state

LIVE_VIEW_PORT = 5900
FEATURE = "live-view"
_SECRET = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_RUNNER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,252}$")


class ViewRegistration(BaseModel):
    password: str

    @field_validator("password")
    @classmethod
    def _password(cls, value: str) -> str:
        if not _SECRET.match(value):
            raise ValueError("password must be 8-64 characters of A-Z, a-z, 0-9, '_' or '-'")
        return value


def caller_address(request: Request) -> Optional[str]:
    """The IP address the request came from, or None when there is none or it is not an IP address."""
    host = request.client.host if request.client else None
    try:
        return str(ipaddress.ip_address(host)) if host else None
    except ValueError:
        return None


def available(runner: Optional[RunnerRecord]) -> bool:
    """A pod's live view can be watched: registered, and the pod is alive (idle or busy, heartbeating)."""
    return bool(runner and runner.view_host and runner.view_secret
                and grid_state(runner, None, _utcnow()) in ("idle", "busy"))


def add_routes(api: APIRouter, pod_api: APIRouter) -> None:
    @pod_api.post("/runners/{runner_id}/view")
    def register_view(runner_id: str, body: ViewRegistration, request: Request, db: Session = Depends(get_db)):
        if not _RUNNER_ID.match(runner_id):
            raise HTTPException(status_code=400, detail="Invalid runner id")
        host = caller_address(request)
        if host is None:
            raise HTTPException(status_code=400, detail="The pod's address is unknown")
        now = _utcnow()
        runner = db.get(RunnerRecord, runner_id)
        if runner is None:
            runner = RunnerRecord(runner_id=runner_id, status="idle", started_at=now)
            db.add(runner)
        elif runner.view_host and runner.view_host != host and available(runner):
            # Every pod holds the same pod token: one must not take over another live pod's view.
            raise HTTPException(status_code=409, detail="This runner's live view was registered from another address")
        runner.view_host, runner.view_secret, runner.last_seen = host, body.password, now
        db.commit()
        return {"status": "ok", "port": LIVE_VIEW_PORT}

    @api.get("/runners/{runner_id}/view")
    def get_view(runner_id: str, db: Session = Depends(get_db)):
        runner = db.get(RunnerRecord, runner_id) if _RUNNER_ID.match(runner_id) else None
        if not available(runner):
            raise HTTPException(status_code=404, detail="This pod has no live view (it finished, or runs headless)")
        return {
            "runner_id": runner.runner_id, "host": runner.view_host, "port": LIVE_VIEW_PORT,
            "password": runner.view_secret, "status": runner.status, "browser": runner.browser,
            "run_id": runner.run_id, "chunk_id": runner.current_chunk,
        }
