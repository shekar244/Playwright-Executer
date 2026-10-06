"""
Workspace persistence for Jira Insights (location: settings.workspace_dir()).

  datasets/<slug>.parquet   — pulled / uploaded issues
  datasets/<slug>.json      — metadata: name, JQL, source, fetched_at, multi-value columns
  specs/<slug>.json         — PyGWalker chart specs, saved from the Explorer toolbar
  reports.json              — saved pivot reports, shared by every dataset
  kpis.json                 — headline KPI tiles (editable from the dashboard)
  settings.json             — UI preferences (colour theme, per-dataset dashboards, tester names)

Slugs are sanitised and every path is checked to stay inside the workspace.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .kpi import EXECUTION_TILES, KpiSpec, Metric, default_tiles
from .pivot import ReportSpec

# Seeded into reports.json on first run so the dashboard is useful immediately.
# Reports whose columns are missing from a dataset are skipped, not broken.
STARTER_REPORTS = [
    {"name": "Open defects", "chart": "Number",
     "filters": {"Issue Type": ["Bug", "Defect"], "Open/Closed": ["Open"]}},
    {"name": "Open stories", "chart": "Number",
     "filters": {"Issue Type": ["Story"], "Open/Closed": ["Open"]}},
    {"name": "Defects by priority and status", "chart": "Stacked bar",
     "rows": "Priority", "series": "Status Category", "sort": "Label",
     "filters": {"Issue Type": ["Bug", "Defect"]}},
    {"name": "Stories by status", "chart": "Column", "rows": "Status", "show_labels": True,
     "filters": {"Issue Type": ["Story"]}},
    {"name": "Issues created per week", "chart": "Line", "rows": "Created",
     "series": "Issue Type", "date_grain": "Week"},
    {"name": "Average age of open defects (days)", "chart": "Bar", "rows": "Priority",
     "value": "Age (days)", "agg": "Average", "sort": "Label", "show_labels": True,
     "filters": {"Issue Type": ["Bug", "Defect"], "Open/Closed": ["Open"]}},
    {"name": "Epic progress", "chart": "Stacked bar", "rows": "Epic Name",
     "series": "Status Category", "sort": "Value"},
    {"name": "Story points by epic", "chart": "Bar", "rows": "Epic Name",
     "value": "Story Points", "agg": "Sum", "show_labels": True, "sort": "Value"},
    {"name": "Linked issues by type", "chart": "Bar", "rows": "Link Type",
     "show_labels": True, "sort": "Value"},
]


_DONE = {"Status Category": ["Done"]}
_NOT_DONE = {"Open/Closed": ["Open"]}

SPRINT_REPORTS = [
    {"name": "Sprint status breakdown", "chart": "Donut", "rows": "Status Category", "show_labels": True},
    {"name": "Stories by status", "chart": "Stacked bar", "rows": "Status", "series": "Issue Type",
     "sort": "Label"},
    {"name": "Story points by assignee", "chart": "Bar", "rows": "Assignee", "value": "Story Points",
     "agg": "Sum", "show_labels": True},
    {"name": "Sprint completion rate", "chart": "Meter", "gauge_where": _DONE,
     "gauge_bands": [60.0, 80.0, 90.0]},
    {"name": "Carry-over (open items)", "chart": "Column", "rows": "Issue Type",
     "filters": _NOT_DONE, "show_labels": True},
    {"name": "Issues by priority", "chart": "Column", "rows": "Priority", "series": "Status Category",
     "sort": "Label"},
    {"name": "Sprint velocity", "chart": "Column", "rows": "Sprint Name", "value": "Story Points",
     "agg": "Sum", "show_labels": True},
    {"name": "Burndown (story points remaining)", "chart": "Line", "rows": "Updated",
     "value": "Story Points", "agg": "Sum", "date_grain": "Day", "cumulative": False},
    {"name": "Created vs resolved per day", "chart": "Line", "rows": "Created", "series": "Open/Closed",
     "date_grain": "Day"},
    {"name": "Defects in sprint", "chart": "Number",
     "filters": {"Issue Type": {"contains": "bug|defect"}}},
    {"name": "Epic progress in sprint", "chart": "Stacked bar", "rows": "Epic Name",
     "series": "Status Category", "sort": "Value"},
    {"name": "Story points by epic", "chart": "Bar", "rows": "Epic Name",
     "value": "Story Points", "agg": "Sum", "show_labels": True, "sort": "Value"},
    {"name": "Linked issues by relationship", "chart": "Bar", "rows": "Link Type",
     "show_labels": True, "sort": "Value"},
]

_EXECUTED, _PASSED = {"Executed": ["Yes"]}, {"Result": ["Passed"]}

# Added once, when the first Zephyr test-run dataset is created. They use test-run columns
# (Result, Cycle, Executed On…), so they stay hidden on Jira issue datasets.
EXECUTION_REPORTS = [
    {"name": "Pass rate (executed tests)", "chart": "Meter", "filters": _EXECUTED, "gauge_where": _PASSED,
     "gauge_bands": [60.0, 80.0, 90.0]},
    {"name": "Pass rate by cycle", "chart": "Gauge", "rows": "Cycle", "filters": _EXECUTED,
     "gauge_where": _PASSED, "gauge_bands": [70.0, 90.0]},
    {"name": "Execution results", "chart": "Donut", "rows": "Result", "sort": "Label", "show_labels": True},
    {"name": "Results by cycle", "chart": "Stacked bar", "rows": "Cycle", "series": "Result"},
    {"name": "Test runs per day", "chart": "Stacked column", "rows": "Executed On", "series": "Result",
     "date_grain": "Day", "filters": _EXECUTED},
    {"name": "Failures by component", "chart": "Bar", "rows": "Components", "filters": {"Result": ["Failed"]},
     "show_labels": True},
    {"name": "Test runs by tester", "chart": "Stacked bar", "rows": "Executed By", "series": "Result",
     "filters": _EXECUTED},
]


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60]
    return slug or "dataset"


class Store:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        (self.root / "datasets").mkdir(parents=True, exist_ok=True)
        (self.root / "specs").mkdir(parents=True, exist_ok=True)

    def _path(self, folder: str, slug: str, ext: str) -> Path:
        path = (self.root / folder / f"{slugify(slug)}{ext}").resolve()
        if self.root not in path.parents:
            raise ValueError(f"Invalid dataset name: {slug!r}")
        return path

    # ── Datasets ──────────────────────────────────────────────────────────────

    def list_datasets(self) -> list[dict]:
        metas = []
        for meta_file in (self.root / "datasets").glob("*.json"):
            try:
                metas.append(json.loads(meta_file.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        return sorted(metas, key=lambda m: m.get("fetched_at", ""), reverse=True)

    def save_dataset(self, name: str, df: pd.DataFrame, *, source: str, jql: str = "",
                     multi_cols=(), extra: dict | None = None) -> dict:
        slug = slugify(name)
        df.to_parquet(self._path("datasets", slug, ".parquet"), index=False)
        meta = {
            "slug": slug, "name": name.strip() or slug, "source": source, "jql": jql,
            "rows": int(len(df)), "multi_value_cols": list(multi_cols),
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            **(extra or {}),
        }
        self._path("datasets", slug, ".json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        return meta

    def load_dataset(self, slug: str) -> tuple[pd.DataFrame, dict]:
        meta = json.loads(self._path("datasets", slug, ".json").read_text(encoding="utf-8"))
        return pd.read_parquet(self._path("datasets", slug, ".parquet")), meta

    def delete_dataset(self, slug: str) -> None:
        for folder, ext in (("datasets", ".parquet"), ("datasets", ".json"), ("specs", ".json")):
            self._path(folder, slug, ext).unlink(missing_ok=True)
        self.clear_dashboard(slugify(slug))

    def dataset_columns(self, slug: str) -> set[str]:
        """Column names without loading the data (used to check which reports fit a dataset)."""
        import pyarrow.parquet as pq
        try:
            return set(pq.read_schema(self._path("datasets", slug, ".parquet")).names)
        except (OSError, ValueError):
            return set()

    # ── Per-dataset dashboards (which reports, in what layout) ────────────────

    def get_dashboard(self, slug: str) -> list[list[str]] | None:
        """The dataset's own dashboard rows, or None when it shows every compatible report."""
        rows = (self.get_setting("dashboards", {}) or {}).get(slug)
        return rows if isinstance(rows, list) else None

    def set_dashboard(self, slug: str, rows: list[list[str]]) -> None:
        boards = dict(self.get_setting("dashboards", {}) or {})
        boards[slug] = [list(r) for r in rows if r]
        self.set_setting("dashboards", boards)

    def clear_dashboard(self, slug: str) -> None:
        boards = dict(self.get_setting("dashboards", {}) or {})
        if boards.pop(slug, None) is not None:
            self.set_setting("dashboards", boards)

    def add_to_dashboard(self, slug: str, report_id: str) -> None:
        """A report created while working on a dataset joins that dataset's own dashboard."""
        rows = self.get_dashboard(slug)
        if rows is not None and not any(report_id in row for row in rows):
            self.set_dashboard(slug, rows + [[report_id]])

    def spec_path(self, slug: str) -> Path:
        return self._path("specs", slug, ".json")

    # ── Reports ───────────────────────────────────────────────────────────────

    @property
    def _reports_file(self) -> Path:
        return self.root / "reports.json"

    def list_reports(self) -> list[ReportSpec]:
        if not self._reports_file.exists():
            self._write_reports([ReportSpec.from_dict(r).with_id() for r in STARTER_REPORTS])
        try:
            raw = json.loads(self._reports_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        reports = [ReportSpec.from_dict(r) for r in raw if isinstance(r, dict)]
        if self._number(reports):
            self._write_reports(reports)              # older reports get their numbers once
        return reports

    def _number(self, reports: list[ReportSpec]) -> bool:
        """Give every report a unique number; numbers are never reused, even after a delete."""
        seq = max([int(self.get_setting("report_seq") or 0)] + [int(r.number or 0) for r in reports])
        seen: set[int] = set()
        changed = False
        for report in reports:
            if not report.number or report.number in seen:
                seq += 1
                report.number = seq
                changed = True
            seen.add(report.number)
        if seq != self.get_setting("report_seq"):
            self.set_setting("report_seq", seq)
        return changed

    def save_report(self, spec: ReportSpec) -> ReportSpec:
        spec.with_id()
        reports = self.list_reports()
        ids = [r.id for r in reports]
        if spec.id in ids:
            spec.number = reports[ids.index(spec.id)].number      # an edit keeps its number
            reports[ids.index(spec.id)] = spec
        else:
            spec.number = 0                                        # new / copied report → next number
            reports.append(spec)
        self._number(reports)
        self._write_reports(reports)
        return spec

    def delete_report(self, report_id: str) -> None:
        self._write_reports([r for r in self.list_reports() if r.id != report_id])

    def _write_reports(self, reports: list[ReportSpec]) -> None:
        self._reports_file.write_text(json.dumps([r.to_dict() for r in reports], indent=2), encoding="utf-8")

    # ── KPI tiles ─────────────────────────────────────────────────────────────

    @property
    def _kpis_file(self) -> Path:
        return self.root / "kpis.json"

    def list_kpis(self) -> list[KpiSpec]:
        if not self._kpis_file.exists():
            self.save_kpis(default_tiles())
        try:
            raw = json.loads(self._kpis_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default_tiles()
        return [KpiSpec.from_dict(t) for t in raw if isinstance(t, dict)]

    def save_kpis(self, tiles: list[KpiSpec]) -> None:
        self._kpis_file.write_text(json.dumps([t.with_id().to_dict() for t in tiles], indent=2), encoding="utf-8")

    def reset_kpis(self) -> None:
        self.save_kpis(default_tiles())

    def seed_execution_starters(self) -> bool:
        """Add the test-run reports and KPI tiles once (by name — user edits and deletions stick)."""
        if self.get_setting("execution_starters_seeded"):
            return False
        names = {r.name for r in self.list_reports()}
        for report in EXECUTION_REPORTS:
            if report["name"] not in names:
                self.save_report(ReportSpec.from_dict(report))
        tiles = self.list_kpis()
        for tile in tiles:      # the issue-count tile predates `requires`; keep it on issue datasets only
            if tile.label == "Issues" and not tile.requires and not tile.metric.value and not tile.metric.filters:
                tile.requires = ["Open/Closed"]
        labels = {t.label for t in tiles}
        tiles += [KpiSpec.from_dict(t.to_dict()) for t in EXECUTION_TILES if t.label not in labels]
        self.save_kpis(tiles)
        self.set_setting("execution_starters_seeded", True)
        return True

    def seed_sprint_starters(self) -> bool:
        """Add sprint reports and KPI tiles once (by name — user edits and deletions stick)."""
        if self.get_setting("sprint_starters_seeded"):
            return False
        names = {r.name for r in self.list_reports()}
        for report in SPRINT_REPORTS:
            if report["name"] not in names:
                self.save_report(ReportSpec.from_dict(report))
        tiles = self.list_kpis()
        labels = {t.label for t in tiles}
        sprint_tiles = [
            KpiSpec("Sprint Items", "🏃", "#7ea8ff", Metric(), caption="dataset", requires=["Sprint Name"]),
            KpiSpec("Completed", "✅", "#5fd4a0", Metric(filters={"Status Category": ["Done"]}),
                    requires=["Sprint Name"]),
            KpiSpec("In Progress", "🔄", "#f0d080",
                    Metric(filters={"Status Category": ["In Progress"]}), requires=["Sprint Name"]),
            KpiSpec("To Do", "📋", "#8e98bc", Metric(filters={"Status Category": ["To Do"]}),
                    requires=["Sprint Name"]),
            KpiSpec("Completion %", "🎯", "#7ecdc0",
                    Metric(filters={"Status Category": ["Done"]}), divide_by=Metric(),
                    caption="text", caption_text="of sprint items", as_percent=True,
                    requires=["Sprint Name"]),
            KpiSpec("Sprint Points", "⭐", "#b898f5", Metric("Story Points", "Sum"),
                    caption="metric", caption_text="completed",
                    caption_metric=Metric("Story Points", "Sum", {"Status Category": ["Done"]}),
                    requires=["Sprint Name"]),
        ]
        tiles += [KpiSpec.from_dict(t.to_dict()) for t in sprint_tiles if t.label not in labels]
        self.save_kpis(tiles)
        self.set_setting("sprint_starters_seeded", True)
        return True

    # ── UI settings ───────────────────────────────────────────────────────────

    @property
    def _settings_file(self) -> Path:
        return self.root / "settings.json"

    def _settings(self) -> dict:
        try:
            data = json.loads(self._settings_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def get_setting(self, key: str, default=None):
        return self._settings().get(key, default)

    def set_setting(self, key: str, value) -> None:
        settings = self._settings()
        settings[key] = value
        self._settings_file.write_text(json.dumps(settings, indent=2), encoding="utf-8")
