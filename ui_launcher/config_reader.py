"""
Reads and manages configuration for the Playwright Executor.
The config file (config.json) lives at the project root alongside the launch scripts.
"""

import json
import os
from pathlib import Path

# Minimal defaults — no hardcoded project names, paths, or markers.
# Markers are discovered at runtime from the selected repo's conftest.py.
# Report paths use only generic allure output locations.
_DEFAULTS: dict = {
    "repo_root": "",
    "browsers": ["chromium", "firefox", "webkit"],
    "default_browser": "chromium",
    "markers": [],
    "report_paths": [
        "allure/reports/latest/index.html",
        "allure/reports/html/index.html",
    ],
    "default_workers": 1,
    "auto_open_report": False,
    "report_individual_dir": "allure/reports",
    "report_consolidated_dir": "allure/reports/history",
    # Each entry: {"label", "flag", "type": "dropdown"|"checkbox",
    #              "values": [...],   # dropdown only
    #              "default": ""|false}
    "extra_options": [],
}

# Location of config.json — always in the project root (parent of this package).
_CONFIG_FILE = Path(__file__).parent.parent / "config.json"

# Directories to scan when auto-detecting a repo (no specific names assumed).
_SCAN_DIRS = [
    Path(__file__).parent.parent.parent,   # sibling of Playwright-Executer
    Path.home() / "Documents" / "GitHub",
    Path.home() / "GitHub",
    Path.home() / "Projects",
    Path.home() / "Desktop",
]


class ConfigReader:
    """Load, merge, and persist the executor configuration."""

    def load(self) -> dict:
        config = _DEFAULTS.copy()
        if _CONFIG_FILE.exists():
            try:
                with open(_CONFIG_FILE, "r", encoding="utf-8") as fh:
                    user = json.load(fh)
                config.update(user)
            except (json.JSONDecodeError, OSError):
                pass

        if not config.get("repo_root"):
            config["repo_root"] = self._detect_repo_root()

        return config

    def save(self, config: dict) -> None:
        with open(_CONFIG_FILE, "w", encoding="utf-8") as fh:
            json.dump(config, fh, indent=2)

    @staticmethod
    def _looks_like_playwright_repo(path: Path) -> bool:
        """Generic heuristic: has tests/ dir and at least one pytest config file."""
        if not (path / "tests").is_dir():
            return False
        config_markers = [
            path / "pytest.ini",
            path / "pyproject.toml",
            path / "setup.cfg",
            path / "requirements.txt",
            path / "config" / "pytest.ini",
        ]
        return any(m.exists() for m in config_markers)

    @classmethod
    def _detect_repo_root(cls) -> str:
        """
        Scan common locations for a Playwright/pytest repo.
        Returns the first match found; empty string if nothing is found.
        """
        seen: set[str] = set()
        for scan_dir in _SCAN_DIRS:
            if not scan_dir.is_dir():
                continue
            try:
                for item in sorted(scan_dir.iterdir()):
                    key = str(item.resolve())
                    if item.is_dir() and key not in seen and cls._looks_like_playwright_repo(item):
                        seen.add(key)
                        return str(item)
            except PermissionError:
                continue
        return ""
