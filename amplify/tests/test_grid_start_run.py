"""Starting a Grid run from a repo: POST /api/grid/runs with repo_path, and delivery to the coordinator.

The coordinator is a real local HTTP server that insists on the X-API-Token header and verifies every
upload's checksum, so a wrong request or a leaked secret shows up.
"""
import hashlib
import importlib
import threading
from types import SimpleNamespace

import pytest
from flask import Flask, jsonify, request
from grid_testkit import FILES, git, isolated_git, make_repo, repo, repos, tar_tree  # noqa: F401
from werkzeug.serving import make_server

from amplify import grid_snapshot as gs

TOKEN = "s3cret-token-value"
SELECTION = {"tests": ["tests/test_a.py"], "browsers": ["chromium"], "environment": "qa", "chunk_size": 2}


@pytest.fixture
def app(data_dir):
    flask_app = Flask(__name__)
    for name in ("auth", "admin", "config", "grid"):
        flask_app.register_blueprint(importlib.import_module(f"amplify.routes.{name}").bp)
    return flask_app


@pytest.fixture
def coordinator():
    """The run and code endpoints of a coordinator: stores uploads, checks the token and the checksum header."""
    fake = Flask("fake-coordinator")
    state = SimpleNamespace(store={}, calls=[], tokens=set(), runs=[], put_status=None, run_status=None,
                            features=["code-delivery", "allure-reports"], config_status=200, config_calls=0,
                            echo=True, echo_sha=None)

    @fake.route("/api/config")
    def config():
        """What a current coordinator answers: its menu, plus the features it has."""
        state.tokens.add(request.headers.get("X-API-Token"))
        state.config_calls += 1
        if state.config_status != 200:
            return jsonify({"detail": "config is down"}), state.config_status
        body = {"environments": ["qa"], "browsers": ["chromium"], "default_chunk_size": 3}
        if state.features is not None:
            body["features"] = state.features
        return jsonify(body)

    @fake.route("/api/code/<repo>/<sha>", methods=["GET", "HEAD", "PUT"])
    def code(repo, sha):
        state.tokens.add(request.headers.get("X-API-Token"))
        if request.headers.get("X-API-Token") != TOKEN:
            return jsonify({"detail": "Missing or invalid X-API-Token"}), 401
        state.calls.append((request.method, repo, sha))
        if request.method == "PUT":
            if state.put_status:
                return jsonify({"detail": "nope"}), state.put_status
            body, claimed = request.get_data(), request.headers.get("X-Content-SHA256", "")
            if hashlib.sha256(body).hexdigest() != claimed or int(request.headers["Content-Length"]) != len(body):
                return jsonify({"detail": "checksum mismatch"}), 400
            existed = (repo, sha) in state.store
            state.store[(repo, sha)] = body
            return (jsonify({"status": "exists"}), 200) if existed else (jsonify({"status": "stored", "bytes": len(body)}), 201)
        if (repo, sha) not in state.store:
            return jsonify({"detail": "not found"}), 404
        return state.store[(repo, sha)], 200, {"Content-Type": "application/gzip"}

    @fake.route("/api/runs", methods=["POST"])
    def runs():
        state.tokens.add(request.headers.get("X-API-Token"))
        if state.run_status:
            return jsonify({"detail": "Unsupported browser"}), state.run_status
        state.runs.append(request.get_json())
        answer = {"run_id": "run-1", "chunks_queued": 2, "browsers": ["chromium"], "environment": "qa"}
        if state.echo:                                   # a current coordinator echoes repo / sha (null when absent)
            answer["repo"] = state.runs[-1].get("repo")
            answer["sha"] = state.echo_sha or state.runs[-1].get("sha")
        return jsonify(answer)

    server = make_server("127.0.0.1", 0, fake, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    state.url = f"http://127.0.0.1:{server.server_port}"
    yield state
    server.shutdown()
    server.server_close()


@pytest.fixture
def wired(coordinator, data_dir, monkeypatch):
    """Amplify pointed at the fake coordinator; returns the grid routes module."""
    monkeypatch.setenv("GRID_COORDINATOR_URL", coordinator.url)
    monkeypatch.setenv("GRID_API_TOKEN", TOKEN)
    return importlib.import_module("amplify.routes.grid")


def start(client, repo_path=None, **extra):
    body = {**SELECTION, **extra}
    if repo_path is not None:
        body["repo_path"] = str(repo_path)
    return client.post("/api/grid/runs", json=body)


# ── delivery to the coordinator ───────────────────────────────────────────────

def test_coordinator_delivery_uploads_once_then_finds_it_cached(repo, wired, coordinator):
    info = gs.inspect_repo(str(repo))
    deliver = lambda: gs.deliver(info, code_dir=None, has_code=wired.coordinator_has_code,   # noqa: E731
                                 put_code=wired.coordinator_put_code)
    assert deliver() == "coordinator"
    assert tar_tree(coordinator.store[("tests-repo", info.sha)]) == {k: v.encode() for k, v in FILES.items()}
    assert [c[0] for c in coordinator.calls] == ["HEAD", "PUT"]
    assert deliver() == "cached"
    assert [c[0] for c in coordinator.calls] == ["HEAD", "PUT", "HEAD"]            # the upload was skipped
    assert coordinator.tokens == {TOKEN}


@pytest.mark.parametrize("status, text, expected_status", [
    (413, "larger than its limit", 400), (500, "did not accept", 502), (404, "does not accept code snapshots", 502),
    (400, "did not accept", 502)])
def test_upload_refusals_are_explained(repo, wired, coordinator, status, text, expected_status):
    coordinator.put_status = status
    with pytest.raises(gs.SnapshotError, match=text) as err:
        gs.deliver(gs.inspect_repo(str(repo)), code_dir=None, has_code=wired.coordinator_has_code,
                   put_code=wired.coordinator_put_code)
    assert err.value.status == expected_status and TOKEN not in str(err.value)


def test_an_unreachable_coordinator_is_a_502_that_does_not_leak_the_token(repo, data_dir, monkeypatch):
    monkeypatch.setenv("GRID_COORDINATOR_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("GRID_API_TOKEN", TOKEN)
    grid = importlib.import_module("amplify.routes.grid")
    with pytest.raises(gs.SnapshotError, match="Could not check the coordinator") as err:
        gs.deliver(gs.inspect_repo(str(repo)), code_dir=None, has_code=grid.coordinator_has_code,
                   put_code=grid.coordinator_put_code)
    assert err.value.status == 502 and TOKEN not in str(err.value)


def test_a_wrong_token_shows_as_502_never_401(repo, wired, coordinator, monkeypatch):
    monkeypatch.setenv("GRID_API_TOKEN", "bad-token-9f3a")
    with pytest.raises(gs.SnapshotError) as err:
        gs.deliver(gs.inspect_repo(str(repo)), code_dir=None, has_code=wired.coordinator_has_code,
                   put_code=wired.coordinator_put_code)
    assert err.value.status == 502 and "bad-token-9f3a" not in str(err.value)


# ── POST /api/grid/runs with repo_path ────────────────────────────────────────

def test_a_run_from_a_repo_carries_repo_and_sha_and_says_where_the_code_went(admin, wired, coordinator, repo):
    sha = git(repo, "rev-parse", "HEAD")
    r = start(admin, repo)
    assert r.status_code == 200
    assert r.get_json() == {"run_id": "run-1", "chunks_queued": 2, "browsers": ["chromium"], "environment": "qa",
                            "repo": "tests-repo", "sha": sha, "dirty": False, "code_source": "coordinator"}
    assert coordinator.runs == [{**SELECTION, "requested_by": "boss", "repo": "tests-repo", "sha": sha}]
    assert tar_tree(coordinator.store[("tests-repo", sha)]) == {k: v.encode() for k, v in FILES.items()}
    assert coordinator.tokens == {TOKEN} and TOKEN not in r.get_data(as_text=True)


def test_the_same_commit_is_uploaded_only_once(admin, wired, coordinator, repo):
    assert start(admin, repo).get_json()["code_source"] == "coordinator"
    assert start(admin, repo).get_json()["code_source"] == "cached"
    assert [c[0] for c in coordinator.calls].count("PUT") == 1 and len(coordinator.runs) == 2


def test_a_folder_inside_the_repo_is_refused_naming_the_top_level_folder_to_select(admin, wired, coordinator, repo):
    """Test ids would be listed relative to the sub-folder but the pods run from the repo's top-level."""
    for sub, shown in ((repo / "tests", "'tests'"), (repo / "tests" / ".." / "bin", "'bin'")):
        r = start(admin, sub)
        error = r.get_json()["error"]
        assert r.status_code == 400 and shown in error and "'tests-repo'" in error and "top-level folder" in error
        assert str(repo.parent) not in error
    assert coordinator.runs == [] and coordinator.calls == [] and not coordinator.store


def test_a_nested_sub_folder_is_named_in_full(admin, wired, coordinator, repo):
    (repo / "qa" / "ui").mkdir(parents=True)
    (repo / "qa" / "ui" / "test_b.py").write_text("def test_b(): pass\n")
    git(repo, "add", "-A"), git(repo, "commit", "-q", "-m", "ui")
    error = start(admin, repo / "qa" / "ui").get_json()["error"]
    assert "'qa/ui'" in error and "'tests-repo'" in error


def test_the_top_level_folder_is_accepted_however_it_is_written(admin, wired, coordinator, repo, tmp_path):
    alias = tmp_path / "alias-to-repo"
    alias.symlink_to(repo)
    for path in (str(repo) + "/", repo / "tests" / "..", alias):
        assert start(admin, path).status_code == 200, path
    assert len(coordinator.runs) == 3


def test_uncommitted_changes_are_flagged_and_not_sent(admin, wired, coordinator, repo):
    (repo / "README.md").write_text("EDITED\n")
    (repo / "scratch.txt").write_text("untracked")
    r = start(admin, repo).get_json()
    assert r["dirty"] is True
    sent = tar_tree(coordinator.store[("tests-repo", r["sha"])])
    assert sent["README.md"] == b"# hello\n" and "scratch.txt" not in sent


def test_with_a_shared_volume_the_snapshot_is_unpacked_there_and_nothing_is_uploaded(
        admin, wired, coordinator, repo, tmp_path, monkeypatch):
    vol = tmp_path / "tests-store"
    vol.mkdir()
    monkeypatch.setenv("GRID_CODE_DIR", str(vol))
    sha = git(repo, "rev-parse", "HEAD")
    r = start(admin, repo)
    assert r.get_json()["code_source"] == "volume" and r.get_json()["sha"] == sha
    assert coordinator.calls == [] and coordinator.runs[0]["repo"] == "tests-repo" and coordinator.runs[0]["sha"] == sha
    assert (vol / "tests-repo" / sha / "tests" / "test_a.py").read_text() == FILES["tests/test_a.py"]
    assert start(admin, repo).get_json()["code_source"] == "volume"           # already there: reused


def test_without_repo_path_nothing_changes(admin, wired, coordinator):
    r = start(admin)
    assert r.status_code == 200 and r.get_json() == {"run_id": "run-1", "chunks_queued": 2, "browsers": ["chromium"],
                                                     "environment": "qa", "repo": None, "sha": None}
    assert coordinator.runs == [{**SELECTION, "requested_by": "boss"}] and coordinator.calls == []
    assert coordinator.config_calls == 0                                   # nothing to check when no code is delivered


@pytest.mark.parametrize("blank", ["", "   ", None])
def test_a_blank_repo_path_means_no_snapshot(admin, wired, coordinator, blank):
    r = admin.post("/api/grid/runs", json={**SELECTION, "repo_path": blank})
    assert r.status_code == 200 and r.get_json()["repo"] is None and "dirty" not in r.get_json() and coordinator.calls == []
    assert "repo" not in coordinator.runs[0] and "sha" not in coordinator.runs[0]


@pytest.mark.parametrize("bad", [5, ["x"], {"a": 1}, True])
def test_repo_path_must_be_text(admin, wired, coordinator, bad):
    assert admin.post("/api/grid/runs", json={**SELECTION, "repo_path": bad}).status_code == 400
    assert coordinator.runs == []


def test_a_repo_path_needs_a_login(app, wired, repo):
    assert start(app.test_client(), repo).status_code == 401


def test_paths_that_are_not_git_work_trees_never_reach_the_coordinator(admin, wired, coordinator, repos):
    plain = repos / "plain"
    plain.mkdir()
    for path in (plain, "relative/folder", repos / "missing"):
        r = start(admin, path)
        assert r.status_code == 400 and str(repos) not in r.get_json()["error"]
    assert coordinator.runs == [] and coordinator.calls == []


def test_amplifys_data_directory_is_refused_but_a_users_own_clones_are_allowed(admin, wired, coordinator, data_dir):
    inside = make_repo(data_dir, "inside-data")
    r = start(admin, inside)
    assert r.status_code == 400 and "Amplify's own data" in r.get_json()["error"] and str(data_dir) not in r.get_json()["error"]

    mine, theirs = data_dir / "workspaces" / "1" / "repos", data_dir / "workspaces" / "2" / "repos"
    mine.mkdir(parents=True, exist_ok=True), theirs.mkdir(parents=True, exist_ok=True)       # admin is user 1
    assert start(admin, make_repo(theirs, "not-mine")).status_code == 400
    assert start(admin, make_repo(mine, "my-clone")).get_json()["repo"] == "my-clone"
    assert [run["repo"] for run in coordinator.runs] == ["my-clone"]


def test_a_repo_over_the_size_cap_is_a_400_and_no_run_is_created(admin, wired, coordinator, repos, monkeypatch):
    import os
    big = make_repo(repos, "big", {"blob.bin": ""})
    (big / "blob.bin").write_bytes(os.urandom(2_000_000))
    git(big, "add", "-A"), git(big, "commit", "-q", "-m", "blob")
    monkeypatch.setattr(gs, "MAX_SNAPSHOT_BYTES", 1024 * 1024)
    r = start(admin, big)
    assert r.status_code == 400 and "larger than 1 MiB" in r.get_json()["error"]
    assert coordinator.runs == [] and not coordinator.store


def test_a_repo_with_a_symlink_is_refused_with_the_reason(admin, wired, coordinator, repos):
    linked = make_repo(repos, "linked")
    (linked / "alias").symlink_to("README.md")
    git(linked, "add", "-A"), git(linked, "commit", "-q", "-m", "link")
    r = start(admin, linked)
    assert r.status_code == 400 and "symlink" in r.get_json()["error"] and coordinator.runs == []


def test_when_the_coordinator_refuses_the_snapshot_the_user_is_told(admin, wired, coordinator, repo):
    coordinator.put_status = 500
    r = start(admin, repo)
    assert r.status_code == 502 and "did not accept the snapshot" in r.get_json()["error"] and coordinator.runs == []


def test_when_the_coordinator_refuses_the_run_its_error_passes_through_without_repo_fields(admin, wired, coordinator, repo):
    coordinator.run_status = 422
    r = start(admin, repo)
    assert r.status_code == 422 and r.get_json() == {"error": "Unsupported browser"}


def test_no_packaging_happens_when_no_coordinator_is_configured(admin, data_dir, repo, monkeypatch):
    monkeypatch.setattr(gs, "build_snapshot", lambda *a, **k: pytest.fail("must not package the repo"))
    r = start(admin, repo)
    assert r.status_code == 503 and "Admin Panel" in r.get_json()["error"]


def test_the_selection_is_still_validated_first(admin, wired, coordinator, repo):
    r = admin.post("/api/grid/runs", json={"tests": [" "], "repo_path": str(repo)})
    assert r.status_code == 400 and coordinator.calls == []


# ── a coordinator that cannot carry repo/sha (an old one) ─────────────────────

def never_package(monkeypatch):
    monkeypatch.setattr(gs, "build_snapshot", lambda *a, **k: pytest.fail("must not package the repo"))


def test_an_old_coordinator_is_refused_before_anything_is_packaged_or_started(admin, wired, coordinator, repo, monkeypatch):
    never_package(monkeypatch)
    for features in (None, [], ["allure-reports"], "code-delivery", {"code-delivery": True}, 5):    # None: no "features" at all
        coordinator.features = features
        r = start(admin, repo)
        assert r.status_code == 502 and r.get_json() == {"error": "The Grid coordinator is too old for code delivery — update it"}, features
    assert coordinator.runs == [] and coordinator.calls == [] and coordinator.config_calls == 6


def test_an_old_coordinator_is_refused_even_with_a_shared_volume(admin, wired, coordinator, repo, tmp_path, monkeypatch):
    vol = tmp_path / "tests-store"
    vol.mkdir()
    monkeypatch.setenv("GRID_CODE_DIR", str(vol))
    coordinator.features = []
    r = start(admin, repo)
    assert r.status_code == 502 and "too old" in r.get_json()["error"]
    assert list(vol.iterdir()) == [] and coordinator.runs == []                    # nothing was unpacked either


def test_a_current_coordinator_is_checked_once_per_run_and_runs_proceed(admin, wired, coordinator, repo):
    assert start(admin, repo).status_code == 200 and start(admin, repo).status_code == 200
    assert coordinator.config_calls == 2 and len(coordinator.runs) == 2


def test_a_coordinator_that_cannot_answer_the_check_is_a_502_with_the_reason(admin, wired, coordinator, repo, monkeypatch):
    never_package(monkeypatch)
    for status in (401, 403, 404, 500):
        coordinator.config_status = status
        r = start(admin, repo)
        assert r.status_code == 502 and "Could not check the Grid coordinator: config is down" in r.get_json()["error"], status
        assert TOKEN not in r.get_data(as_text=True)
    assert coordinator.runs == []


def test_an_unreachable_coordinator_is_a_502_before_packaging(admin, data_dir, repo, monkeypatch):
    monkeypatch.setenv("GRID_COORDINATOR_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("GRID_API_TOKEN", TOKEN)
    never_package(monkeypatch)
    r = start(admin, repo)
    assert r.status_code == 502 and "Could not check the Grid coordinator" in r.get_json()["error"] and TOKEN not in r.get_data(as_text=True)


# ── did the coordinator take repo and sha? ────────────────────────────────────

def test_a_coordinator_that_echoes_repo_and_sha_gives_no_warning(admin, wired, coordinator, repo):
    assert "warning" not in start(admin, repo).get_json()


def test_a_coordinator_that_does_not_echo_repo_and_sha_gets_a_warning_in_the_answer(admin, wired, coordinator, repo):
    coordinator.echo = False                             # advertises the feature but drops the fields
    sha = git(repo, "rev-parse", "HEAD")
    r = start(admin, repo)
    body = r.get_json()
    assert r.status_code == 200 and body["run_id"] == "run-1" and body["repo"] == "tests-repo" and body["sha"] == sha
    assert f"tests-repo@{sha[:7]}" in body["warning"] and "did not confirm" in body["warning"]
    assert TOKEN not in r.get_data(as_text=True)


def test_a_coordinator_that_echoes_another_commit_gets_a_warning(admin, wired, coordinator, repo):
    coordinator.echo_sha = "f" * 40
    assert "did not confirm" in start(admin, repo).get_json()["warning"]


# ── malformed requests are a JSON 400, never an HTML 500 ──────────────────────

def raw_post(client, data, content_type="application/json"):
    return client.post("/api/grid/runs", data=data, headers={"Content-Type": content_type})


def test_a_body_that_is_not_a_json_object_is_a_400(admin, wired, coordinator):
    for data, content_type in [("[1, 2]", "application/json"), ('"tests"', "application/json"), ("5", "application/json"),
                               ("null", "application/json"), ("true", "application/json"), ("{not json", "application/json"),
                               ('{"tests": ["a.py"]}', "text/plain"), (b"\xff\xfe\x00", "application/json")]:
        r = raw_post(admin, data, content_type)
        assert r.status_code == 400 and r.is_json and "JSON object" in r.get_json()["error"], data
    assert coordinator.runs == []


def test_an_empty_body_is_asked_for_tests(admin, wired, coordinator):
    r = raw_post(admin, b"")
    assert r.status_code == 400 and r.get_json() == {"error": "Select at least one test"}


WRONG_TYPES = [
    ({"tests": 5}, "tests must be a list"), ({"tests": "tests/a.py"}, "tests must be a list"),
    ({"tests": {"a": 1}}, "tests must be a list"), ({"tests": ["a.py", 5]}, "tests must be a list"),
    ({"tests": [["a.py"]]}, "tests must be a list"), ({"tests": [None]}, "tests must be a list"),
    ({"tests": None}, "tests must be a list"), ({"tests": []}, "at least one test"),
    ({"chunk_size": "abc"}, "chunk_size"), ({"chunk_size": "2"}, "chunk_size"), ({"chunk_size": 0}, "chunk_size"),
    ({"chunk_size": 11}, "chunk_size"), ({"chunk_size": -1}, "chunk_size"), ({"chunk_size": 2.5}, "chunk_size"),
    ({"chunk_size": True}, "chunk_size"), ({"chunk_size": [3]}, "chunk_size"), ({"chunk_size": {}}, "chunk_size"),
    ({"environment": 5}, "environment must be text"), ({"environment": ["qa"]}, "environment must be text"),
    ({"browsers": "chromium"}, "browsers must be a list"), ({"browsers": [5]}, "browsers must be a list"),
    ({"browsers": {"a": 1}}, "browsers must be a list"), ({"browsers": 7}, "browsers must be a list"),
    ({"repo_path": 5}, "repo_path"), ({"repo_path": ["/x"]}, "repo_path")]


def test_wrong_field_types_are_a_json_400_naming_the_field(admin, wired, coordinator):
    for change, text in WRONG_TYPES:
        r = admin.post("/api/grid/runs", json={**SELECTION, **change})
        assert r.status_code == 400 and r.is_json and text in r.get_json()["error"], (change, r.get_data(as_text=True))
    assert coordinator.runs == [] and coordinator.calls == [] and coordinator.config_calls == 0


def test_valid_optional_fields_are_accepted(admin, wired, coordinator):
    for change in ({"chunk_size": None}, {"chunk_size": 1}, {"chunk_size": 10}, {"environment": None}, {"browsers": None},
                   {"browsers": []}, {"environment": "  "}):
        assert admin.post("/api/grid/runs", json={**SELECTION, **change}).status_code == 200, change


def test_an_omitted_chunk_size_defaults_to_three(admin, wired, coordinator):
    body = {k: v for k, v in SELECTION.items() if k != "chunk_size"}
    assert admin.post("/api/grid/runs", json=body).status_code == 200 and coordinator.runs[0]["chunk_size"] == 3
