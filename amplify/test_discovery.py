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
        Collect custom markers from all standard pytest configuration sources:
          1. pytest.ini / config/pytest.ini — [pytest] markers = section
          2. pyproject.toml               — [tool.pytest.ini_options] markers
          3. setup.cfg                    — [tool:pytest] markers = section
          4. conftest.py                  — addinivalue_line("markers", ...) calls

        All sources are merged; duplicates are removed preserving first-seen order.
        """
        seen: set = set()
        markers: List[str] = []

        def _add(name: str) -> None:
            name = name.strip()
            if name and name not in seen:
                seen.add(name)
                markers.append(name)

        # ── 1 & 3. pytest.ini / setup.cfg — ini-style [pytest] or [tool:pytest]
        for ini_name in ("pytest.ini", "config/pytest.ini", "setup.cfg"):
            ini_path = self.repo_root / ini_name
            if not ini_path.exists():
                continue
            try:
                import configparser
                cp = configparser.ConfigParser(strict=False)
                cp.read(str(ini_path), encoding="utf-8")
                for section in ("pytest", "tool:pytest"):
                    if cp.has_option(section, "markers"):
                        raw = cp.get(section, "markers")
                        for line in raw.splitlines():
                            line = line.strip()
                            if not line or line.startswith("#") or line.startswith(";"):
                                continue
                            # "marker_name: description" or just "marker_name"
                            name = line.split(":")[0].split()[0]
                            _add(name)
            except Exception:
                pass

        # ── 2. pyproject.toml — [tool.pytest.ini_options] markers
        pyproject = self.repo_root / "pyproject.toml"
        if pyproject.exists():
            try:
                import tomllib  # Python 3.11+
            except ImportError:
                try:
                    import tomli as tomllib  # type: ignore[no-redef]
                except ImportError:
                    tomllib = None  # type: ignore[assignment]
            if tomllib is not None:
                try:
                    with open(pyproject, "rb") as fh:
                        data = tomllib.load(fh)
                    for entry in data.get("tool", {}).get("pytest", {}).get("ini_options", {}).get("markers", []):
                        name = str(entry).split(":")[0].split()[0].strip()
                        _add(name)
                except Exception:
                    pass
            else:
                # Fallback: plain-text scan for quoted marker strings
                try:
                    text = pyproject.read_text(encoding="utf-8", errors="replace")
                    in_markers = False
                    for line in text.splitlines():
                        stripped = line.strip()
                        if re.match(r'^markers\s*=', stripped):
                            in_markers = True
                        elif in_markers and stripped.startswith("["):
                            in_markers = False
                        if in_markers:
                            m = re.search(r'["\']([A-Za-z_][A-Za-z0-9_]*)[\s:"\':]', stripped)
                            if m:
                                _add(m.group(1))
                except Exception:
                    pass

        # ── 4. conftest.py — addinivalue_line("markers", "name: desc") calls
        conftest = self.tests_dir / "conftest.py"
        if conftest.exists():
            try:
                with open(conftest, "r", encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        m = re.search(
                            r'addinivalue_line\s*\(\s*["\']markers["\']\s*,\s*["\']([^"\':\s]+)',
                            line,
                        )
                        if m:
                            _add(m.group(1))
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
