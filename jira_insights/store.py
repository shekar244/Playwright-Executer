"""
Workspace persistence for Jira Insights (location: settings.workspace_dir()).

  datasets/<slug>.parquet   — pulled / uploaded issues
  datasets/<slug>.json      — metadata: name, JQL, source, fetched_at, multi-value columns
  specs/<slug>.json         — PyGWalker chart specs, saved from the Explorer toolbar
  reports.json              — saved pivot reports, shared by every dataset
  settings.json             — UI preferences (colour theme)

Slugs are sanitised and every path is checked to stay inside the workspace.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

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
        return [ReportSpec.from_dict(r) for r in raw if isinstance(r, dict)]

    def save_report(self, spec: ReportSpec) -> ReportSpec:
        spec.with_id()
        reports = self.list_reports()
        ids = [r.id for r in reports]
        if spec.id in ids:
            reports[ids.index(spec.id)] = spec
        else:
            reports.append(spec)
        self._write_reports(reports)
        return spec

    def delete_report(self, report_id: str) -> None:
        self._write_reports([r for r in self.list_reports() if r.id != report_id])

    def _write_reports(self, reports: list[ReportSpec]) -> None:
        self._reports_file.write_text(json.dumps([r.to_dict() for r in reports], indent=2), encoding="utf-8")

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
