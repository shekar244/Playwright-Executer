"""The Grid tab's test picker: the repo's tests/ as folders → files → tests (src/amplify_server/grid_tests.py, /api/grid/test-tree).

Read with ast, never imported; ids are pytest node ids relative to the repo root, the form the coordinator accepts.
"""
import importlib
import subprocess
from types import SimpleNamespace

import pytest
from flask import Flask

from amplify_server import auth_utils, grid_tests
from amplify_server.routes import grid

SOURCE = '''
import pytest

def test_top(): pass

async def test_async(): pass

def helper(): pass

class TestLogin:
    def test_valid(self): pass
    def setup_method(self): pass

class Helper:
    def test_not_collected(self): pass

@pytest.mark.parametrize("x", [1, 2])
def test_param(x): pass
'''


def make_repo(root):
    (root / "tests" / "web" / "login").mkdir(parents=True)
    (root / "tests" / "api").mkdir()
    (root / "tests" / "_private").mkdir()
    (root / "tests" / "empty").mkdir()
    (root / "tests" / "web" / "login" / "test_login.py").write_text(SOURCE)
    (root / "tests" / "api" / "test_api.py").write_text("def test_get(): pass\n")
    (root / "tests" / "api" / "helpers.py").write_text("def test_no(): pass\n")
    (root / "tests" / "_private" / "test_hidden.py").write_text("def test_x(): pass\n")
    (root / "tests" / "web" / "test_broken.py").write_text("def test_(:\n")
    return root


def test_the_tree_has_folders_files_and_the_tests_pytest_would_collect(tmp_path):
    tree = grid_tests.build_tree(make_repo(tmp_path))

    assert tree["path"] == "tests" and [c["name"] for c in tree["children"]] == ["api", "web"]   # empty and _private dropped
    api, web = tree["children"]
    assert api["children"] == [{"name": "test_api.py", "path": "tests/api/test_api.py", "type": "file", "tests": ["test_get"]}]
    login = next(c for c in web["children"] if c["name"] == "login")["children"][0]
    assert login["path"] == "tests/web/login/test_login.py"
    assert login["tests"] == ["test_top", "test_async", "TestLogin::test_valid", "test_param"]
    broken = next(c for c in web["children"] if c["name"] == "test_broken.py")
    assert broken["tests"] == []                                   # a file that does not parse still shows (whole-file pick)


def test_without_a_tests_folder_it_says_so(tmp_path):
    with pytest.raises(FileNotFoundError, match="no tests/ folder"):
        grid_tests.build_tree(tmp_path)


def test_a_huge_repo_is_refused(tmp_path, monkeypatch):
    make_repo(tmp_path)
    monkeypatch.setattr(grid_tests, "MAX_FILES", 1)
    with pytest.raises(ValueError, match="More than 1 test files"):
        grid_tests.build_tree(tmp_path)


@pytest.fixture
def client(data_dir, monkeypatch):
    monkeypatch.setattr(auth_utils, "verify_jwt", lambda token: 1)
    monkeypatch.setattr(auth_utils.User, "get_by_id", staticmethod(lambda uid: SimpleNamespace(
        id=1, username="shekar", is_active=True, is_admin=False, must_change_password=False)))
    app = Flask(__name__)
    app.register_blueprint(grid.bp)
    c = app.test_client()
    c.set_cookie("access_token", "x")
    return c


def git_repo(path):
    make_repo(path)
    for cmd in (["init", "-q", "-b", "main"], ["add", "."],
                ["-c", "user.email=a@b", "-c", "user.name=T", "commit", "-q", "-m", "tests"]):
        subprocess.run(["git", *cmd], cwd=path, check=True, capture_output=True)
    return path


def test_the_endpoint_reads_the_users_repo(client, tmp_path_factory):
    repo = git_repo(tmp_path_factory.mktemp("elsewhere") / "qe")     # tmp_path is Amplify's (refused) data folder here

    resp = client.get(f"/api/grid/test-tree?repo={repo}")

    body = resp.get_json()
    assert resp.status_code == 200 and body["repo"] == "qe" and len(body["sha"]) == 40 and body["dirty"] is False
    assert body["tree"]["children"][0]["name"] == "api"


def test_the_endpoint_uses_the_active_repo_by_default(client, tmp_path, data_dir):
    from amplify_server.user_config_reader import UserConfigReader
    repo = git_repo(data_dir / "workspaces" / "1" / "repos" / "qe")
    reader = UserConfigReader(1)
    cfg = reader.load()
    cfg["repo_root"] = str(repo)
    reader.save(cfg)

    resp = client.get("/api/grid/test-tree")
    assert resp.get_json().get("repo") == "qe", resp.get_json()


def test_amplifys_own_data_folder_and_non_repos_are_refused(client, tmp_path, data_dir):
    assert client.get(f"/api/grid/test-tree?repo={data_dir}").status_code in (400, 403)
    assert client.get(f"/api/grid/test-tree?repo={tmp_path / 'nope'}").status_code == 400
    assert client.get("/api/grid/test-tree").status_code == 400                   # no active repo
