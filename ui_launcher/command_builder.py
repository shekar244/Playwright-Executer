"""
Converts UI selections into a safe pytest argument list.
Never concatenates shell strings — always builds a list for subprocess.
"""

import os
import sys
import shlex
from pathlib import Path
from typing import Dict, List, Optional


class CommandBuilder:
    """Build the pytest invocation from user selections."""

    def __init__(self, repo_root: str):
        self.repo_root = Path(repo_root)

    # ── Public API ────────────────────────────────────────────────────────────

    def build(
        self,
        *,
        suite: str,
        file_sel: str,
        k_filter: str,
        browser: str,
        marker: Optional[str],
        workers: int,
        verbose: bool,
        headed: bool,
        extra: str,
        test_tree: Dict[str, List[str]],
        extra_flags: Optional[List[str]] = None,
        allure_results_dir: str = "allure/results",
    ) -> List[str]:
        """Return a list of strings suitable for subprocess.Popen(args=...)."""

        python = self._resolve_python()
        cmd: List[str] = [python, "-m", "pytest"]

        # ── Test target ───────────────────────────────────────────────────────
        target = self._resolve_target(suite, file_sel, test_tree)
        if target:
            cmd.append(target)

        # ── Config file ───────────────────────────────────────────────────────
        # Check root first (standard), then config/ subfolder
        ini = None
        for candidate in [
            self.repo_root / "pytest.ini",
            self.repo_root / "config" / "pytest.ini",
        ]:
            if candidate.exists():
                ini = candidate
                break
        if ini:
            cmd.extend(["-c", str(ini)])
            # Clear addopts so the UI has full control over options.
            # Without this, pytest.ini's addopts (e.g. --browser chromium -n 4)
            # would stack on top of what the UI passes.
            cmd.extend(["--override-ini", "addopts="])

        # ── Allure results dir ────────────────────────────────────────────────
        if allure_results_dir:
            cmd.append(f"--alluredir={allure_results_dir}")

        # ── Browser ───────────────────────────────────────────────────────────
        if browser:
            cmd.extend(["--browser", browser])

        # ── Headed / headless ─────────────────────────────────────────────────
        if headed:
            cmd.append("--headed")

        # ── Parallelism ───────────────────────────────────────────────────────
        if workers > 1:
            cmd.extend(["-n", str(workers), "--dist", "loadfile"])

        # ── Marker ───────────────────────────────────────────────────────────
        if marker:
            cmd.extend(["-m", marker])

        # ── Name filter ───────────────────────────────────────────────────────
        if k_filter:
            cmd.extend(["-k", k_filter])

        # ── Verbosity ─────────────────────────────────────────────────────────
        if verbose:
            cmd.append("-v")

        # ── Config-defined extra options (dropdowns / checkboxes) ─────────────
        if extra_flags:
            cmd.extend(extra_flags)

        # ── Free-text extra user args ─────────────────────────────────────────
        if extra:
            try:
                cmd.extend(shlex.split(extra))
            except ValueError:
                cmd.append(extra)

        return cmd

    def preview(self, **kwargs) -> str:
        """Return a human-readable preview of the command."""
        return " ".join(self.build(**kwargs))

    # ── Internals ─────────────────────────────────────────────────────────────

    def _resolve_target(
        self, suite: str, file_sel: str, test_tree: Dict[str, List[str]]
    ) -> Optional[str]:
        if suite == "All Tests":
            return None  # pytest discovers everything from testpaths in ini

        if file_sel == "All in Suite":
            return suite  # e.g. "tests/api"

        # Resolve the specific file within the suite
        files = test_tree.get(suite, [])
        for abs_path in files:
            if Path(abs_path).name == file_sel:
                rel = os.path.relpath(abs_path, self.repo_root).replace("\\", "/")
                return rel

        # Fallback: just use the suite folder
        return suite

    def _resolve_python(self) -> str:
        """
        Prefer the venv inside the framework repo, then fall back to the
        system Python that is running this UI.
        """
        for candidate in [
            self.repo_root / "venv" / "bin" / "python",
            self.repo_root / "venv" / "Scripts" / "python.exe",
            self.repo_root / ".venv" / "bin" / "python",
            self.repo_root / ".venv" / "Scripts" / "python.exe",
        ]:
            if candidate.exists():
                return str(candidate)

        return sys.executable
