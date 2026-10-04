"""
Zephyr test runs for Jira Insights — a thin adapter over Amplify QEA's own
Test Management code (routes/zephyr.py).

Every request goes through _z_call (Zephyr Squad Cloud, JWT-signed with the
access key / secret key / account id from Config → Zephyr) or _jira_call (Jira
REST with the same config), so authentication is exactly the Test Management tab's:

  projects / versions  _jira_call  GET /project/{key}, /project/{key}/versions
  cycles               _z_call     GET  /public/rest/api/1.0/cycles/search
  executions           _z_call     GET  /public/rest/api/1.0/executions/search/cycle/{id}  (size / offset)
  ZQL                  _z_call     POST /public/rest/api/1.0/zql/search
  tester names         _jira_call  GET /user?accountId=… (Cloud) · ?key=… / ?username=… (Server / DC)
"""
from __future__ import annotations

from typing import Callable, Optional

from routes.zephyr import _jira_call, _z_call, _z_cfg

from .jira_client import JiraError

PAGE_SIZE = 50
ProgressFn = Callable[[int, Optional[int]], None]


def is_configured() -> bool:
    cfg = _z_cfg()
    return all(str(cfg.get(k, "")).strip() for k in ("jira_url", "access_key", "secret_key", "account_id"))


def default_project_key() -> str:
    return str(_z_cfg().get("project_key", "")).strip().upper()


def _error_text(data) -> str:
    if isinstance(data, dict):
        err = data.get("error", data)
        if isinstance(err, dict):
            parts = list(err.get("errorMessages") or []) + [str(err.get("message") or "")]
            return "; ".join(p for p in parts if p) or str(err)
        return str(err)
    return str(data)


def _ok(result: tuple):
    data, code = result
    if code != 200 or (isinstance(data, dict) and data.get("error")):
        raise JiraError(code if code != 200 else 0, _error_text(data))
    return data


def normalize_cycles(data) -> list[dict]:
    """Cycle lists come back as a list, or as {cycleId: {...}, "recordsCount": n} (Test Management's handling)."""
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
    return rows, int(total) if str(total).isdigit() else None


class AmplifyZephyr:
    """Same interface as the standalone Jira Report Builder's Zephyr clients."""
    mode = "cloud"

    def test(self) -> str:
        statuses = _ok(_z_call("GET", "/public/rest/api/1.0/util/statuses"))
        return f"Zephyr authenticated · {len(statuses) if isinstance(statuses, (dict, list)) else 0} execution statuses"

    def display_names(self, user_ids) -> dict[str, str]:
        """Best effort: user id → display name via Jira; ids Jira can't resolve are left out."""
        cloud = ".atlassian.net" in str(_z_cfg().get("jira_url", "")).lower()
        names: dict[str, str] = {}
        for uid in dict.fromkeys(str(u).strip() for u in user_ids if str(u).strip()):
            for params in ([{"accountId": uid}] if cloud else [{"key": uid}, {"username": uid}]):
                data, code = _jira_call("GET", "/user", params)
                if code == 200 and isinstance(data, dict) and data.get("displayName"):
                    names[uid] = data["displayName"]
                    break
        return names

    def project(self, key: str) -> dict:
        return _ok(_jira_call("GET", f"/project/{key.strip()}"))

    def versions(self, key: str) -> list[dict]:
        data = _ok(_jira_call("GET", f"/project/{key.strip()}/versions"))
        return data if isinstance(data, list) else []

    def cycles(self, project_id: str, version_id: str) -> list[dict]:
        return normalize_cycles(_ok(_z_call("GET", "/public/rest/api/1.0/cycles/search",
                                            {"projectId": project_id, "versionId": version_id})))

    def cycle_executions(self, project: dict, version: dict, cycle: dict,
                         on_progress: ProgressFn | None = None, max_records: int = 20000) -> list[dict]:
        """Same paging as _z_get_all_executions, but a failed page raises instead of returning a partial list."""
        path = f"/public/rest/api/1.0/executions/search/cycle/{cycle['id']}"
        rows: list[dict] = []
        while len(rows) < max_records:
            params = {"projectId": project["id"], "versionId": version["id"],
                      "size": str(PAGE_SIZE), "offset": str(len(rows))}
            page, total = _page(_ok(_z_call("GET", path, params)))
            rows.extend(page)
            if on_progress:
                on_progress(len(rows), total)
            if len(page) < PAGE_SIZE or (total is not None and len(rows) >= total):
                break
        return rows[:max_records]

    def zql(self, query: str, max_records: int = 5000, on_progress: ProgressFn | None = None) -> list[dict]:
        rows: list[dict] = []
        while len(rows) < max_records:
            body = {"zqlQuery": query, "offset": len(rows), "maxRecords": min(PAGE_SIZE, max_records - len(rows))}
            page, total = _page(_ok(_z_call("POST", "/public/rest/api/1.0/zql/search", body=body)))
            rows.extend(page)
            if on_progress:
                on_progress(len(rows), total)
            if not page or (total is not None and len(rows) >= total):
                break
        return rows[:max_records]


def zephyr_client(settings=None) -> AmplifyZephyr:
    return AmplifyZephyr()
