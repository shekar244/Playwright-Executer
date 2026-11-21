import importlib.util
import io
import json
import os
import re
import signal
import socket
import sys
import tarfile
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import quote

import requests

import chunk_runner
import code_download
import lease_token as leasing
from tar_guard import MAX_TAR_BYTES, MAX_TAR_MEMBERS, safe_extract_tar  # noqa: F401  (re-exported)

COORDINATOR_URL = os.getenv("COORDINATOR_URL", "http://central-coordinator-svc:8000")
# The pod name on Kubernetes/OpenShift (hostname). RUNNER_ID overrides it for local/multi-runner testing.
RUNNER_ID = os.getenv("RUNNER_ID") or socket.gethostname()
# Browser this pod runs (set by the image; must match gridtest.browsers). The runner only
# leases chunks that were queued for this browser.
BROWSER = os.getenv("BROWSER", "edge").lower()
MAX_PARALLEL = 3
HEARTBEAT_SECONDS = 15
LOG_LIMIT = 20000  # keep stored pytest output bounded

HEADERS = {"X-API-Token": os.environ["GRID_API_TOKEN"]} if os.getenv("GRID_API_TOKEN") else {}

# Code delivery: a run carries (repo, sha); the pod runs the leased tests from that snapshot.
# Shared rules, identical in the coordinator and Amplify: validate BEFORE building any path.
REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
CODE_MAX_BYTES = 100 * 1024 * 1024  # largest snapshot tarball accepted from the coordinator
ALLURE_UPLOAD_BACKOFF = (2, 5)  # seconds slept between the upload attempts
_ATTACHMENT_RE = re.compile(r"-attachment(\..*)?$")


def _code_root() -> str:
    return os.getenv("GRID_CODE_ROOT", "/code")  # shared read-only volume: <root>/<repo>/<sha>/


def _work_dir() -> Path:
    return Path(os.getenv("GRID_WORK_DIR", "/tmp/grid-work"))


def _allure_max_bytes() -> int:
    try:
        return int(os.getenv("GRID_ALLURE_MAX_BYTES", "52428800"))
    except ValueError:
        return 52428800


def _log(message: str) -> None:
    print(f"[{RUNNER_ID}] {message}")


def _tail(text: str) -> str:
    return text if len(text) <= LOG_LIMIT else "...[truncated]...\n" + text[-LOG_LIMIT:]


def _printable(value, limit: int = 100) -> str:
    """A repo/sha from the network, made safe to print or send back in a message."""
    return re.sub(r"[^A-Za-z0-9._-]", "?", str(value))[:limit]


# ---------------------------------------------------------------- code delivery

def _volume_snapshot(repo: str, sha: str, root: str) -> Path | None:
    """<root>/<repo>/<sha>/ when it is a directory that really lives inside root."""
    try:
        base = Path(root).resolve()
        candidate = (base / repo / sha).resolve()
        if candidate.is_dir() and candidate.is_relative_to(base) and candidate != base:
            return candidate
    except (OSError, RuntimeError):
        pass
    return None


def _download_snapshot(repo: str, sha: str, work_dir: Path) -> Path | None:
    """A FRESH directory with the checksum-verified snapshot from the coordinator (see code_download)."""
    return code_download.fetch_snapshot(repo, sha, work_dir, base_url=COORDINATOR_URL, headers=HEADERS,
                                        max_bytes=CODE_MAX_BYTES, log=_log)


def resolve_code(repo, sha) -> Path | None:
    """Directory holding the snapshot of repo@sha, or None when it cannot be found or trusted.

    Order: the shared read-only volume (GRID_CODE_ROOT/<repo>/<sha>/), then a checked
    download from the coordinator, unpacked into a fresh directory under GRID_WORK_DIR that the
    caller removes after the chunk (code_download.discard_snapshot). Never raises.
    """
    if not (isinstance(repo, str) and REPO_RE.fullmatch(repo) and isinstance(sha, str) and SHA_RE.fullmatch(sha)):
        _log(f"Refusing invalid code reference {_printable(repo)}@{_printable(sha, 7)}.")
        return None
    found = _volume_snapshot(repo, sha, _code_root())
    if found:
        _log(f"Using {repo}@{sha[:7]} from the shared volume.")
        return found
    try:
        found = _download_snapshot(repo, sha, _work_dir())
    except Exception as e:  # network, disk: the chunk is reported as failed, the runner carries on
        _log(f"Could not fetch code for {repo}@{sha[:7]}: {type(e).__name__}")
        return None
    if found:
        _log(f"Using {repo}@{sha[:7]} downloaded from the coordinator.")
    return found


# ---------------------------------------------------------------- running pytest

