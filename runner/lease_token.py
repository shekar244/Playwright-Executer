"""The per-chunk lease secret: what the runner does with it, in one place.

Test code runs with the pod's uid and can read the pod's GRID_API_TOKEN, so that token alone
must be worth little. When the runner asks for a lease with lease_tokens=1 the coordinator
returns a secret that belongs to THAT chunk only; the runner sends it as X-Lease-Token on the
three calls that write the chunk (results submit, Allure upload, release). A coordinator
without lease secrets returns none, and then no header is sent and nothing changes.

The secret lives in the runner's memory only: it is never printed, never put in a URL, in the
pytest child's environment or arguments, or in a file. Everything here keeps it out of messages.
"""
import re

LEASE_TOKEN_HEADER = "X-Lease-Token"
_USABLE = re.compile(r"[A-Za-z0-9._~-]{8,256}")  # the coordinator sends hex; nothing that could break a header
_UNSAFE = re.compile(r"[^ -~]")  # anything but printable ASCII (no newlines: no forged log lines)
_DETAIL_LIMIT = 120


def accept(value, log=None) -> str | None:
    """The lease_token of a lease response when it is usable, else None (absent = an old coordinator).

    A malformed value is dropped with a note that does not repeat it: a bad header value would
    make `requests` raise an error whose message contains that value.
    """
    if value is None:
        return None
    if isinstance(value, str) and _USABLE.fullmatch(value):
        return value
    if log:
        log("Ignoring a malformed lease_token in the lease response.")
    return None


def with_token(headers: dict, lease_token: str | None) -> dict:
    """The request headers, plus X-Lease-Token when the chunk has a lease secret. A new dict."""
    return {**headers, LEASE_TOKEN_HEADER: lease_token} if lease_token else dict(headers)


def rejection(resp, lease_token: str | None = None) -> str:
    """Why the coordinator refused a request, for a log line: its `detail`, cleaned and shortened."""
    try:
        detail = resp.json().get("detail")
    except (ValueError, AttributeError):
        detail = None
    text = detail if isinstance(detail, str) and detail else f"HTTP {resp.status_code}"
    if lease_token:
        text = text.replace(lease_token, "***")
    return _UNSAFE.sub("?", text)[:_DETAIL_LIMIT]
