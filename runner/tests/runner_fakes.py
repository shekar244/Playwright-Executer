"""Test doubles for the runner: a tar builder and a tiny fake coordinator (local http.server thread)."""
import hashlib
import hmac
import io
import json
import re
import tarfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

REPO = "snap-repo"
SHA = "a" * 40
TOKEN = "0123456789abcdef0123456789abcdef"  # a lease secret, as secrets.token_hex(16) makes them
OTHER = "fedcba9876543210fedcba9876543210"
CHUNK = "run1-chromium-000"


def make_lease(chunk_id=CHUNK, repo=REPO, sha=SHA, tests=("tests/test_x.py::test_ok",)) -> dict:
    return {"chunk_id": chunk_id, "run_id": "run1", "tests": list(tests), "environment": "qa",
            "browser": "chromium", "repo": repo, "sha": sha}


def make_tar(path: Path, files: dict) -> bytes:
    """Write a .tar.gz of {name: bytes} (git archive layout, no top-level folder); return its bytes."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, content in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(content))
    Path(path).write_bytes(buf.getvalue())
    return buf.getvalue()


class FakeCoordinator:
    """Just enough of the coordinator API for the runner, on an ephemeral local port."""

    def __init__(self):
        self.code = {}  # (repo, sha) -> (bytes, X-Content-SHA256 header value or None)
        self.head_checksums = {}  # (repo, sha) -> what HEAD announces instead of the GET header
        self.leases = []  # handed out in order by /api/runner/lease, then 404
        self.allure_statuses = []  # answers for chunk allure PUTs, consumed in order, then 200
        self.requests = []  # (method, path, headers, body)
        self.events = []  # ("submit", payload) / ("allure", chunk_id, body) in arrival order, accepted ones only
        self.queries = []  # (method, path, parsed query string) of every request, parallel to `requests`
        # Lease secrets, as the real coordinator does them: a lease request WITH lease_tokens=1 for a chunk in
        # lease_tokens gets that secret in the response and from then on submit/allure/release of the chunk need
        # it as X-Lease-Token (403 otherwise). A lease without the parameter is neither given nor enforced.
        self.lease_tokens = {}  # chunk_id -> secret handed out on lease
        self.required = {}  # chunk_id -> secret enforced (set on lease; a test may overwrite it: a stale pod)
        self.rejected = []  # (method, path) of the requests refused for a missing or wrong lease secret
        self.releases = []  # (chunk_id, runner_id, X-Lease-Token header or None) of the accepted releases
        self.submit_statuses = []  # answers for results submits, consumed in order, then 200
        coordinator = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _body(self):
                return self.rfile.read(int(self.headers.get("Content-Length") or 0))

            def _reply(self, status, payload=b"{}", headers=None):
                if not isinstance(payload, bytes):
                    payload = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(payload)))
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                if self.command != "HEAD":  # a HEAD reply carries the headers (Content-Length too) but no body
                    self.wfile.write(payload)

            def _lease_secret_ok(self, chunk_id):
                need = coordinator.required.get(chunk_id)
                return need is None or hmac.compare_digest((self.headers.get("X-Lease-Token") or "").encode(),
                                                           str(need).encode())

            def _handle(self, method):
                body = self._body()
                url = urlsplit(self.path)
                path, query = url.path, parse_qs(url.query)
                coordinator.requests.append((method, path, dict(self.headers), body))
                coordinator.queries.append((method, path, query))
                code = re.fullmatch(r"/api/code/([^/]+)/([^/]+)", path)
                allure = re.fullmatch(r"/api/chunks/([^/]+)/allure", path)
                release = re.fullmatch(r"/api/chunks/([^/]+)/release", path)
                written = None  # the chunk a write request is about
                if method == "PUT" and allure:
                    written = allure.group(1)
                elif method == "POST" and release:
                    written = release.group(1)
                elif method == "POST" and path == "/api/results/submit":
                    written = json.loads(body).get("chunk_id")
                if written is not None and not self._lease_secret_ok(written):
                    coordinator.rejected.append((method, path))
                    return self._reply(403, {"detail": "Invalid lease token for this chunk"})
                if method in ("GET", "HEAD") and code:
                    entry = coordinator.code.get(code.groups())
                    if entry is None:
                        return self._reply(404, {"detail": "not found"})
                    data, checksum = entry
                    if method == "HEAD" and code.groups() in coordinator.head_checksums:
                        checksum = coordinator.head_checksums[code.groups()]
                    headers = {"Content-Type": "application/gzip"}
                    if checksum is not None:
                        headers["X-Content-SHA256"] = checksum
                    return self._reply(200, data, headers)
                if method == "PUT" and allure:
                    coordinator.events.append(("allure", allure.group(1), body))
                    status = coordinator.allure_statuses.pop(0) if coordinator.allure_statuses else 200
                    return self._reply(status, {"status": "stored", "bytes": len(body)})
                if method == "GET" and path == "/api/runner/lease":
                    if coordinator.leases:
                        lease = dict(coordinator.leases.pop(0))
                        secret = coordinator.lease_tokens.get(lease["chunk_id"])
                        if secret is not None and query.get("lease_tokens") == ["1"]:
                            lease["lease_token"] = secret
                            coordinator.required.setdefault(lease["chunk_id"], secret)
                        return self._reply(200, lease)
                    return self._reply(404, {"detail": "empty"})
                if method == "POST" and path == "/api/results/submit":
                    if coordinator.submit_statuses:
                        return self._reply(coordinator.submit_statuses.pop(0), {"detail": "scripted"})
                    coordinator.events.append(("submit", json.loads(body)))
                    return self._reply(200, {"status": "recorded"})
                if method == "POST" and release:
                    chunk_id = release.group(1)
                    coordinator.releases.append((chunk_id, query.get("runner_id", [None])[0],
                                                 self.headers.get("X-Lease-Token")))
                    coordinator.required.pop(chunk_id, None)  # back to pending: the secret is gone
                    return self._reply(200, {"status": "released"})
                return self._reply(200)  # heartbeat, exit

            def do_GET(self):
                self._handle("GET")

            def do_HEAD(self):
                self._handle("HEAD")

            def do_PUT(self):
                self._handle("PUT")

            def do_POST(self):
                self._handle("POST")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()

    def serve_code(self, repo, sha, tar_bytes, checksum="auto", head_checksum=None):
        """Serve a snapshot. head_checksum, when given, is what HEAD announces (GET keeps `checksum`)."""
        if checksum == "auto":
            checksum = hashlib.sha256(tar_bytes).hexdigest()
        self.code[(repo, sha)] = (tar_bytes, checksum)
        if head_checksum is None:
            self.head_checksums.pop((repo, sha), None)
        else:
            self.head_checksums[(repo, sha)] = head_checksum

    def secrets_sent(self) -> dict:
        """(method, path) -> X-Lease-Token for every request that carried the header."""
        sent = {}
        for method, path, headers, _ in self.requests:
            for name, value in headers.items():
                if name.lower() == "x-lease-token":
                    sent[(method, path)] = value
        return sent

    def close(self):
        self.server.shutdown()
        self.server.server_close()
