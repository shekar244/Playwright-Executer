"""
Blueprint: Distributed grid (proxy to the central coordinator pod)

The coordinator (gridtestframework) runs Playwright tests across runner pods on OpenShift.
This blueprint lets the Amplify UI choose tests / environment / browsers, start a run, and
watch pods and runs. The coordinator URL and token stay server-side; browsers never see them.

  GET  /api/grid/status              — configured? reachable?
  GET  /api/grid/config              — browsers + environments the coordinator allows
  GET  /api/grid/pods                — runner pods and their status
  GET  /api/grid/runs                — recent runs with progress
  GET  /api/grid/runs/<run_id>       — one run: chunks and per-test outcomes
  GET  /api/grid/chunks/<chunk_id>   — chunk record including pytest output
  POST /api/grid/runs/<run_id>/cancel — cancel a queued or running run. Only the person who started it, or an
                                       admin, may. Queued chunks are dropped; a chunk already running finishes on
                                       its pod but its results are discarded; finished chunks keep theirs.
  GET  /api/grid/test-tree           — the repo's tests/ as folders → files → tests for the New run picker (grid_tests.py)
  GET  /api/grid/pytest-args         — the extra pytest arguments configured in Admin → Grid (see grid_args.py)
  POST /api/grid/runs                — start a run {tests, environment, browsers, chunk_size[, repo_path][, pytest_args]}
                                       pytest_args is {option: value | true/false}, checked against Admin → Grid
                                       with repo_path (the repo's top-level folder) the tests run from a snapshot
                                       of that repo's HEAD, delivered to the pods (see grid_snapshot.py). The
                                       coordinator must advertise "code-delivery" in /api/config "features", and
                                       must echo repo/sha back (else the answer carries a "warning").
                                       A body or field of the wrong type is a JSON 400, never a 500.

Where the coordinator is comes from the Admin Panel (Grid Coordinator), with these environment
variables as defaults — a value saved in the Admin Panel wins (see server_secrets.load_grid_config):
  GRID_COORDINATOR_URL  — e.g. http://central-coordinator-svc:8000
  GRID_API_TOKEN        — shared secret sent as X-API-Token (matches the coordinator's API_TOKEN)
  GRID_CODE_DIR         — the shared tests-store volume, mounted read-write here and read-only on the pods;
                          snapshots are unpacked into it. Unset: they are uploaded to the coordinator instead
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from flask import Blueprint, g, jsonify, request

from amplify import grid_args, grid_snapshot, grid_tests
from amplify.auth_utils import require_auth
from amplify.config import settings
from amplify.grid_snapshot import SnapshotError
from amplify.request_body import json_object
from amplify.server_secrets import load_grid_config

bp = Blueprint("grid", __name__)

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")      # starts alphanumeric: ".." and "." never reach a coordinator path
_TIMEOUT = 8
_CODE_TIMEOUT = 120                       # sending a snapshot (up to 100 MiB) to the coordinator
REPORT_TIMEOUT = 150                      # the coordinator may need up to ~2 minutes to generate a report
REPORT_MAX_BYTES = 200 * 1024 * 1024
CODE_DELIVERY = "code-delivery"           # the coordinator's /api/config "features" entry for repo/sha runs
PYTEST_ARGS = "pytest-args"               # ... and for runs that carry extra pytest arguments (Admin → Grid)
DEFAULT_CHUNK_SIZE, CHUNK_SIZE_RANGE = 3, (1, 10)    # the coordinator accepts 1..10
_COORDINATOR_TOO_OLD = "The Grid coordinator is too old for code delivery — update it"


def _coordinator_url() -> str:
    return load_grid_config()["coordinator_url"]


def probe() -> dict:
    """Is a coordinator set up and answering? Also says what it offers (for the Admin Panel)."""
    if not _coordinator_url():
        return {"configured": False, "reachable": False}
    data, code = coordinator_request("GET", "/api/config")
    status = {"configured": True, "reachable": code == 200, "url": _coordinator_url(),
              "error": None if code == 200 else data.get("error")}
    if code == 200:
        status["environments"] = len(data.get("environments") or [])
        status["browsers"] = len(data.get("browsers") or [])
    return status


def _target(path: str, params: dict | None = None) -> tuple[str, dict] | None:
    """(URL, auth headers) for a coordinator path, or None when no coordinator is set up. The token goes
    only into the X-API-Token header — never into a URL, a message or a log line."""
    base = _coordinator_url()
    if not base:
        return None
    url = f"{base}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {}
    token = load_grid_config()["api_token"]
    if token:
        headers["X-API-Token"] = token
    return url, headers


_NOT_CONFIGURED = ("Grid is not configured — an admin can set the coordinator in "
                   "Admin Panel → Grid Coordinator")


def _http_error(exc: urllib.error.HTTPError) -> tuple[str, int]:
    """(message, status) for an error answer; a rejected token is shown as 502, never as a 401 for the browser."""
    try:
        detail = json.loads(exc.read()).get("detail", exc.reason)
    except Exception:
        detail = exc.reason
    if isinstance(detail, list):  # FastAPI validation errors
        detail = "; ".join(str(d.get("msg", d)) for d in detail)
    return str(detail), (502 if exc.code in (401, 403) else exc.code)


def coordinator_request(method: str, path: str, params: dict | None = None, body: dict | None = None,
                        timeout: float = _TIMEOUT) -> tuple[dict, int]:
    """Call the coordinator and return (payload, status). Errors come back as ({"error": ...}, status);
    this never raises, so callers can show "Grid unavailable" instead of failing."""
    target = _target(path, params)
    if target is None:
        return {"error": _NOT_CONFIGURED, "configured": False}, 503
    url, headers = target
    headers["Accept"] = "application/json"
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read() or b"{}"), resp.status
    except urllib.error.HTTPError as exc:
        message, status = _http_error(exc)
        return {"error": message}, status
    except Exception as exc:  # unreachable, timeout, bad JSON
        return {"error": f"Coordinator unreachable: {exc}"}, 502


def _size(n: int) -> str:
    return f"{n // (1024 * 1024)} MiB" if n >= 1024 * 1024 else f"{n} bytes"


@dataclass
class RawReply:
    """A coordinator answer that is not JSON (a tarball upload, a header-only check, an HTML report)."""
    status: int
    headers: dict = field(default_factory=dict)      # lower-case names
    body: bytes = b""
    error: str | None = None                         # set when status is not a success

    @property
    def ok(self) -> bool:
        return self.error is None


def coordinator_raw(method: str, path: str, *, data=None, headers: dict | None = None,
                    timeout: float = _TIMEOUT, max_bytes: int | None = None) -> RawReply:
    """Like coordinator_request for bodies that are not JSON. `data` may be bytes or an open binary file (give
    Content-Length in `headers` for a file). At most `max_bytes` of the answer are read; more is an error.
    304 counts as success. Never raises."""
    target = _target(path)
    if target is None:
        return RawReply(503, error=_NOT_CONFIGURED)
    url, auth = target
    req = urllib.request.Request(url, data=data, headers={**auth, **(headers or {})}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            got = {k.lower(): v for k, v in resp.headers.items()}
            too_big = RawReply(502, got, error=f"The coordinator's answer is larger than {_size(max_bytes or 0)}")
            if max_bytes is not None and int(got.get("content-length") or 0) > max_bytes:
                return too_big
            blocks, total = [], 0
            while True:                                  # never hold more than the cap (+1 block) in memory
                block = resp.read(1024 * 1024)
                if not block:
                    break
                total += len(block)
                if max_bytes is not None and total > max_bytes:
                    return too_big
                blocks.append(block)
            return RawReply(resp.status, got, b"".join(blocks))
    except urllib.error.HTTPError as exc:
        got = {k.lower(): v for k, v in exc.headers.items()} if exc.headers else {}
        if exc.code == 304:
            return RawReply(304, got)
        message, status = _http_error(exc)
        return RawReply(status, got, error=message)
    except Exception as exc:  # unreachable, timeout
        return RawReply(502, error=f"Coordinator unreachable: {exc}")


def coordinator_download(path: str, *, headers: dict | None = None, max_bytes: int | None = None,
                         timeout: float | None = None) -> RawReply:
    """GET a (possibly large) non-JSON answer — an Allure report — with a hard size cap (REPORT_MAX_BYTES,
    200 MiB) and a long timeout (REPORT_TIMEOUT, 150 s) unless told otherwise."""
    return coordinator_raw("GET", path, headers=headers, timeout=timeout or REPORT_TIMEOUT,
                           max_bytes=REPORT_MAX_BYTES if max_bytes is None else max_bytes)


def coordinator_has_code(repo: str, sha: str) -> bool:
    """Does the coordinator already hold this snapshot? (HEAD /api/code/<repo>/<sha>)"""
    reply = coordinator_raw("HEAD", f"/api/code/{repo}/{sha}")
    if reply.ok:
        return True
    if reply.status == 404:
        return False
    raise SnapshotError(f"Could not check the coordinator for this code: {reply.error}", 502)


def coordinator_put_code(repo: str, sha: str, snap: grid_snapshot.Snapshot) -> None:
    """Send a snapshot to the coordinator (PUT /api/code/<repo>/<sha>, checksum in X-Content-SHA256)."""
    with open(snap.path, "rb") as body:
        reply = coordinator_raw(
            "PUT", f"/api/code/{repo}/{sha}", data=body, timeout=_CODE_TIMEOUT,
            headers={"Content-Type": "application/gzip", "Content-Length": str(snap.size),
                     "X-Content-SHA256": snap.sha256})
    if reply.ok:
        return
    if reply.status == 413:
        raise SnapshotError("The coordinator refused the snapshot: it is larger than its limit", 400)
    if reply.status in (404, 405):
        raise SnapshotError("This coordinator does not accept code snapshots — is it up to date?", 502)
    raise SnapshotError(f"The coordinator did not accept the snapshot: {reply.error}", 502)


def _call(method: str, path: str, params: dict | None = None, body: dict | None = None):
    """Call the coordinator. Returns (flask response, status code)."""
    payload, status = coordinator_request(method, path, params, body)
    return jsonify(payload), status


@bp.route("/api/grid/status")
@require_auth
def grid_status():
    return jsonify(probe())


@bp.route("/api/grid/config")
@require_auth
def grid_config():
    payload, status = coordinator_request("GET", "/api/config")
    if status == 200 and isinstance(payload, dict):
        configured = grid_args.run_environments(grid_args.load_definitions())
        environments = configured["environments"]
        if environments:
            payload["environments"] = environments
            payload["default_environment"] = (
                configured["default_environment"] if configured["default_environment"] in environments
                else environments[0]
            )
    return jsonify(payload), status


@bp.route("/api/grid/pods")
@require_auth
def grid_pods():
    return _call("GET", "/api/pods")


@bp.route("/api/grid/runs", methods=["GET"])
@require_auth
def grid_runs():
    return _call("GET", "/api/runs", params={"limit": request.args.get("limit", "25")})


@bp.route("/api/grid/runs/<run_id>")
@require_auth
def grid_run(run_id):
    if not _ID.match(run_id):
        return jsonify({"error": "Invalid run id"}), 400
    return _call("GET", f"/api/runs/{run_id}")


@bp.route("/api/grid/runs/<run_id>/cancel", methods=["POST"])
@require_auth
def grid_cancel_run(run_id):
    """Cancel a run. The Grid is shared by everyone with access to Amplify, so only the person who started the run
    (the coordinator records their username) or an admin may stop it."""
    if not _ID.match(run_id):
        return jsonify({"error": "Invalid run id"}), 400
    run, status = coordinator_request("GET", f"/api/runs/{run_id}")
    if status != 200:
        return jsonify(run), status
    owner = run.get("requested_by") if isinstance(run, dict) else None
    if not g.user.is_admin and not (isinstance(owner, str) and owner and owner == g.user.username):
        return jsonify({"error": "Only the person who started a run, or an admin, can cancel it"}), 403
    payload, status = coordinator_request("POST", f"/api/runs/{run_id}/cancel")
    if status == 200:
        from amplify import grid_data           # local import: grid_data imports this module
        grid_data.clear_cache()                 # the dashboard must not keep showing the run as queued
    return jsonify(payload), status


@bp.route("/api/grid/test-tree")
@require_auth
def grid_test_tree():
    """The repo's tests/ as folders → files → tests, for the New run picker. ?repo= a repo top-level folder; without
    it, the user's active repo. The same folders a run may be snapshotted from (grid_snapshot.inspect_repo rules)."""
    from amplify.user_config_reader import UserConfigReader
    repo = (request.args.get("repo") or "").strip() or str(UserConfigReader(g.user.id).load().get("repo_root") or "")
    if not repo:
        return jsonify({"error": "No repository selected — choose one in the Repository tab"}), 400
    try:
        info = grid_snapshot.inspect_repo(repo, **_snapshot_dirs())
        tree = grid_tests.build_tree(info.top)
    except SnapshotError as exc:
        return jsonify({"error": str(exc)}), exc.status
    except (FileNotFoundError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"repo": info.repo, "path": str(info.top), "sha": info.sha, "dirty": info.dirty, "tree": tree})


