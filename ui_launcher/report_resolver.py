"""
Locates the latest generated test report in the framework's output folder.
Read-only — never modifies any framework files.
Works with any repo layout; no hardcoded paths or project names.
"""

import os
from pathlib import Path
from typing import List, Optional

# Common report output directories to scan when configured_paths come up empty.
_FALLBACK_SCAN_DIRS = [
    "allure/reports",
    "allure-results",
    "reports",
    "test-results",
    "htmlcov",
    "output/reports",
]


class ReportResolver:
    """Find the most recently modified HTML report artefact."""

    def __init__(self, repo_root: str, configured_paths: List[str]):
        self.repo_root = Path(repo_root)
        self.configured_paths = configured_paths

    # ── Public API ────────────────────────────────────────────────────────────

    def find_latest(self) -> Optional[str]:
        """
        1. Check explicitly configured paths (relative to repo root).
        2. Scan common report directories for any *.html file.
        Returns the absolute path of the most recently modified file, or None.
        """
        candidates: list[tuple[float, str]] = []

        for rel in self.configured_paths:
            full = self.repo_root / rel
            if full.exists() and full.is_file():
                candidates.append((full.stat().st_mtime, str(full)))

        for rel_dir in _FALLBACK_SCAN_DIRS:
            scan_dir = self.repo_root / rel_dir
            if scan_dir.exists():
                for item in scan_dir.rglob("*.html"):
                    candidates.append((item.stat().st_mtime, str(item)))

        if not candidates:
            return None
        candidates.sort(reverse=True)
        return candidates[0][1]

    def find_latest_in_dir(self, rel_dir: str) -> Optional[str]:
        """
        Find the Allure 3 single-file report (index.html) in rel_dir,
        falling back to any *.html file by mtime.
        """
        if not rel_dir:
            return None

        scan_dir = self.repo_root / rel_dir
        if not scan_dir.exists():
            return None

        # Allure 3 --single-file always writes index.html at the root
        allure3 = scan_dir / "index.html"
        if allure3.exists():
            return str(allure3)

        # Fallback: newest HTML file anywhere under the directory
        candidates: list[tuple[float, str]] = []
        for item in scan_dir.rglob("*.html"):
            try:
                candidates.append((item.stat().st_mtime, str(item)))
            except OSError:
                pass

        if not candidates:
            return None

        candidates.sort(reverse=True)
        return candidates[0][1]
