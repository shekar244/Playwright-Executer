"""
Discovers test files and markers from the target pytest framework.
Does NOT modify any framework files — read-only operations only.
"""

import os
import re
from pathlib import Path
from typing import Dict, List


class TestDiscovery:
    """Scans the framework's tests/ directory and returns a suite → file mapping."""

    def __init__(self, repo_root: str):
        self.repo_root = Path(repo_root)
        self.tests_dir = self.repo_root / "tests"

    # ── Public API ────────────────────────────────────────────────────────────

    def discover(self) -> Dict[str, List[str]]:
        """
        Return {suite_rel_path: [absolute_test_file_paths, ...]}.
        Suite names are paths relative to the repo root (e.g. "tests/api").
        Files are sorted within each suite.
        """
        if not self.tests_dir.exists():
            raise FileNotFoundError(
                f"tests/ directory not found under {self.repo_root}. "
                "Check that the repo root is correct."
            )

        suites: Dict[str, List[str]] = {}

        for root, dirs, files in os.walk(self.tests_dir):
            dirs[:] = sorted(
                d for d in dirs
                if not d.startswith("_") and not d.startswith(".")
            )
            test_files = sorted(f for f in files if f.startswith("test_") and f.endswith(".py"))
            if not test_files:
                continue

            rel_dir = os.path.relpath(root, self.repo_root).replace("\\", "/")
            suites[rel_dir] = [
                os.path.join(root, f).replace("\\", "/")
                for f in test_files
            ]

        return dict(sorted(suites.items()))

    def discover_markers(self) -> List[str]:
        """
        Parse custom markers registered via addinivalue_line in conftest.py.
        Falls back to an empty list if the file cannot be read.
        """
        conftest = self.tests_dir / "conftest.py"
        markers: List[str] = []
        if not conftest.exists():
            return markers

        try:
            with open(conftest, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    m = re.search(r'addinivalue_line\s*\(\s*"markers"\s*,\s*"([^":]+)', line)
                    if m:
                        marker_name = m.group(1).strip()
                        if marker_name and marker_name not in markers:
                            markers.append(marker_name)
        except OSError:
            pass

        return markers

    def discover_env_files(self) -> List[str]:
        """
        Find .env* files in the repo root that could represent environment presets.
        Returns a list of file paths relative to the repo root.
        """
        env_files = []
        for item in sorted(self.repo_root.iterdir()):
            name = item.name
            if item.is_file() and (name == ".env" or name.startswith(".env.")):
                env_files.append(str(item))
        return env_files