def framework_src_dir() -> str | None:
    """The gridtest package directory's parent: /app/src in the image, <project>/src in a checkout."""
    here = Path(__file__).resolve().parent
    for candidate in (here / "src", here.parent / "src"):
        if (candidate / "gridtest" / "__init__.py").is_file():
            return str(candidate)
    return None


def allure_available() -> bool:
    return importlib.util.find_spec("allure_pytest") is not None


def build_test_env(environment: str, results_file: str, snapshot: bool = False) -> dict:
    """Environment for the pytest process: the runner's, minus the grid credentials."""
    env = dict(os.environ)
    for secret in ("GRID_API_TOKEN", "COORDINATOR_URL"):
        env.pop(secret, None)  # test code must not see (or leak) the grid's token and address
    if environment:
        env["TEST_ENV"] = environment
    env["GRID_RESULTS_FILE"] = results_file
    env["GRID_POD"] = RUNNER_ID
    env["BROWSER"] = BROWSER
    src = framework_src_dir()
    if src:  # gridtest must import from any cwd (the snapshot has no grid conftest)
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [src, env.get("PYTHONPATH")]))
    if snapshot:
        env["PYTHONDONTWRITEBYTECODE"] = "1"  # the snapshot may be a read-only volume
    return env


def build_pytest_args(tests: list[str], snapshot: bool = False, alluredir: str | None = None,
                      cache_dir: str | None = None) -> list[str]:
    # Execute up to 3 tests concurrently in separate browser instances via pytest-xdist
    runner_count = min(len(tests), MAX_PARALLEL)
    args = [sys.executable, "-m", "pytest", "-v", f"-n={runner_count}"]
    if snapshot:
        # The repo needs no grid conftest: the plugins come from the framework on PYTHONPATH.
        args += ["-p", "gridtest.results_plugin", "-p", "gridtest.allure_labels"]
    if cache_dir:
        args += ["-o", f"cache_dir={cache_dir}"]  # .pytest_cache stays out of /app and the snapshot (read-only)
    if snapshot and alluredir and allure_available():
        args.append(f"--alluredir={alluredir}")
    # "--" keeps every test id a positional argument, never a pytest option.
    return args + ["--"] + tests


def _chunk_result(tests, environment, success, duration, test_results, stdout, stderr) -> dict:
    return {
        "tests": tests,
        "environment": environment,
        "browser": BROWSER,
        "success": success,
        "duration_seconds": duration,
        "test_results": test_results,
        "stdout": _tail(stdout),
        "stderr": _tail(stderr),
    }


def _run_chunk(tests, environment, snapshot, cwd, allure_dir) -> dict:
    """pytest for one chunk under GRID_CHUNK_TIMEOUT_SECONDS; a timeout is a failed chunk, not a crash."""
    with tempfile.TemporaryDirectory() as tmp:
        results_file = os.path.join(tmp, "results.json")
        env = build_test_env(environment, results_file, snapshot)
        args = build_pytest_args(tests, snapshot, allure_dir, os.path.join(tmp, "cache"))
        timeout = chunk_runner.chunk_timeout_seconds()

        start_time = time.time()
        try:
            outcome = chunk_runner.run_pytest(args, env, cwd, timeout)
        except OSError as e:  # e.g. the snapshot disappeared; report it instead of dying
            return _chunk_result(tests, environment, False, 0.0, [], "", f"Could not start pytest ({type(e).__name__})")
        duration = round(time.time() - start_time, 2)

        try:
            with open(results_file) as f:
                test_results = json.load(f)
        except (OSError, ValueError):
            test_results = []  # pytest died before writing (collection error, crash, timeout)

    if outcome.timed_out:
        _log(f"{chunk_runner.timeout_message(timeout)}; its process group was stopped.")
        stderr = chunk_runner.timeout_stderr(timeout, outcome.stderr, LOG_LIMIT)
        return _chunk_result(tests, environment, False, duration, test_results, outcome.stdout, stderr)
    return _chunk_result(tests, environment, outcome.returncode == 0, duration, test_results,
                         outcome.stdout, outcome.stderr)


def run_assigned_tests(tests: list[str], environment: str = "", repo: str | None = None,
                       sha: str | None = None, allure_dir: str | None = None) -> list[dict]:
    """Run one chunk. Without repo/sha: the tests baked into the image, from the working directory.

    With repo/sha: from that snapshot (cwd = the snapshot) and, when allure_dir is given and
    allure-pytest is installed, raw Allure results are written to allure_dir for the caller.
    A snapshot downloaded from the coordinator is deleted again once the chunk is done.
    """
    snapshot = bool(repo or sha)
    cwd = None
    if snapshot:
        cwd = resolve_code(repo, sha)
        if cwd is None:
            message = (f"Code for {_printable(repo)}@{_printable(sha, 7)} is not available "
                       f"(looked in {_code_root()} and on the coordinator)")
            return [_chunk_result(tests, environment, False, 0.0, [], "", message)]
        if allure_dir:
            os.makedirs(allure_dir, exist_ok=True)
    try:
        return [_run_chunk(tests, environment, snapshot, cwd, allure_dir)]
    finally:
        if cwd is not None:
            code_download.discard_snapshot(cwd, _work_dir())  # never reused; a volume path is left alone


