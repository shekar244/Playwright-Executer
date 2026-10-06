"""
Sprint-aware Jira client — fetch boards, sprints, and sprint-scoped issues.

Extends JiraClient with Jira Agile REST API calls:
  GET /rest/agile/1.0/board          — boards by project
  GET /rest/agile/1.0/board/{id}/sprint — sprints on a board
  GET /rest/agile/1.0/sprint/{id}    — single sprint metadata
  GET /rest/agile/1.0/board/{id}/sprint?state=active — active sprints

Sprint issues are pulled with the regular JQL search (sprint = {id}),
reusing JiraClient.search() for pagination and retry.
"""
from __future__ import annotations

from .jira_client import JiraClient, JiraError


class SprintClient:
    """Thin wrapper around JiraClient for sprint-specific endpoints."""

    def __init__(self, client: JiraClient):
        self._client = client

    def boards(self, project_key: str) -> list[dict]:
        """Scrum/Kanban boards for a project (type, id, name)."""
        boards: list[dict] = []
        start = 0
        while True:
            data = self._client._get("/rest/agile/1.0/board", {
                "projectKeyOrId": project_key, "startAt": start, "maxResults": 50,
            })
            batch = data.get("values") or []
            boards.extend(batch)
            if data.get("isLast", True) or not batch:
                break
            start += len(batch)
        return boards

    def sprints(self, board_id: int, state: str = "") -> list[dict]:
        """Sprints on a board. state: active | closed | future | '' (all)."""
        sprints: list[dict] = []
        start = 0
        params: dict = {"startAt": start, "maxResults": 50}
        if state:
            params["state"] = state
        while True:
            params["startAt"] = start
            data = self._client._get(f"/rest/agile/1.0/board/{int(board_id)}/sprint", params)
            batch = data.get("values") or []
            sprints.extend(batch)
            if data.get("isLast", True) or not batch:
                break
            start += len(batch)
        return sprints

    def sprint(self, sprint_id: int) -> dict:
        return self._client._get(f"/rest/agile/1.0/sprint/{int(sprint_id)}")

    def active_sprints_across_boards(self, project_keys: list[str]) -> list[dict]:
        """Active sprints across multiple projects (de-duplicated by sprint id)."""
        seen: set[int] = set()
        result: list[dict] = []
        for key in project_keys:
            try:
                boards = self.boards(key)
            except JiraError:
                continue
            for board in boards:
                try:
                    for sp in self.sprints(board["id"], state="active"):
                        sid = sp["id"]
                        if sid not in seen:
                            seen.add(sid)
                            sp["_project"] = key
                            sp["_board"] = board.get("name", "")
                            result.append(sp)
                except JiraError:
                    continue
        return result

    def sprint_issues(self, sprint_name: str, project_key: str = "",
                      fields: str = "*navigable", max_issues: int = 5000,
                      on_progress=None) -> list[dict]:
        """Issues in a sprint via JQL."""
        jql = f'sprint = "{sprint_name}"'
        if project_key:
            jql = f'project = "{project_key}" AND {jql}'
        jql += " ORDER BY rank ASC"
        return self._client.search(jql, fields=fields, max_issues=max_issues, on_progress=on_progress)

    def sprint_issues_by_id(self, sprint_id: int, project_key: str = "",
                            fields: str = "*navigable", max_issues: int = 5000,
                            on_progress=None) -> list[dict]:
        """Issues in a sprint via JQL using sprint id."""
        jql = f"sprint = {int(sprint_id)}"
        if project_key:
            jql = f'project = "{project_key}" AND {jql}'
        jql += " ORDER BY rank ASC"
        return self._client.search(jql, fields=fields, max_issues=max_issues, on_progress=on_progress)

    def multi_sprint_issues(self, sprint_ids: list[int], project_key: str = "",
                            fields: str = "*navigable", max_issues: int = 10000,
                            on_progress=None) -> list[dict]:
        """Issues across multiple sprints in one query."""
        id_list = ", ".join(str(int(s)) for s in sprint_ids)
        jql = f"sprint in ({id_list})"
        if project_key:
            jql = f'project = "{project_key}" AND {jql}'
        jql += " ORDER BY sprint ASC, rank ASC"
        return self._client.search(jql, fields=fields, max_issues=max_issues, on_progress=on_progress)

    def previous_closed_sprints(self, board_id: int, current_sprint_id: int,
                                count: int = 5) -> list[dict]:
        """The last N closed sprints before the current one, most recent first."""
        closed = self.sprints(board_id, state="closed")
        closed.sort(key=lambda s: s.get("endDate") or "", reverse=True)
        return [s for s in closed if s["id"] != current_sprint_id][:count]

    def velocity_history(self, board_id: int, current_sprint_id: int,
                         project_key: str = "", history_count: int = 5,
                         on_progress=None) -> list[dict]:
        """Velocity for the last N closed sprints: [{name, id, committed, completed, startDate, endDate}].

        Fetches all past sprint issues in a single JQL query, groups by sprint,
        and computes committed (total story points) vs completed (done points).
        """
        past = self.previous_closed_sprints(board_id, current_sprint_id, history_count)
        if not past:
            return []

        all_ids = [s["id"] for s in past]
        issues = self.multi_sprint_issues(all_ids, project_key=project_key,
                                          fields="sprint,status,customfield_10016,story_points",
                                          max_issues=20000, on_progress=on_progress)

        from .sprint_transform import _assign_sprint_id
        per_sprint: dict[int, dict] = {s["id"]: {"committed": 0.0, "completed": 0.0} for s in past}
        for issue in issues:
            fields = issue.get("fields") or {}
            pts = _safe_points(fields)
            sid = _assign_sprint_id(fields, set(per_sprint))
            if sid is None:
                continue
            per_sprint[sid]["committed"] += pts
            status_cat = ((fields.get("status") or {}).get("statusCategory") or {}).get("name", "")
            if status_cat.lower() == "done":
                per_sprint[sid]["completed"] += pts

        result = []
        for sprint in reversed(past):  # chronological order
            sid = sprint["id"]
            result.append({
                "name": sprint.get("name", ""),
                "id": sid,
                "committed": round(per_sprint[sid]["committed"], 1),
                "completed": round(per_sprint[sid]["completed"], 1),
                "startDate": sprint.get("startDate", ""),
                "endDate": sprint.get("endDate", ""),
            })
        return result


def _safe_points(fields: dict) -> float:
    """Extract story points from any of the common field locations."""
    for key in ("story_points", "customfield_10016", "customfield_10028",
                "customfield_10014"):
        val = fields.get(key)
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                continue
    return 0.0
