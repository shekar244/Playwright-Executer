"""
Reads and manages configuration for the Playwright Executor.
The config file (config.json) lives at the project root alongside the launch scripts.
"""

import json
import os
from pathlib import Path

# Defaults applied when config.json is missing or a key is absent.
_DEFAULTS: dict = {
    "repo_root": "",
    "browsers": ["chromium", "firefox", "webkit"],
    "default_browser": "chromium",
    "markers": [
        "smoke", "slow", "nbo", "nbc", "api", "web",
        "content", "data_driven", "trace", "skip_ci",
    ],
    "report_paths": [
        "allure/reports/P13n-Marketing-Experiences-QA-Automation-Report.html",
        "allure/reports/latest/index.html",
        "allure/reports/html/index.html",
    ],
    "default_workers": 1,
    "auto_open_report": False,
    "window_geometry": "1250x820",
}

# Location of config.json — always in the project root (parent of this package).
_CONFIG_FILE = Path(__file__).parent.parent / "config.json"


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

        if not config["repo_root"]:
            config["repo_root"] = self._detect_repo_root()

        return config

    def save(self, config: dict) -> None:
        with open(_CONFIG_FILE, "w", encoding="utf-8") as fh:
            json.dump(config, fh, indent=2)

    @staticmethod
    def _detect_repo_root() -> str:
        """Heuristic: look for the p13n framework alongside this project."""
        candidates = [
            Path(__file__).parent.parent.parent / "p13n-marketing-experiences-qa-automation",
            Path.home() / "Documents" / "GitHub" / "p13n-marketing-experiences-qa-automation",
            Path.home() / "projects" / "p13n-marketing-experiences-qa-automation",
        ]
        for path in candidates:
            if path.exists() and (path / "tests").exists():
                return str(path)
        return ""
