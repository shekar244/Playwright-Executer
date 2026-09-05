"""
Converts UI selections into a safe pytest argument list.
Never concatenates shell strings — always builds a list for subprocess.
"""

import os
import sys
import shlex
from pathlib import Path
from typing import Dict, List, Optional


def resolve_python(repo_root: str, venv_path: str = "") -> str:
    """Resolve the Python executable for a given repo and optional venv path."""
    return CommandBuilder(repo_root, venv_path)._resolve_python()


def venv_env_overrides(python_path: str) -> dict:
    """
    Return environment variable overrides that mimic `source venv/bin/activate`
    (or `venv\\Scripts\\activate` on Windows), so subprocesses behave as if the
    venv is activated — correct VIRTUAL_ENV, PATH, and no PYTHONHOME.
    """
    p = Path(python_path)
    # python lives at <venv>/bin/python  or  <venv>/Scripts/python.exe
    venv_root = p.parent.parent  # go up two levels from the executable
    env = os.environ.copy()
    env["VIRTUAL_ENV"] = str(venv_root)
    env.pop("PYTHONHOME", None)          # activation always unsets this
    env["PYTHONUNBUFFERED"] = "1"        # ensure streaming output
    # Prepend venv's bin/Scripts to PATH so `python`, `pip`, etc. resolve correctly
    bin_dir = str(p.parent)
    env["PATH"] = bin_dir + os.pathsep + env.get("PATH", "")
    return env


class CommandBuilder:
    """Build the pytest invocation from user selections."""

    def __init__(self, repo_root: str, venv_path: str = ""):
        self.repo_root = Path(repo_root)
        self.venv_path = venv_path  # configured venv (relative to repo or absolute)

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
        Resolution order:
          1. Configured venv_path (relative to repo root or absolute)
          2. Auto-detect common venv names inside the repo (.venv, venv, env)
          3. System Python running this tool
        """
        # 1. Configured venv path
        if self.venv_path:
            base = (Path(self.venv_path) if Path(self.venv_path).is_absolute()
                    else self.repo_root / self.venv_path)
            for suffix in ["bin/python", "bin/python3", "Scripts/python.exe"]:
                p = base / suffix
                if p.exists():
                    return str(p)

        # 2. Auto-detect common venv names
        for name in [".venv", "venv", "env", ".env"]:
            base = self.repo_root / name
            for suffix in ["bin/python", "bin/python3", "Scripts/python.exe"]:
                p = base / suffix
                if p.exists():
                    return str(p)

        # 3. Fall back to the system Python running this tool
        return sys.executable
