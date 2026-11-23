"""Shared helpers for the Grid snapshot tests: real git repositories on disk, isolated from the developer's git setup.

Not a test module: the tests import from it (`from grid_testkit import ...`, which works because pytest puts
this folder on sys.path), including the autouse `isolated_git` fixture.
"""
import io
import os
import subprocess
import tarfile
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_git(monkeypatch):
    """Nothing from the developer's own git configuration (signing, hooks, templates) may leak into the tests."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for who in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{who}_NAME", "Test")
        monkeypatch.setenv(f"GIT_{who}_EMAIL", "test@example.com")
    for name in ("GRID_CODE_DIR", "GRID_COORDINATOR_URL", "GRID_API_TOKEN"):
        monkeypatch.delenv(name, raising=False)


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


FILES = {"tests/test_a.py": "def test_a():\n    assert True\n", "conftest.py": "", "README.md": "# hello\n",
         "bin/run.sh": "#!/bin/sh\necho hi\n"}


@pytest.fixture
def repos(tmp_path_factory):
    """A folder (outside Amplify's data directory) to keep test repositories in."""
    return tmp_path_factory.mktemp("repos")


def make_repo(root: Path, name="tests-repo", files=None) -> Path:
    repo = root / name
    repo.mkdir()
    git(repo, "init", "-q")
    for rel, text in (files or FILES).items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        if rel.endswith(".sh"):
            os.chmod(path, 0o755)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    return repo


@pytest.fixture
def repo(repos):
    return make_repo(repos)


def tree(root: Path) -> dict:
    """{relative file path: bytes} for every file below root."""
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def tar_tree(blob: bytes) -> dict:
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:*") as tar:
        return {m.name: tar.extractfile(m).read() for m in tar if m.isfile()}