@bp.route("/api/grid/pytest-args")
@require_auth
def grid_pytest_args():
    """The extra pytest arguments an admin configured: what the Grid tab's New run form offers."""
    return jsonify({"arguments": grid_args.load_definitions()})


@bp.route("/api/grid/chunks/<chunk_id>")
@require_auth
def grid_chunk(chunk_id):
    if not _ID.match(chunk_id):
        return jsonify({"error": "Invalid chunk id"}), 400
    return _call("GET", f"/api/chunks/{chunk_id}")


def _snapshot_dirs() -> dict:
    """Amplify's data directory is off limits — except this user's own clones, which live there in a container."""
    user_id = getattr(getattr(g, "user", None), "id", None)
    return {"protected_dirs": [settings.DATABASE_PATH.parent, settings.WORKSPACES_ROOT],
            "allowed_dirs": [settings.WORKSPACES_ROOT / str(user_id) / "repos"] if user_id is not None else []}


def _snapshot_for(repo_path) -> tuple[grid_snapshot.RepoInfo, str]:
    """Package the repo's committed state and deliver it. Returns (repo info, code_source); raises SnapshotError."""
    info = grid_snapshot.inspect_repo(repo_path, **_snapshot_dirs())
    if info.subpath:
        # The pods unpack the whole repo and run from its top-level, so test ids listed relative to a
        # sub-folder would not exist there. Say which folder to select rather than run nothing.
        raise SnapshotError(
            f"'{info.subpath}' is a sub-folder of the git repository '{info.top.name}'. The Grid runs the tests "
            f"from the repository's top-level folder, so select '{info.top.name}' on the Executor tab instead "
            "and list the tests relative to it.")
    source = grid_snapshot.deliver(info, code_dir=os.environ.get("GRID_CODE_DIR", "").strip() or None,
                                   has_code=coordinator_has_code, put_code=coordinator_put_code)
    return info, source


