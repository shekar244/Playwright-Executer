"""
Shared mutable run state — imported by all blueprints.
Modify via attribute assignment: state._is_running = True
"""
from __future__ import annotations

import json
import queue
import threading
from typing import Optional

from ui_launcher.runner import TestRunner

_runner: Optional[TestRunner] = None
_is_running: bool = False
_output_queues: list[queue.Queue] = []
_run_lock = threading.Lock()

# Ring buffer: stores the last N SSE messages so reconnecting clients can
# catch up on lines they missed while the connection was down.
_recent_events: list[str] = []
_MAX_BUFFER = 400


def broadcast(event: str, data: str) -> None:
    global _recent_events
    msg = f"event: {event}\ndata: {json.dumps(data)}\n\n"
    # A new 'cmd' means a fresh run — clear the old buffer.
    if event == "cmd":
        _recent_events = []
    _recent_events.append(msg)
    if len(_recent_events) > _MAX_BUFFER:
        _recent_events = _recent_events[-_MAX_BUFFER:]
    for q in list(_output_queues):
        try:
            q.put_nowait(msg)
        except queue.Full:
            pass
