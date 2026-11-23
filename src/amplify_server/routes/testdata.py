"""
Blueprint: Test Data routes
  GET    /api/testdata/tree   - list folders and files in the active cloned repo
  POST   /api/testdata/upload - upload files to a chosen repo folder
  DELETE /api/testdata/file   - delete a file from the active cloned repo
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from flask import Blueprint, g, jsonify, request

from amplify_server.auth_utils import require_auth
from amplify_server.models.user import ActivityLog
from amplify_server.request_body import json_object
from amplify_server.storage import get_user_workspace_dir
from amplify_server.user_config_reader import UserConfigReader

bp = Blueprint("testdata", __name__)


def _active_repo(user_id: int) -> tuple[Path | None, str | None]:
    cfg = UserConfigReader(user_id).load()
    raw = str(cfg.get("repo_root", "")).strip()
    if not raw:
        return None, "No repo configured"

    try:
        repo = Path(raw).resolve()
        repos_root = (get_user_workspace_dir(user_id) / "repos").resolve()
    except (OSError, RuntimeError):
        return None, "Invalid repo path"

    if not repo.is_dir():
        return None, "Repo path not found"
    if not (repo / ".git").is_dir():
        return None, "Active repo is not a cloned git repository"
    try:
        repo.relative_to(repos_root)
    except ValueError:
        return None, "Active repo is outside the user workspace"
    return repo, None


def _folder_path(repo: Path, raw: str) -> tuple[Path | None, str | None]:
    raw = (raw or "").strip().replace("\\", "/")
    if raw in ("", "."):
        target = repo
    else:
        if raw.startswith("/") or ".." in raw.split("/"):
            return None, "Invalid folder"
        target = (repo / raw).resolve()

    try:
        target.relative_to(repo)
    except ValueError:
        return None, "Folder is outside the repository"
    if target == repo / ".git" or str(target).startswith(str(repo / ".git") + os.sep):
        return None, ".git is not available as Test Data"
    if not target.is_dir():
        return None, "Folder not found"
    return target, None


def _relative_folder(repo: Path, folder: Path) -> str:
    return "." if folder == repo else folder.relative_to(repo).as_posix()


def _safe_filename(raw: str) -> str | None:
    name = Path(str(raw).replace("\\", "/")).name
    if not name or name in (".", ".."):
        return None
    return name


@bp.route("/api/testdata/tree")
@require_auth
def testdata_tree():
    repo, error = _active_repo(g.user.id)
    if error:
        return jsonify({"error": error}), 400

    folders = [{"name": ".", "path": "."}]
    for child in sorted(repo.rglob("*"), key=lambda p: p.as_posix().lower()):
        if ".git" in child.parts or not child.is_dir():
            continue
        try:
            child.resolve().relative_to(repo)
        except (ValueError, RuntimeError, OSError):
            continue
        folders.append({
            "name": child.relative_to(repo).as_posix(),
            "path": child.relative_to(repo).as_posix(),
        })

    folder_raw = request.args.get("folder", ".").strip()
    folder, error = _folder_path(repo, folder_raw)
    if error:
        return jsonify({"error": error}), 400

    files = []
    for child in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_file() or child.is_symlink():
            continue
        stat = child.stat()
        files.append({
            "name": child.name,
            "size": stat.st_size,
            "modified": int(stat.st_mtime),
        })

    return jsonify({
        "repo": repo.name,
        "folder": _relative_folder(repo, folder),
        "folders": folders,
        "files": files,
    })


@bp.route("/api/testdata/upload", methods=["POST"])
@require_auth
def testdata_upload():
    repo, error = _active_repo(g.user.id)
    if error:
        return jsonify({"error": error}), 400

    folder, error = _folder_path(repo, request.form.get("folder", "."))
    if error:
        return jsonify({"error": error}), 400

    uploads = request.files.getlist("files")
    if not uploads:
        return jsonify({"error": "Choose at least one file"}), 400

    uploaded = []
    replaced = []
    for storage in uploads:
        name = _safe_filename(storage.filename)
        if not name:
            return jsonify({"error": "Invalid file name"}), 400
        destination = folder / name
        if destination.is_symlink():
            return jsonify({"error": f"Cannot replace symbolic link: {name}"}), 400

        existed = destination.is_file()
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(dir=folder, delete=False) as temp:
                temp_path = Path(temp.name)
                shutil.copyfileobj(storage.stream, temp)
            os.replace(temp_path, destination)
        except OSError as exc:
            if temp_path:
                temp_path.unlink(missing_ok=True)
            return jsonify({"error": f"Could not upload {name}: {exc}"}), 500

        record = destination.relative_to(repo).as_posix()
        (replaced if existed else uploaded).append(record)

    ActivityLog.record(
        g.user.id,
        "testdata_upload",
        f"Uploaded {len(uploaded)} file(s); replaced {len(replaced)} file(s)",
        request.remote_addr,
    )
    return jsonify({
        "ok": True,
        "uploaded": uploaded,
        "replaced": replaced,
    })


@bp.route("/api/testdata/file", methods=["DELETE"])
@require_auth
def testdata_delete():
    body, error = json_object()
    if error:
        return error

    repo, error = _active_repo(g.user.id)
    if error:
        return jsonify({"error": error}), 400

    folder, error = _folder_path(repo, str(body.get("folder", "")).strip())
    if error:
        return jsonify({"error": error}), 400
    name = _safe_filename(str(body.get("name", "")).strip())
    if not name:
        return jsonify({"error": "File name required"}), 400

    raw_destination = folder / name
    if raw_destination.is_symlink():
        return jsonify({"error": "Symbolic links cannot be deleted"}), 400
    destination = raw_destination.resolve()
    try:
        destination.relative_to(repo)
    except ValueError:
        return jsonify({"error": "File is outside the repository"}), 400
    if destination.is_symlink() or not destination.is_file():
        return jsonify({"error": "File not found"}), 404

    try:
        destination.unlink()
    except OSError as exc:
        return jsonify({"error": f"Could not delete file: {exc}"}), 500

    ActivityLog.record(
        g.user.id,
        "testdata_delete",
        f"Deleted {destination.relative_to(repo).as_posix()}",
        request.remote_addr,
    )
    return jsonify({"ok": True, "deleted": destination.relative_to(repo).as_posix()})
