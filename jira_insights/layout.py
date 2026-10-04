"""
Dashboard layout — an ordered list of rows, each row a list of report ids.

Each row splits the page width evenly (one report = full width, four =
quarter tiles), so moving a report between rows is all it takes to resize
it. The layout is edited by drag-and-drop on the dashboard and saved in the
workspace settings; reports missing from a saved layout (e.g. newly saved
ones) are appended with the default packing.
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


def normalize(saved, reports: list[ReportSpec]) -> list[list[str]]:
    """Saved rows minus unknown/duplicate ids and empty rows, plus any reports not placed yet."""
    known = {r.id for r in reports}
    seen: set[str] = set()
    rows: list[list[str]] = []
    for row in saved if isinstance(saved, list) else []:
        if not isinstance(row, list):
            continue
        kept = [i for i in row if isinstance(i, str) and i in known and not (i in seen or seen.add(i))]
        if kept:
            rows.append(kept)
    return rows + default_layout([r for r in reports if r.id not in seen])


def visible_rows(rows: list[list[str]], visible: set[str]) -> list[list[str]]:
    return [kept for kept in ([i for i in row if i in visible] for row in rows) if kept]


def merge_hidden(edited: list[list[str]], previous: list[list[str]], visible: set[str]) -> list[list[str]]:
    """Keep rows of reports the current dataset can't show (they're hidden, not deleted)."""
    hidden = [kept for kept in ([i for i in row if i not in visible] for row in previous) if kept]
    return [row for row in edited if row] + hidden


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