_TOO_OLD_FOR_ARGS = ("The Grid coordinator is too old for pytest arguments — update it, "
                     "or remove the arguments in Admin → Grid")


def _require_features(needed: list[tuple[str, str]]):
    """None when the coordinator announces every (feature, message-if-missing) in `needed` (one /api/config call),
    else a (JSON answer, status) to return. An older coordinator would ignore what it does not know: repo/sha (its
    pods would run whatever tests their image holds) or pytest_args (dropped without a word)."""
    data, status = coordinator_request("GET", "/api/config")
    if status != 200 or not isinstance(data, dict):
        reason = data.get("error") if isinstance(data, dict) else None
        return jsonify({"error": f"Could not check the Grid coordinator: {reason or f'HTTP {status}'}"}), 502
    features = data.get("features")
    for feature, too_old in needed:
        if not isinstance(features, list) or feature not in features:
            return jsonify({"error": too_old}), 502
    return None


def _selection(body: dict) -> tuple[dict | None, str | None]:
    """(the coordinator payload, None) for a start-run body, or (None, a message fit for a 400): every field is
    checked for its type here, before anything is packaged or sent."""
    tests = body.get("tests", [])
    if not isinstance(tests, list) or not all(isinstance(t, str) for t in tests):
        return None, "tests must be a list of test ids (text)"
    tests = [t.strip() for t in tests if t.strip()]
    if not tests:
        return None, "Select at least one test"
    environment, browsers = body.get("environment"), body.get("browsers")
    if environment is not None and not isinstance(environment, str):
        return None, "environment must be text"
    environment = (environment or "").strip().lower() or None
    definitions = grid_args.load_definitions()
    configured = grid_args.run_environments(definitions)
    environment = environment or configured["default_environment"] or None    # the run's and --env's value agree
    if environment and configured["environments"] and environment not in configured["environments"]:
        return None, f"Unsupported environment '{environment}'. Choose one of: {configured['environments']}"
    if browsers is not None and (not isinstance(browsers, list) or not all(isinstance(b, str) for b in browsers)):
        return None, "browsers must be a list of browser names (text)"
    chunk_size = DEFAULT_CHUNK_SIZE if body.get("chunk_size") is None else body["chunk_size"]
    low, high = CHUNK_SIZE_RANGE
    if isinstance(chunk_size, bool) or not isinstance(chunk_size, int) or not low <= chunk_size <= high:
        return None, f"chunk_size must be a whole number from {low} to {high}"
    repo_path = body.get("repo_path")
    if repo_path is not None and not isinstance(repo_path, str):
        return None, "repo_path must be a folder path"
    try:
        pytest_args = grid_args.resolve(definitions, body.get("pytest_args"), environment)
    except ValueError as exc:
        return None, str(exc)
    return {"tests": tests, "environment": environment, "browsers": browsers or None,
            "chunk_size": chunk_size, "requested_by": getattr(getattr(g, "user", None), "username", None),
            "pytest_args": pytest_args or None}, None


