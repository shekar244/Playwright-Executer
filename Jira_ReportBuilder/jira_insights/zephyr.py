"""
Zephyr Squad Cloud connectivity — ported from Amplify QEA (routes/zephyr.py),
without Flask.

Zephyr's API authenticates every request with a short-lived JWT signed by the
Zephyr secret key: `iss` = access key, `sub` = Atlassian account id, and `qsh`
= SHA-256 of the canonical "METHOD&path&sorted-query" string.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from .jira_client import JiraError, _error_message, _ssl_context
from .settings import JiraSettings

ZAPI_BASE = "https://prod-api.zephyr4jiracloud.com/connect"
STATUSES_PATH = "/public/rest/api/1.0/util/statuses"     # cheap authenticated call for "Test"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def canonical_qs(params: dict | None) -> str:
    if not params:
        return ""
    items: list[tuple[str, str]] = []
    for key, value in params.items():
        if value is None:
            continue
        values = value if isinstance(value, (list, tuple)) else [value]
        items.extend((str(key), str(v)) for v in values)
    items.sort()
    safe = "~._-"
    return "&".join(f"{urllib.parse.quote(k, safe=safe)}={urllib.parse.quote(v, safe=safe)}" for k, v in items)


def build_qsh(method: str, api_path: str, query_params: dict | None = None) -> str:
    path = api_path if api_path.startswith("/") else f"/{api_path}"
    canonical = f"{method.upper().strip()}&{path}&{canonical_qs(query_params)}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def zephyr_jwt(access_key: str, secret_key: str, account_id: str, method: str, path: str,
               query_params: dict | None = None, expires_in: int = 3600, now: int | None = None) -> str:
    issued = int(time.time()) if now is None else now
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode("utf-8"))
    payload = _b64url(json.dumps({
        "sub": account_id, "qsh": build_qsh(method, path, query_params), "iss": access_key,
        "iat": issued, "exp": issued + expires_in, "nonce": uuid.uuid4().hex,
    }, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header}.{payload}".encode("ascii")
    signature = hmac.new(secret_key.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64url(signature)}"


class ZephyrClient:
    def __init__(self, settings: JiraSettings, opener=None):
        if not settings.zephyr_configured:
            raise JiraError(0, "Zephyr is not configured — add the Zephyr access key, secret key "
                               "and Atlassian account id.")
        self._settings = settings
        self._open = opener or urllib.request.urlopen
        self._ctx = _ssl_context(settings.verify_ssl)

    def call(self, method: str, path: str, params: dict | None = None, body=None):
        s = self._settings
        token = zephyr_jwt(s.zephyr_access_key, s.zephyr_secret_key, s.zephyr_account_id,
                           method, path, params, expires_in=60)
        headers = {"Authorization": f"JWT {token}", "zapiAccessKey": s.zephyr_access_key,
                   "Accept": "application/json"}
        if method.upper() in ("POST", "PUT", "PATCH"):
            headers["Content-Type"] = "application/json"
        url = ZAPI_BASE + path
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
        try:
            with self._open(req, timeout=30, context=self._ctx) as resp:
                raw = resp.read()
            return json.loads(raw.decode("utf-8")) if raw.strip() else {}
        except urllib.error.HTTPError as e:
            raise JiraError(e.code, _error_message(e)) from None
        except urllib.error.URLError as e:
            raise JiraError(0, f"Cannot reach Zephyr at {ZAPI_BASE}: {e.reason}") from None

    def test(self) -> str:
        statuses = self.call("GET", STATUSES_PATH)
        count = len(statuses) if isinstance(statuses, (dict, list)) else 0
        return f"Zephyr authenticated · {count} execution statuses available"
