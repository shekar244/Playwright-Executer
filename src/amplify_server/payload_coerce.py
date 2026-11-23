"""Reading numbers and text out of payloads Amplify does not control.

A Grid run's detail comes from the coordinator, but its per-test results were written by the pods' test code
(anything a repo's conftest.py makes of them), and a run-history record is a file on disk. Nothing in those
payloads can be trusted to have the shape it should, so the dashboard reads them only through these helpers:
every one answers with a plain value of the type asked for, and none of them raises.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

MAX_COUNT = 1_000_000_000                       # no real run has more tests than this; keeps sums far from overflow
MAX_DURATION_S = 10 * 365 * 86_400              # ten years: longer than any real test, still safe to add up
MAX_TS_MS = 4_102_444_800_000                   # 2100-01-01 UTC; later dates only come from corrupt data
MAX_TEXT = 1000


def count(value: Any) -> int:
    """A test count: a whole number from 0 up. Anything else (bool, NaN, inf, text that is not a plain
    whole number, a list, a dict, None) is 0; a float is cut to its whole part."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, float):
        value = int(value) if math.isfinite(value) else 0
    elif isinstance(value, str):
        value = int(value) if value.isascii() and value.isdigit() and len(value) < 20 else 0
    elif not isinstance(value, int):
        return 0
    return min(max(value, 0), MAX_COUNT)


def duration(value: Any) -> float | None:
    """Seconds as a finite, non-negative number up to MAX_DURATION_S — else None. A bool, a string, a
    list, a dict, NaN, +/-inf, a negative or an absurdly long time is not a duration."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        seconds = float(value)
    except OverflowError:                        # an integer too big for a float
        return None
    return seconds if math.isfinite(seconds) and 0 <= seconds <= MAX_DURATION_S else None


def text(value: Any, limit: int = MAX_TEXT) -> str:
    """Text: a string (cut to `limit`) or a finite number written out; anything else is ''."""
    if isinstance(value, str):
        return value[:limit]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return ""
    if isinstance(value, float) and not math.isfinite(value):
        return ""
    try:
        return str(value)[:limit]
    except ValueError:                           # an integer with thousands of digits
        return ""


def message(value: Any, limit: int = 2000) -> str | None:
    """A pod's message about a test: its text, or None when it is not text."""
    return value[:limit] if isinstance(value, str) else None


def epoch_ms(value: Any) -> int:
    """Milliseconds since 1970 for a plausible date (1970 < t <= 2100), else 0."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, float):
        value = int(value) if math.isfinite(value) else 0
    elif isinstance(value, str):
        value = int(value) if value.isascii() and value.isdigit() and len(value) < 20 else 0
    elif not isinstance(value, int):
        return 0
    return value if 0 < value <= MAX_TS_MS else 0


def iso_to_ms(value: Any) -> int | None:
    """An ISO-8601 timestamp (no zone means UTC) as epoch milliseconds, or None when it is not one or is
    not a plausible date."""
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        ms = int(parsed.timestamp() * 1000)
    except (ValueError, OverflowError, OSError):
        return None
    return ms if 0 < ms <= MAX_TS_MS else None


def dicts(value: Any) -> list[dict]:
    """The dict items of a list; [] when `value` is not a list."""
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def texts(value: Any) -> list[str]:
    """The items of a list, each as text (see `text`); [] when `value` is not a list."""
    return [text(v) for v in value] if isinstance(value, list) else []