def _unconfirmed(info: grid_snapshot.RepoInfo) -> str:
    return (f"The coordinator did not confirm {info.repo}@{info.sha[:7]} for this run (it did not send repo and sha "
            "back), so the pods may run the tests built into their image instead. Update the coordinator and the runners.")


@bp.route("/api/grid/runs", methods=["POST"])
@require_auth
def grid_start_run():
    body, bad = json_object()
    if bad:
        return bad
    payload, problem = _selection(body)
    if payload is None:
        return jsonify({"error": problem}), 400
    repo_path = body.get("repo_path")
    wants_code = bool(repo_path and repo_path.strip())
    info = source = None
    if wants_code and not _coordinator_url():            # nothing to deliver to: don't package the repo for nothing
        return jsonify({"error": _NOT_CONFIGURED, "configured": False}), 503
    needed = ([(CODE_DELIVERY, _COORDINATOR_TOO_OLD)] if wants_code else []) + \
        ([(PYTEST_ARGS, _TOO_OLD_FOR_ARGS)] if payload["pytest_args"] else [])
    if needed:
        refused = _require_features(needed)
        if refused:
            return refused
    if wants_code:
        try:
            info, source = _snapshot_for(repo_path)
        except SnapshotError as exc:
            return jsonify({"error": str(exc)}), exc.status
        payload["repo"], payload["sha"] = info.repo, info.sha
    # Let the coordinator apply its own defaults for anything left unset.
    data, status = coordinator_request("POST", "/api/runs", body={k: v for k, v in payload.items() if v is not None})
    if info is not None and status < 300:
        if not isinstance(data, dict):
            return jsonify({"error": "The coordinator's answer was not understood"}), 502
        confirmed = data.get("repo") == info.repo and data.get("sha") == info.sha       # the run really carries them
        data = {**data, "repo": info.repo, "sha": info.sha, "dirty": info.dirty, "code_source": source}
        if not confirmed:
            data["warning"] = _unconfirmed(info)
    return jsonify(data), status
