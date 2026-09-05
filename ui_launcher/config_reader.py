"""
Reads and manages configuration for the Playwright Executor.

Config resolution chain (first match wins):
  1. config_override_path  — any file the user explicitly pointed to
  2. {repo_root}/config.json — repo-level config
  3. {tool_root}/config.json — tool-level bootstrap config (always read first
                                 to discover repo_root / config_override_path)

report_history.json is stored per-repo at {repo_root}/report_history.json
so each project keeps its own run history.
"""

import json
from pathlib import Path

# Minimal defaults — no hardcoded project names, paths, or markers.
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
    "allure_results_dir": "allure/results",
    "report_individual_dir": "allure/reports",
    "report_consolidated_dir": "allure/reports/history",
    "extra_options": [],
    "config_override_path": "",
    "venv_path": "",          # relative to repo root (e.g. ".venv", "venv") or absolute
    "pinned_repos": [],       # list of pinned repo paths for quick-access
}

# Tool-level bootstrap config — always exists in the tool directory.
TOOL_CONFIG_FILE = Path(__file__).parent.parent / "config.json"

_SCAN_DIRS = [
    Path(__file__).parent.parent.parent,
    Path.home() / "Documents" / "GitHub",
    Path.home() / "GitHub",
    Path.home() / "Projects",
    Path.home() / "Desktop",
]


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


class ConfigReader:
    """Load, merge, and persist the executor configuration."""

    def load(self) -> dict:
        config = _DEFAULTS.copy()

        # Step 1: Always load the tool-level bootstrap config to get
        # repo_root and config_override_path before anything else.
        tool_cfg = _read_json(TOOL_CONFIG_FILE)
        config.update(tool_cfg)

        # Step 2: Resolve repo_root (detect if not set).
        if not config.get("repo_root"):
            config["repo_root"] = self._detect_repo_root()

        # Step 3: Check for an explicit override path.
        override = config.get("config_override_path", "").strip()
        if override:
            override_path = Path(override)
            if override_path.exists():
                config.update(_read_json(override_path))
                config["_active_config_path"]   = str(override_path)
                config["_active_config_source"]  = "override"
                config["_tool_config_path"]      = str(TOOL_CONFIG_FILE)
                return config

        # Step 4: Try repo-level config.
        repo = config.get("repo_root", "")
        if repo:
            repo_cfg_path = Path(repo) / "config.json"
            if repo_cfg_path.exists():
                config.update(_read_json(repo_cfg_path))
                config["_active_config_path"]   = str(repo_cfg_path)
                config["_active_config_source"]  = "repo"
                config["_tool_config_path"]      = str(TOOL_CONFIG_FILE)
                return config

        # Step 5: Fall back to tool-level config (already applied above).
        config["_active_config_path"]   = str(TOOL_CONFIG_FILE)
        config["_active_config_source"]  = "tool"
        config["_tool_config_path"]      = str(TOOL_CONFIG_FILE)
        return config

    def save(self, config: dict) -> None:
        """Save to the active config path, stripping internal _ keys."""
        active = config.get("_active_config_path", "")
        save_to = Path(active) if active else TOOL_CONFIG_FILE
        to_save = {k: v for k, v in config.items() if not k.startswith("_")}
        save_to.write_text(json.dumps(to_save, indent=2), encoding="utf-8")

    def save_tool_config(self, updates: dict) -> None:
        """Write specific keys to the tool-level bootstrap config only."""
        tool_cfg = _read_json(TOOL_CONFIG_FILE)
        tool_cfg.update({k: v for k, v in updates.items() if not k.startswith("_")})
        TOOL_CONFIG_FILE.write_text(json.dumps(tool_cfg, indent=2), encoding="utf-8")

    @staticmethod
    def _looks_like_playwright_repo(path: Path) -> bool:
        if not (path / "tests").is_dir():
            return False
        markers = [
            path / "pytest.ini", path / "pyproject.toml",
            path / "setup.cfg",  path / "requirements.txt",
            path / "config" / "pytest.ini",
        ]
        return any(m.exists() for m in markers)

    @classmethod
    def _detect_repo_root(cls) -> str:
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
