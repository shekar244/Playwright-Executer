"""
Minimal Jira REST client for bulk JQL pulls.

  • Jira Cloud            — enhanced search  GET /rest/api/latest/search/jql  (nextPageToken)
  • Jira Server / DC      — classic search   GET /rest/api/2/search           (startAt / total)

Same endpoints, order and headers as Amplify QEA's _jira_search_jql: the
enhanced endpoint first, falling back to classic on 404/405/410 or when the
response has no "issues". Paging and HTTP 429 retries (Retry-After) are added.

Auth (settings.auth_type):
  basic   Authorization: Basic base64(username:token)   — Cloud email + API token, DC username + password
  bearer  Authorization: Bearer token                   — Data Center personal access token (PAT)
  auto    Basic when a username is set, else Bearer; and on a non-Cloud host a
          Basic 401 is retried once as Bearer (a DC PAT entered with a username).
"""
from __future__ import annotations

import base64
import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Optional

from .settings import JiraSettings

ProgressFn = Callable[[int, Optional[int]], None]   # (fetched, total-or-None)

_FALLBACK_CODES = (404, 405, 410)


def _ssl_context(verify: bool) -> ssl.SSLContext:
    """certifi CA bundle when verifying; when not, also tolerate legacy corporate TLS proxies."""
    if not verify:
        ctx = ssl._create_unverified_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        if hasattr(ssl, "OP_LEGACY_SERVER_CONNECT"):
            ctx.options |= ssl.OP_LEGACY_SERVER_CONNECT
        try:
            ctx.set_ciphers("DEFAULT@SECLEVEL=0")
        except ssl.SSLError:
            pass
        return ctx
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


class _NoIssues(Exception):
    """The enhanced search answered without an "issues" list — use classic search instead."""


class JiraError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _error_message(e: urllib.error.HTTPError) -> str:
    raw = e.read().decode("utf-8", errors="replace")
    try:
        body = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return raw.strip() or f"HTTP {e.code}"
    parts = list(body.get("errorMessages") or []) + [f"{k}: {v}" for k, v in (body.get("errors") or {}).items()]
    return "; ".join(parts) or body.get("message") or f"HTTP {e.code}"


def _retry_after(e: urllib.error.HTTPError, attempt: int) -> float:
    try:
        return max(float(e.headers.get("Retry-After", "")), 1.0)
    except (TypeError, ValueError):
        return 2.0 ** attempt


class JiraClient:
    PAGE_SIZE   = 100
    MAX_RETRIES = 3

    def __init__(self, settings: JiraSettings, opener=None, sleep=time.sleep):
        if not settings.configured:
            raise JiraError(0, "Jira is not configured — set the Jira URL and API token under "
                               "🔌 Jira / Zephyr config (or JIRA_URL / JIRA_API_TOKEN).")
        self._settings = settings
        self._open     = opener or urllib.request.urlopen
        self._sleep    = sleep
        self._ctx      = _ssl_context(settings.verify_ssl)
        mode = settings.auth_type if settings.auth_type in ("basic", "bearer") else "auto"
        self._scheme   = mode if mode != "auto" else ("basic" if settings.username else "bearer")
        self._can_fall_back = mode == "auto" and self._scheme == "basic" and not settings.is_cloud

    @property
    def auth_scheme(self) -> str:
        return self._scheme

    # ── HTTP ──────────────────────────────────────────────────────────────────

    def _headers(self) -> dict:
        s = self._settings
        if self._scheme == "basic":
            auth = "Basic " + base64.b64encode(f"{s.username}:{s.token}".encode()).decode()
        else:
            auth = f"Bearer {s.token}"
        return {"Authorization": auth, "Content-Type": "application/json", "Accept": "application/json"}

    def get(self, path: str, params: dict | None = None):
        """GET a path on the Jira host (REST API, or ZAPI for Zephyr Server) with this client's auth."""
        return self._get(path, params)

    def _get(self, path: str, params: dict | None = None):
        url = self._settings.url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        for attempt in range(self.MAX_RETRIES + 1):
            req = urllib.request.Request(url, headers=self._headers(), method="GET")
            try:
                with self._open(req, timeout=60, context=self._ctx) as resp:
                    raw = resp.read()
                return json.loads(raw.decode("utf-8")) if raw.strip() else {}
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < self.MAX_RETRIES:
                    self._sleep(_retry_after(e, attempt))
                    continue
                if e.code == 401 and self._can_fall_back:
                    self._scheme, self._can_fall_back = "bearer", False     # DC PAT with a username
                    return self._get(path, params)
                raise JiraError(e.code, _error_message(e)) from None
            except urllib.error.URLError as e:
                raise JiraError(0, f"Cannot reach Jira at {self._settings.url}: {e.reason}") from None
        raise JiraError(429, "Jira rate limit — retries exhausted")

    # ── API ───────────────────────────────────────────────────────────────────

    def myself(self) -> dict:
        return self._get("/rest/api/2/myself")

    def field_names(self) -> dict[str, str]:
        """Map field id → display name (customfield_10016 → 'Story Points')."""
        fields = self._get("/rest/api/2/field")
        if not isinstance(fields, list):
            return {}
        return {f["id"]: f.get("name") or f["id"] for f in fields if f.get("id")}

    def project(self, key: str) -> dict:
        return self._get(f"/rest/api/2/project/{urllib.parse.quote(key.strip())}")

    def versions(self, key: str) -> list[dict]:
        data = self._get(f"/rest/api/2/project/{urllib.parse.quote(key.strip())}/versions")
        return data if isinstance(data, list) else []

    def search(self, jql: str, fields: str = "*navigable", max_issues: int = 5000,
               on_progress: ProgressFn | None = None) -> list[dict]:
        progress = on_progress or (lambda fetched, total: None)
        try:
            return self._search_enhanced(jql, fields, max_issues, progress)
        except JiraError as e:
            if e.status not in _FALLBACK_CODES:
                raise
        except _NoIssues:
            pass
        return self._search_classic(jql, fields, max_issues, progress)

    def _search_enhanced(self, jql: str, fields: str, max_issues: int, progress: ProgressFn) -> list[dict]:
        issues: list[dict] = []
        token = None
        while len(issues) < max_issues:
            params = {"jql": jql, "fields": fields,
                      "maxResults": min(self.PAGE_SIZE, max_issues - len(issues))}
            if token:
                params["nextPageToken"] = token
            page  = self._get("/rest/api/latest/search/jql", params)
            if not isinstance(page, dict) or "issues" not in page:
                raise _NoIssues()           # like Amplify: anything without "issues" → classic search
            batch = page.get("issues") or []
            issues.extend(batch)
            progress(len(issues), None)
            token = page.get("nextPageToken")
            if not batch or not token or page.get("isLast"):
                break
        return issues[:max_issues]

    def _search_classic(self, jql: str, fields: str, max_issues: int, progress: ProgressFn) -> list[dict]:
        issues: list[dict] = []
        while len(issues) < max_issues:
            page = self._get("/rest/api/2/search", {
                "jql": jql, "fields": fields, "startAt": len(issues),
                "maxResults": min(self.PAGE_SIZE, max_issues - len(issues)),
            })
            batch = page.get("issues") or []
            issues.extend(batch)
            total = int(page.get("total", len(issues)))
            progress(len(issues), min(total, max_issues))
            if not batch or len(issues) >= total:
                break
        return issues[:max_issues]
