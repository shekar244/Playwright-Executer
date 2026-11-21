import io
from pathlib import Path

import pytest

from amplify.models.user import User
from amplify.user_config_reader import UserConfigReader


@pytest.fixture
def app(data_dir):
    from amplify.server import app as real_app
    return real_app


def _set_active_repo(user_name):
    user = User.get_by_username(user_name)
    repo = Path(user_name)
    from amplify.storage import get_user_workspace_dir
    repo = get_user_workspace_dir(user.id) / "repos" / "demo"
    (repo / "testdata" / "nested").mkdir(parents=True)
    (repo / ".git").mkdir()
    reader = UserConfigReader(user.id)
    cfg = reader.load()
    cfg["repo_root"] = str(repo)
    reader.save(cfg)
    return repo


def test_test_data_tab_is_available_to_regular_users(app, user):
    repo = _set_active_repo("plain")
    page = user.get("/").get_data(as_text=True)
    assert 'id="navbtn-testdata"' in page
    assert 'id="page-testdata"' in page
    assert 'id="tdFolderTree"' in page


def test_test_data_lists_only_files_in_the_active_cloned_repo(app, user):
    repo = _set_active_repo("plain")
    (repo / "testdata" / "nested" / "users.csv").write_text("name\nAda\n")
    r = user.get("/api/testdata/tree", query_string={"folder": "testdata/nested"})
    assert r.status_code == 200
    data = r.get_json()
    assert data["folder"] == "testdata/nested"
    assert data["files"] == [{
        "name": "users.csv",
        "size": 9,
        "modified": data["files"][0]["modified"],
    }]
    assert "testdata/nested" in [folder["path"] for folder in data["folders"]]


def test_test_data_uploads_replaces_and_deletes_any_file_type(app, user):
    repo = _set_active_repo("plain")
    r = user.post(
        "/api/testdata/upload",
        data={"folder": "testdata", "files": [(io.BytesIO(b"first"), "accounts.any")]},
        content_type="multipart/form-data",
    )
    assert r.status_code == 200
    assert r.get_json()["uploaded"] == ["testdata/accounts.any"]
    target = repo / "testdata" / "accounts.any"
    assert target.read_bytes() == b"first"

    r = user.post(
        "/api/testdata/upload",
        data={"folder": "testdata", "files": [(io.BytesIO(b"replacement"), "accounts.any")]},
        content_type="multipart/form-data",
    )
    assert r.status_code == 200
    assert r.get_json()["replaced"] == ["testdata/accounts.any"]
    assert target.read_bytes() == b"replacement"

    r = user.delete(
        "/api/testdata/file",
        json={"folder": "testdata", "name": "accounts.any"},
    )
    assert r.status_code == 200
    assert not target.exists()


@pytest.mark.parametrize("folder", ["../outside", "/tmp", ".git", ".git/config"])
def test_test_data_rejects_paths_outside_folders(app, user, folder):
    repo = _set_active_repo("plain")
    r = user.post(
        "/api/testdata/upload",
        data={"folder": folder, "files": [(io.BytesIO(b"bad"), "bad.txt")]},
        content_type="multipart/form-data",
    )
    assert r.status_code == 400
    assert not (repo.parent / "outside" / "bad.txt").exists()


def test_test_data_requires_an_active_cloned_repo(app, user):
    r = user.get("/api/testdata/tree")
    assert r.status_code == 400
