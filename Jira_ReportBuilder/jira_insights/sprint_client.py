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
