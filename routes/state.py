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


def broadcast(event: str, data: str) -> None:
    msg = f"event: {event}\ndata: {json.dumps(data)}\n\n"
    for q in list(_output_queues):
        try:
            q.put_nowait(msg)
        except queue.Full:
            pass
