"""
Dashboard layout — an ordered list of rows, each row a list of report ids.

Each row splits the page width evenly (one report = full width, four =
quarter tiles), so moving a report between rows is all it takes to resize
it. Every dataset can have its own dashboard (which reports, in what layout);
a dataset without one shows every compatible report automatically, using the
shared default layout.
"""
from __future__ import annotations

from .pivot import ReportSpec

NUMBERS_PER_ROW = 4
CHARTS_PER_ROW  = 2


def default_layout(reports: list[ReportSpec]) -> list[list[str]]:
    """Report order: Number tiles four to a row, charts two to a row, gauge sets on their own row."""
    rows: list[list[str]] = []
    row: list[str] = []
    kind = None
    for report in reports:
        this = "number" if report.chart == "Number" else ("gauges" if report.chart in ("Gauge", "Meter") and report.rows
                                                          else "chart")
        cap = {"number": NUMBERS_PER_ROW, "gauges": 1}.get(this, CHARTS_PER_ROW)
        if row and (this != kind or len(row) >= cap):
            rows.append(row)
            row = []
        row.append(report.id)
        kind = this
    if row:
        rows.append(row)
    return rows


def clean(saved, reports: list[ReportSpec]) -> list[list[str]]:
    """Saved rows minus unknown / duplicate ids and empty rows (deleted reports simply drop out)."""
    known = {r.id for r in reports}
    seen: set[str] = set()
    rows: list[list[str]] = []
    for row in saved if isinstance(saved, list) else []:
        if not isinstance(row, list):
            continue
        kept = [i for i in row if isinstance(i, str) and i in known and not (i in seen or seen.add(i))]
        if kept:
            rows.append(kept)
    return rows


def normalize(saved, reports: list[ReportSpec]) -> list[list[str]]:
    """Automatic dashboards: the saved rows, plus every report not placed yet (default packing)."""
    rows = clean(saved, reports)
    placed = {i for row in rows for i in row}
    return rows + default_layout([r for r in reports if r.id not in placed])


def members(rows: list[list[str]]) -> list[str]:
    return [i for row in rows for i in row]


def with_members(rows: list[list[str]], picked: list[str], reports: list[ReportSpec]) -> list[list[str]]:
    """Keep the picked reports where they are, drop the rest, add new picks as new rows."""
    keep = set(picked)
    kept = [k for k in ([i for i in row if i in keep] for row in rows) if k]
    placed = set(members(kept))
    by_id = {r.id: r for r in reports}
    new = [by_id[i] for i in picked if i not in placed and i in by_id]
    return kept + default_layout(new)


# Columns only Zephyr test-run datasets have, and columns only Jira issue datasets have.
_ZEPHYR_COLUMNS = {"Result", "Executed", "Execution Status", "Execution ID", "Cycle", "Folder",
                   "Executed By", "Executed On", "Defect Keys", "Defects"}
_JIRA_COLUMNS = {"Issue Type", "Status", "Status Category", "Open/Closed", "Created", "Updated", "Resolved",
                 "Age (days)", "Resolution Time (days)", "Story Points", "Sprint", "Reporter", "Resolution",
                 "Epic Name", "Epic Key", "Link Type", "Linked Keys"}
KIND_BADGES = {"zephyr": "🧪", "jira": "🧾", "any": "◻️"}


def report_kind(spec: ReportSpec) -> str:
    """zephyr / jira / any — from the columns the report needs (Priority, Labels … exist in both)."""
    needs = spec.required_columns()
    if needs & _ZEPHYR_COLUMNS:
        return "zephyr"
    if needs & _JIRA_COLUMNS:
        return "jira"
    return "any"


def visible_rows(rows: list[list[str]], visible: set[str]) -> list[list[str]]:
    return [kept for kept in ([i for i in row if i in visible] for row in rows) if kept]



def unique_labels(reports: list[ReportSpec], icons: dict[str, str]) -> dict[str, str]:
    """report id → label for the drag-and-drop items (duplicates get a counter)."""
    labels: dict[str, str] = {}
    used: dict[str, int] = {}
    for r in reports:
        base = f"{icons.get(r.chart, '•')} {r.title}"
        used[base] = used.get(base, 0) + 1
        labels[r.id] = base if used[base] == 1 else f"{base} · {used[base]}"
    return labels


def to_containers(rows: list[list[str]], labels: dict[str, str], new_row_header: str) -> list[dict]:
    containers = [{"header": f"Row {i + 1}", "items": [labels[r] for r in row if r in labels]}
                  for i, row in enumerate(rows)]
    return containers + [{"header": new_row_header, "items": []}]


def from_containers(containers: list[dict], labels: dict[str, str]) -> list[list[str]]:
    by_label = {label: rid for rid, label in labels.items()}
    rows = [[by_label[item] for item in c.get("items", []) if item in by_label] for c in containers]
    return [row for row in rows if row]
