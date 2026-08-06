"""
Locates the latest generated test report in the framework's output folder.
Read-only — never modifies any framework files.
"""

import os
from pathlib import Path
from typing import List, Optional


class ReportResolver:
    """Find the most recently modified report artefact."""

    def __init__(self, repo_root: str, configured_paths: List[str]):
        self.repo_root = Path(repo_root)
        self.configured_paths = configured_paths  # Relative paths from config.json

    # ── Public API ────────────────────────────────────────────────────────────

    def find_latest(self) -> Optional[str]:
        """
        Search configured report paths first, then scan allure/reports/ for any
        HTML file. Return the absolute path of the most recently modified file,
        or None if nothing is found.
        """
        candidates: List[tuple[float, str]] = []

        # Configured paths from config.json
        for rel in self.configured_paths:
            full = self.repo_root / rel
            if full.exists() and full.is_file():
                candidates.append((full.stat().st_mtime, str(full)))

        # Broad scan of allure/reports/
        reports_dir = self.repo_root / "allure" / "reports"
        if reports_dir.exists():
            for item in reports_dir.rglob("*.html"):
                candidates.append((item.stat().st_mtime, str(item)))

        if not candidates:
            return None

        candidates.sort(reverse=True)
        return candidates[0][1]

    def status_message(self) -> str:
        """Return a human-readable string about the report state."""
        path = self.find_latest()
        if path is None:
            return "No report found in output folder"
        rel = os.path.relpath(path, self.repo_root)
        return f"Report found: {rel}"