# ---------------------------------------------------------------- Allure results upload

class _TooBig(Exception):
    pass


class _CappedBuffer(io.BytesIO):
    """In-memory archive that refuses to grow past a cap, so a huge result set fails fast."""

    def __init__(self, cap: int):
        super().__init__()
        self.cap = cap

    def write(self, data):
        if self.tell() + len(data) > self.cap:
            raise _TooBig
        return super().write(data)


def _tar_gz(directory: str, max_bytes: int, skip_attachments: bool) -> bytes | None:
    root, buf, count = Path(directory), _CappedBuffer(max_bytes), 0
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path in sorted(root.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            if skip_attachments and _ATTACHMENT_RE.search(path.name):
                continue
            info = tar.gettarinfo(str(path), arcname=path.relative_to(root).as_posix())
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mode = 0o644
            with open(path, "rb") as f:
                tar.addfile(info, f)
            count += 1
    return buf.getvalue() if count else None


def pack_allure(directory: str, max_bytes: int) -> bytes | None:
    """tar.gz of the CONTENTS of the Allure results directory, or None when there is nothing to send.

    Over max_bytes it is rebuilt without the *-attachment.* files; still too big: skipped.
    """
    if not os.path.isdir(directory):
        _log("No Allure results directory; nothing to upload.")
        return None
    for skip_attachments in (False, True):
        try:
            data = _tar_gz(directory, max_bytes, skip_attachments)
        except _TooBig:
            if not skip_attachments:
                _log(f"Allure results exceed {max_bytes} bytes; retrying without attachments.")
                continue
            _log(f"Allure results exceed {max_bytes} bytes even without attachments; skipping the upload.")
            return None
        if data is None:
            _log("No Allure results produced; nothing to upload.")
        return data
    return None


def upload_allure(chunk_id: str, data: bytes, attempts: int = 3, backoff=None,
                  lease_token: str | None = None) -> bool:
    """PUT the packed results to the coordinator, retrying with backoff. Never raises."""
    backoff = ALLURE_UPLOAD_BACKOFF if backoff is None else backoff
    url = f"{COORDINATOR_URL}/api/chunks/{quote(str(chunk_id), safe='')}/allure"
    headers = {**leasing.with_token(HEADERS, lease_token), "Content-Type": "application/gzip"}
    for attempt in range(1, attempts + 1):
        try:
            resp = requests.put(url, data=data, headers=headers, timeout=(10, 120), allow_redirects=False)
            if resp.ok:
                _log(f"Uploaded Allure results for {chunk_id} ({len(data)} bytes).")
                return True
            _log(f"Allure upload attempt {attempt}/{attempts} failed: HTTP {resp.status_code}")
            if resp.status_code in (400, 401, 403, 404, 413):
                return False  # retrying cannot help
        except requests.exceptions.RequestException as e:
            _log(f"Allure upload attempt {attempt}/{attempts} failed: {type(e).__name__}")
        if attempt < attempts and backoff:
            time.sleep(backoff[min(attempt - 1, len(backoff) - 1)])
    _log(f"Giving up on the Allure upload for {chunk_id}; results were already submitted.")
    return False


def publish_allure(chunk_id: str, allure_dir: str, lease_token: str | None = None) -> None:
    """Best effort: pack and upload this chunk's Allure results. Problems here never fail the chunk."""
    try:
        data = pack_allure(allure_dir, _allure_max_bytes())
        if data:
            upload_allure(chunk_id, data, lease_token=lease_token)
    except Exception as e:
        _log(f"Allure upload skipped for {chunk_id}: {type(e).__name__}: {e}")


# ---------------------------------------------------------------- coordinator protocol

def start_heartbeat() -> threading.Event:
    """Tell the coordinator this pod is alive, including while a long chunk is running."""
    stop = threading.Event()

    def beat():
        while not stop.wait(HEARTBEAT_SECONDS):
            try:
                requests.post(
                    f"{COORDINATOR_URL}/api/runners/{RUNNER_ID}/heartbeat", headers=HEADERS, timeout=5
                )
            except requests.exceptions.RequestException:
                pass  # best effort; the next beat will try again

    threading.Thread(target=beat, daemon=True).start()
    return stop


def release_chunk(chunk_id: str, lease_token: str | None = None) -> None:
    """Hand an unfinished chunk back to the queue so another pod runs it."""
    try:
        resp = requests.post(
            f"{COORDINATOR_URL}/api/chunks/{chunk_id}/release",
            params={"runner_id": RUNNER_ID},
            headers=leasing.with_token(HEADERS, lease_token),
            timeout=5,
        )
        resp.raise_for_status()
        outcome = resp.json().get("status")
        print(f"[{RUNNER_ID}] Release of {chunk_id}: {outcome}.")
    except (requests.exceptions.RequestException, ValueError) as e:
        # The coordinator's reaper requeues it once this pod stops heartbeating.
        print(f"[{RUNNER_ID}] Could not release {chunk_id}: {e}")


def report_exit() -> None:
    try:
        requests.post(f"{COORDINATOR_URL}/api/runners/{RUNNER_ID}/exit", headers=HEADERS, timeout=5)
    except requests.exceptions.RequestException:
        pass


def submit_results(chunk_id: str, results: list[dict], lease_token: str | None = None) -> bool:
    """True once the coordinator recorded the results; False when it refuses them (HTTP 403).

    403 means this pod no longer holds the chunk (released, requeued or leased again, so the lease
    secret is stale): the results are dropped, without retries. Other failures are retried and,
    when they persist, raise RuntimeError.
    """
    headers = leasing.with_token(HEADERS, lease_token)
    # Retry so a transient coordinator blip does not strand a completed chunk as "running".
    for attempt in range(1, 4):
        try:
            resp = requests.post(
                f"{COORDINATOR_URL}/api/results/submit",
                json={"chunk_id": chunk_id, "runner_id": RUNNER_ID, "results": results},
                headers=headers,
                timeout=10,
            )
            if resp.status_code == 403:
                print(f"[{RUNNER_ID}] Results for {chunk_id} were rejected ({leasing.rejection(resp, lease_token)}); "
                      "this pod no longer holds the chunk. Dropping them.")
                return False
            resp.raise_for_status()
            print(f"[{RUNNER_ID}] Submitted results for {chunk_id}.")
            return True
        except requests.exceptions.RequestException as e:
            print(f"[{RUNNER_ID}] Submit attempt {attempt}/3 failed: {e}")
            time.sleep(3)
    raise RuntimeError(f"Could not submit results for {chunk_id}")


def poll_and_execute():
    print(f"[{RUNNER_ID}] Runner initialized ({BROWSER}). Polling {COORDINATOR_URL}...")
    heartbeat_stop = start_heartbeat()
    in_flight = {"chunk_id": None, "lease_token": None}  # the chunk held right now, and its lease secret

    def on_sigterm(signum, frame):
        # Kubernetes sends SIGTERM on eviction / scale-in / rollout. Unwinding via SystemExit
        # also kills the running pytest subprocess; the finally block requeues the chunk.
        print(f"[{RUNNER_ID}] SIGTERM received; shutting down.")
        raise SystemExit(143)

    signal.signal(signal.SIGTERM, on_sigterm)
    try:
        while True:
            try:
                resp = requests.get(
                    f"{COORDINATOR_URL}/api/runner/lease",
                    params={"runner_id": RUNNER_ID, "browser": BROWSER, "lease_tokens": 1},
                    headers=HEADERS,
                    timeout=10,
                )
                if resp.status_code == 404:
                    print(f"[{RUNNER_ID}] Queue empty. Terminating runner pod cleanly.")
                    break

                resp.raise_for_status()
                data = resp.json()
                chunk_id = data["chunk_id"]
                tests = data["tests"]
                environment = data.get("environment", "")
                repo, sha = data.get("repo"), data.get("sha")  # null for runs that carry no code reference
                code = f", code={_printable(repo)}@{_printable(sha, 7)}" if repo or sha else ""
                token = leasing.accept(data.get("lease_token"), _log)  # None from an old coordinator

                print(f"[{RUNNER_ID}] Leased {chunk_id} (env={environment}{code}): {tests}")
                in_flight.update(chunk_id=chunk_id, lease_token=token)
                with tempfile.TemporaryDirectory(prefix="grid-allure-") as allure_dir:
                    results = run_assigned_tests(tests, environment, repo, sha,
                                                 allure_dir if (repo or sha) else None)
                    accepted = submit_results(chunk_id, results, token)
                    in_flight.update(chunk_id=None, lease_token=None)
                    if accepted and (repo or sha):  # after the results: never delays or fails the chunk
                        publish_allure(chunk_id, allure_dir, token)

            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
                print(f"[{RUNNER_ID}] Coordinator unreachable or slow. Retrying in 3s...")
                time.sleep(3)
            except Exception as e:
                print(f"[{RUNNER_ID}] Unexpected error: {e}")
                sys.exit(1)
    finally:
        heartbeat_stop.set()
        if in_flight["chunk_id"]:
            release_chunk(in_flight["chunk_id"], in_flight["lease_token"])
        report_exit()


if __name__ == "__main__":
    poll_and_execute()
