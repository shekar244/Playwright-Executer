"""The Grid tab's test picker: the repo's tests/ folder as a tree of folders, test files and the tests in each file.

Read with `ast` (never imported: importing test code would run it), so a test is what pytest would collect by name:
module-level `test_*` functions and `test_*` methods of `Test*` classes. Their ids are pytest node ids relative to
the repo root (`tests/web/test_login.py::TestLogin::test_valid`), the form the coordinator accepts. Parametrised tests
appear once; their id selects every parameter set.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path

MAX_FILES = 3000
MAX_FILE_BYTES = 2 * 1024 * 1024


def tests_in(path: Path) -> list[str]:
    """The test names in one file: "test_x" or "TestClass::test_y". [] when it cannot be parsed."""
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return []
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError, ValueError):
        return []
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            names.append(node.name)
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            names += [f"{node.name}::{m.name}" for m in node.body
                      if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)) and m.name.startswith("test")]
    return names


def build_tree(repo_root: str | Path) -> dict:
    """{"name": "tests", "path": "tests", "type": "dir", "children": [...]} with file nodes
    {"name", "path", "type": "file", "tests": [names]}. Raises FileNotFoundError without a tests/ folder."""
    root = Path(repo_root)
    tests_dir = root / "tests"
    if not tests_dir.is_dir():
        raise FileNotFoundError(f"There is no tests/ folder in {root.name}")
    top = {"name": "tests", "path": "tests", "type": "dir", "children": []}
    dirs = {"tests": top}
    count = 0
    for current, subdirs, files in os.walk(tests_dir):
        subdirs[:] = sorted(d for d in subdirs if not d.startswith((".", "_")) and d not in ("node_modules", "venv"))
        rel = Path(current).relative_to(root).as_posix()
        node = dirs.get(rel)
        if node is None:
            continue
        for d in subdirs:
            child = {"name": d, "path": f"{rel}/{d}", "type": "dir", "children": []}
            node["children"].append(child)
            dirs[child["path"]] = child
        for f in sorted(f for f in files if f.startswith("test_") and f.endswith(".py")):
            count += 1
            if count > MAX_FILES:
                raise ValueError(f"More than {MAX_FILES} test files: pick a smaller folder")
            node["children"].append({"name": f, "path": f"{rel}/{f}", "type": "file", "tests": tests_in(Path(current) / f)})

    def prune(n: dict) -> bool:                       # drop folders without any test file
        if n["type"] == "file":
            return True
        n["children"] = [c for c in n["children"] if prune(c)]
        return bool(n["children"])

    prune(top)
    return top
