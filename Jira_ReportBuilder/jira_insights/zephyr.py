"""
Zephyr Squad connectivity — test cycles and executions for execution reports.

Two deployments, one interface (test · project · versions · cycles · zql · display_names):

  ZephyrCloud   Zephyr Squad Cloud API at prod-api.zephyr4jiracloud.com, every
                request signed with a short-lived JWT (access key / secret key /
                Atlassian account id). Ported from Amplify QEA routes/zephyr.py —
                same JWT claims, canonical query string, headers and endpoints:
                  cycles      GET  /public/rest/api/1.0/cycles/search?projectId&versionId
                  test runs   POST /public/rest/api/1.0/zql/search            (offset / maxRecords)
                Rate-limited calls (HTTP 429) are retried, honouring Retry-After.
  ZephyrServer  Zephyr Squad Server / Data Center (ZAPI on the Jira host), using the
                Jira login (Basic or Bearer PAT) through JiraClient:
                  statuses    GET  /rest/zapi/latest/util/testExecutionStatus
                  cycles      GET  /rest/zapi/latest/cycle?projectId&versionId
                  test runs   GET  /rest/zapi/latest/zql/executeSearch?zqlQuery&offset&maxRecords

Test runs are always fetched with ZQL — the drill-down (project → version → cycles) is
turned into a query by zql.drilldown_zql — one paged search instead of an executions
call per cycle, which tripped Zephyr's rate limits on large versions.
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
from typing import Callable, Optional

from .jira_client import JiraClient, JiraError, _error_message, _retry_after, _ssl_context
from .settings import JiraSettings

ZAPI_BASE = "https://prod-api.zephyr4jiracloud.com/connect"
STATUSES_PATH = "/public/rest/api/1.0/util/statuses"
ZAPI_SERVER = "/rest/zapi/latest"
PAGE_SIZE = 50
MAX_RETRIES = 4

ProgressFn = Callable[[int, Optional[int]], None]


# ── JWT (Zephyr Squad Cloud) ──────────────────────────────────────────────────

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


# ── Shared helpers ────────────────────────────────────────────────────────────

def normalize_cycles(data) -> list[dict]:
    """Cycle lists come back as a list, or as {cycleId: {...}, "recordsCount": n} (same handling as Amplify)."""
    if isinstance(data, list):
        items = [c for c in data if isinstance(c, dict)]
    elif isinstance(data, dict):
        items = [{"id": k, **v} for k, v in data.items() if isinstance(v, dict)]
    else:
        items = []
    return [{"id": str(c.get("id", "")), "name": c.get("name") or str(c.get("id", ""))}
            for c in items if c.get("name") or c.get("id") not in (None, "")]


def _page(data) -> tuple[list, int | None]:
    if not isinstance(data, dict):
        return [], None
    rows = data.get("searchObjectList") or data.get("executions") or []
    total = data.get("totalCount", data.get("total"))
    return rows, int(total) if isinstance(total, (int, float, str)) and str(total).isdigit() else None


class _RateLimited(Exception):
    def __init__(self, error: urllib.error.HTTPError):
        super().__init__("rate limited")
        self.error = error


# ── Zephyr Squad Cloud ────────────────────────────────────────────────────────

class ZephyrCloud:
    mode = "cloud"

    def __init__(self, settings: JiraSettings, opener=None, sleep=time.sleep):
        if not settings.zephyr_cloud_keys:
            raise JiraError(0, "Zephyr Squad Cloud needs the Zephyr access key, secret key and Atlassian "
                               "account id — or set Zephyr type to Server / DC.")
        self._settings = settings
        self._open = opener or urllib.request.urlopen
        self._sleep = sleep
        self._ctx = _ssl_context(settings.verify_ssl)

    def call(self, method: str, path: str, params: dict | None = None, body=None):
        for attempt in range(MAX_RETRIES + 1):
            try:
                return self._call_once(method, path, params, body)
            except _RateLimited as limited:
                if attempt == MAX_RETRIES:
                    raise JiraError(429, "Zephyr rate limit — still limited after retries; try again shortly "
                                         "or narrow the selection") from None
                self._sleep(_retry_after(limited.error, attempt))
        raise JiraError(429, "Zephyr rate limit")

    def _call_once(self, method: str, path: str, params: dict | None, body):
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
            if e.code == 429:
                raise _RateLimited(e) from None
            raise JiraError(e.code, _error_message(e)) from None
        except urllib.error.URLError as e:
            raise JiraError(0, f"Cannot reach Zephyr at {ZAPI_BASE}: {e.reason}") from None

    def project(self, key: str) -> dict:
        return JiraClient(self._settings, opener=self._open).project(key)

    def versions(self, key: str) -> list[dict]:
        return JiraClient(self._settings, opener=self._open).versions(key)

    def display_names(self, user_ids) -> dict[str, str]:
        """Account ids → names through the Jira REST API (needs the Jira URL + token too)."""
        if not self._settings.configured:
            return {}
        return JiraClient(self._settings, opener=self._open).display_names(user_ids)

    def test(self) -> str:
        statuses = self.call("GET", STATUSES_PATH)
        count = len(statuses) if isinstance(statuses, (dict, list)) else 0
        return f"Zephyr Cloud authenticated · {count} execution statuses available"

    def cycles(self, project_id: str, version_id: str) -> list[dict]:
        return normalize_cycles(self.call("GET", "/public/rest/api/1.0/cycles/search",
                                          {"projectId": project_id, "versionId": version_id}))

    def zql(self, query: str, max_records: int = 5000, on_progress: ProgressFn | None = None) -> list[dict]:
        rows: list[dict] = []
        while len(rows) < max_records:
            page, total = _page(self.call("POST", "/public/rest/api/1.0/zql/search", body={
                "zqlQuery": query, "offset": len(rows), "maxRecords": min(PAGE_SIZE, max_records - len(rows))}))
            rows.extend(page)
            if on_progress:
                on_progress(len(rows), total)
            if not page or (total is not None and len(rows) >= total):
                break
        return rows[:max_records]


# ── Zephyr Squad Server / Data Center ─────────────────────────────────────────

class ZephyrServer:
    mode = "server"

    def __init__(self, settings: JiraSettings, opener=None, jira: JiraClient | None = None):
        self._jira = jira or JiraClient(settings, opener=opener)

    def project(self, key: str) -> dict:
        return self._jira.project(key)

    def versions(self, key: str) -> list[dict]:
        return self._jira.versions(key)

    def display_names(self, user_ids) -> dict[str, str]:
        return self._jira.display_names(user_ids)

    def test(self) -> str:
        statuses = self._jira.get(f"{ZAPI_SERVER}/util/testExecutionStatus")
        count = len(statuses) if isinstance(statuses, (dict, list)) else 0
        return f"Zephyr Server authenticated with your Jira login · {count} execution statuses available"

    def cycles(self, project_id: str, version_id: str) -> list[dict]:
        return normalize_cycles(self._jira.get(f"{ZAPI_SERVER}/cycle",
                                               {"projectId": project_id, "versionId": version_id}))


    def zql(self, query: str, max_records: int = 5000, on_progress: ProgressFn | None = None) -> list[dict]:
        rows: list[dict] = []
        while len(rows) < max_records:
            page, total = _page(self._jira.get(f"{ZAPI_SERVER}/zql/executeSearch", {
                "zqlQuery": query, "offset": len(rows), "maxRecords": min(PAGE_SIZE, max_records - len(rows))}))
            rows.extend(page)
            if on_progress:
                on_progress(len(rows), total)
            if not page or (total is not None and len(rows) >= total):
                break
        return rows[:max_records]


def zephyr_client(settings: JiraSettings, opener=None):
    """The right client for settings.zephyr_mode (cloud keys → Cloud, otherwise Server / DC)."""
    return ZephyrCloud(settings, opener) if settings.zephyr_mode == "cloud" else ZephyrServer(settings, opener)


# ── What the 🧪 Zephyr panel needs to know (same functions in Amplify QEA's zephyr.py) ──

def is_configured(settings: JiraSettings) -> bool:
    return settings.zephyr_configured


def default_project_key(settings: JiraSettings) -> str:
    return settings.project_key


def setup_hint() -> str:
    return ("Set up Zephyr under **🔌 Jira / Zephyr config** — Cloud needs the Zephyr API keys; "
            "Server / DC uses your Jira login.")


def backend_label(settings: JiraSettings) -> str:
    return "Zephyr Squad Cloud" if settings.zephyr_mode == "cloud" else "Zephyr Server / DC"

