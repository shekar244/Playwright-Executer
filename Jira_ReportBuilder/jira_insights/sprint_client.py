"""
Sprint-aware Jira client — fetch boards, sprints, and sprint-scoped issues.

Extends JiraClient with Jira Agile REST API calls:
  GET /rest/agile/1.0/board                          — boards by project
  GET /rest/agile/1.0/board/{id}/sprint              — sprints on a board
  GET /rest/agile/1.0/sprint/{id}                    — single sprint metadata
  GET /rest/agile/1.0/board/{id}/reports/velocity    — velocity report

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
            if isinstance(data, list):
                boards.extend(d for d in data if isinstance(d, dict))
                break
            if not isinstance(data, dict):
                break
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
            if isinstance(data, list):
                sprints.extend(d for d in data if isinstance(d, dict))
                break
            if not isinstance(data, dict):
                break
            batch = data.get("values") or []
            sprints.extend(batch)
            if data.get("isLast", True) or not batch:
                break
            start += len(batch)
        return sprints

    def sprint(self, sprint_id: int) -> dict:
        data = self._client._get(f"/rest/agile/1.0/sprint/{int(sprint_id)}")
        return data if isinstance(data, dict) else {}

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

    def velocity_report(self, board_id: int) -> list[dict]:
        """Velocity data from Jira's built-in velocity report.

        Uses GET /rest/agile/1.0/board/{boardId}/reports/velocity (Jira Cloud)
        or  GET /rest/greenhopper/1.0/rapid/charts/velocity (Server/DC).

        Returns [{name, id, committed, completed, startDate, endDate, state}]
        in chronological order.
        """
        try:
            data = self._client._get(f"/rest/agile/1.0/board/{int(board_id)}/reports/velocity")
        except JiraError as e:
            if e.status in (404, 405):
                data = self._client._get("/rest/greenhopper/1.0/rapid/charts/velocity",
                                         {"rapidViewId": str(board_id)})
            else:
                raise

        if not isinstance(data, dict):
            return []

        raw_sprints = data.get("sprints") or {}
        raw_stats = data.get("velocityStatEntries") or {}

        if isinstance(raw_sprints, list):
            sprints_data = {str(s.get("id", "")): s for s in raw_sprints if isinstance(s, dict)}
        elif isinstance(raw_sprints, dict):
            sprints_data = raw_sprints
        else:
            sprints_data = {}

        if isinstance(raw_stats, list):
            velocity_stats = {}
            for entry in raw_stats:
                if isinstance(entry, dict):
                    sid = str(entry.get("sprintId", entry.get("id", "")))
                    if sid:
                        velocity_stats[sid] = entry
        elif isinstance(raw_stats, dict):
            velocity_stats = raw_stats
        else:
            velocity_stats = {}

        result = []
        for sprint_id_str, stats in velocity_stats.items():
            try:
                sprint_id = int(sprint_id_str)
            except (ValueError, TypeError):
                continue
            sprint_info = sprints_data.get(sprint_id_str) or sprints_data.get(str(sprint_id), {})

            estimated = stats.get("estimated") or stats.get("committedEstimate") or {}
            completed_stat = stats.get("completed") or stats.get("completedEstimate") or {}

            committed_val = estimated.get("value", estimated) if isinstance(estimated, dict) else estimated
            completed_val = completed_stat.get("value", completed_stat) if isinstance(completed_stat, dict) else completed_stat

            try:
                committed_f = float(committed_val or 0)
                completed_f = float(completed_val or 0)
            except (TypeError, ValueError):
                committed_f, completed_f = 0.0, 0.0

            result.append({
                "name": sprint_info.get("name", f"Sprint {sprint_id}") if isinstance(sprint_info, dict) else f"Sprint {sprint_id}",
                "id": sprint_id,
                "committed": committed_f,
                "completed": completed_f,
                "startDate": sprint_info.get("startDate", "") if isinstance(sprint_info, dict) else "",
                "endDate": (sprint_info.get("endDate") or sprint_info.get("completeDate", "")) if isinstance(sprint_info, dict) else "",
                "state": sprint_info.get("state", "closed") if isinstance(sprint_info, dict) else "closed",
            })

        result.sort(key=lambda s: s.get("startDate") or str(s["id"]))
        return result
