"""Pod token vs full token: role resolution, the endpoint-to-role table, and the 401/403 behaviour.

The role table is read from the real FastAPI route table (no database). The behaviour tests send
requests through the real app over in-memory SQLite (see pod_scope_helpers.py).
"""
import json
import re

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

import schemas
from pod_scope_helpers import FULL_TOKEN, POD_TOKEN, SHA, call

UNAUTHORIZED = {"detail": "Missing or invalid X-API-Token"}
FORBIDDEN = {"detail": "This token is not allowed to call that endpoint"}

# The contract: what a runner pod legitimately calls. Everything else under /api is full-token only.
POD_ENDPOINTS = {
    ("GET", "/api/runner/lease"),
    ("POST", "/api/results/submit"),
    ("PUT", "/api/chunks/{chunk_id}/allure"),
    ("POST", "/api/chunks/{chunk_id}/release"),
    ("POST", "/api/runners/{runner_id}/heartbeat"),
    ("POST", "/api/runners/{runner_id}/exit"),
    ("GET", "/api/code/{repo}/{sha}"),
    ("HEAD", "/api/code/{repo}/{sha}"),
}
FULL_ONLY_ENDPOINTS = {
    ("GET", "/api/config"),
    ("POST", "/api/queue/push"),
    ("POST", "/api/runs"),
    ("GET", "/api/runs"),
    ("GET", "/api/runs/{run_id}"),
    ("GET", "/api/chunks/{chunk_id}"),
    ("GET", "/api/pods"),
    ("GET", "/api/results/summary"),
    ("PUT", "/api/code/{repo}/{sha}"),
    ("GET", "/api/runs/{run_id}/report/info"),
    ("GET", "/api/runs/{run_id}/report"),
}
OPEN_ENDPOINTS = {("GET", "/health"), ("GET", "/ready")}


@pytest.fixture(autouse=True)
def tokens(monkeypatch):
    monkeypatch.setattr(schemas, "API_TOKEN", FULL_TOKEN)
    monkeypatch.setattr(schemas, "POD_API_TOKEN", POD_TOKEN)


# --- token role resolution ---------------------------------------------------------------
@pytest.mark.parametrize(
    "api, pod, sent, role",
    [
        ("", "", None, "full"),                    # auth disabled: everything passes as today
        ("", "", "anything", "full"),
        ("", POD_TOKEN, None, "full"),             # ... even when a pod token is configured
        (FULL_TOKEN, "", FULL_TOKEN, "full"),      # POD_API_TOKEN unset: pods use the full token
        (FULL_TOKEN, "", POD_TOKEN, None),         # ... and a pod token means nothing
        (FULL_TOKEN, "", "", None),                # an empty header never matches an unset pod token
        (FULL_TOKEN, "", None, None),
        (FULL_TOKEN, POD_TOKEN, FULL_TOKEN, "full"),
        (FULL_TOKEN, POD_TOKEN, POD_TOKEN, "pod"),
        (FULL_TOKEN, POD_TOKEN, None, None),
        (FULL_TOKEN, POD_TOKEN, "", None),
        (FULL_TOKEN, POD_TOKEN, "wrong", None),
        (FULL_TOKEN, POD_TOKEN, POD_TOKEN + "x", None),
        (FULL_TOKEN, POD_TOKEN, POD_TOKEN.upper(), None),
        (FULL_TOKEN, POD_TOKEN, "pod-yyý", None),  # non-ASCII must be a refusal, not a TypeError
        (FULL_TOKEN, FULL_TOKEN, FULL_TOKEN, "full"),  # POD == API is simply the full token
        (FULL_TOKEN, FULL_TOKEN, "wrong", None),
    ],
)
def test_token_role(monkeypatch, api, pod, sent, role):
    monkeypatch.setattr(schemas, "API_TOKEN", api)
    monkeypatch.setattr(schemas, "POD_API_TOKEN", pod)
    assert schemas.token_role(sent) == role


def test_tokens_are_compared_in_constant_time_over_bytes(monkeypatch):
    seen = []
    real = schemas.secrets.compare_digest

    def spy(a, b):
        seen.append((type(a), type(b)))
        return real(a, b)

    monkeypatch.setattr(schemas.secrets, "compare_digest", spy)
    assert schemas.token_role("wrong") is None
    assert seen == [(bytes, bytes), (bytes, bytes)]  # both comparisons run, on bytes
    seen.clear()
    assert schemas.token_role(FULL_TOKEN) == "full"
    assert len(seen) == 2


@pytest.mark.parametrize("sent, outcome", [(POD_TOKEN, None), (FULL_TOKEN, None), (None, 401), ("wrong", 401)])
def test_require_token_accepts_either_credential(sent, outcome):
    if outcome is None:
        assert schemas.require_token(sent) is None
        return
    with pytest.raises(HTTPException) as exc:
        schemas.require_token(sent)
    assert (exc.value.status_code, exc.value.detail) == (401, UNAUTHORIZED["detail"])


@pytest.mark.parametrize(
    "sent, status",
    [(FULL_TOKEN, None), (POD_TOKEN, 403), (None, 401), ("", 401), ("wrong", 401), ("sécret", 401)],
)
def test_require_full_token_refuses_the_pod_token_with_403_and_the_rest_with_401(sent, status):
    if status is None:
        assert schemas.require_full_token(sent) is None
        return
    with pytest.raises(HTTPException) as exc:
        schemas.require_full_token(sent)
    expected = FORBIDDEN if status == 403 else UNAUTHORIZED
    assert (exc.value.status_code, exc.value.detail) == (status, expected["detail"])


def test_with_authentication_disabled_both_dependencies_pass(monkeypatch):
    monkeypatch.setattr(schemas, "API_TOKEN", "")
    assert schemas.require_token(None) is None
    assert schemas.require_full_token(None) is None
    assert schemas.require_full_token(POD_TOKEN) is None


# --- endpoint-to-role table (from the real route table) ---------------------------------------------
def _role_table(app):
    table = {}
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        calls = {d.call for d in route.dependant.dependencies}
        pod, full = schemas.require_token in calls, schemas.require_full_token in calls
        role = "both" if pod and full else "pod" if pod else "full" if full else "open"
        for method in route.methods:
            table[(method, route.path)] = role
    return table


def test_exactly_the_contracted_endpoints_accept_the_pod_token(coordinator):
    table = _role_table(coordinator.app)
    assert {k for k, role in table.items() if role == "pod"} == POD_ENDPOINTS


def test_every_other_api_endpoint_is_full_token_only(coordinator):
    table = _role_table(coordinator.app)
    api = {k: role for k, role in table.items() if k[1].startswith("/api/")}
    assert {k for k, role in api.items() if role == "full"} == FULL_ONLY_ENDPOINTS
    assert set(api) == POD_ENDPOINTS | FULL_ONLY_ENDPOINTS      # nothing unclassified, nothing doubled
    assert "both" not in table.values()


def test_only_health_and_ready_stay_open(coordinator):
    table = _role_table(coordinator.app)
    assert {k for k, role in table.items() if role == "open"} == OPEN_ENDPOINTS


# --- behaviour through the real app ------------------------------------------------------------------
def _fill(path: str) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: SHA if m.group(1) == "sha" else "x", path)


def _json_headers():
    return {"Content-Type": "application/json"}


ALL_API = sorted(POD_ENDPOINTS | FULL_ONLY_ENDPOINTS)


@pytest.mark.parametrize("method, path", sorted(FULL_ONLY_ENDPOINTS))
def test_the_pod_token_gets_403_on_every_full_only_endpoint(grid, method, path):
    status, body = call(grid.app, method, _fill(path), token=POD_TOKEN, extra_headers=_json_headers())
    assert (status, body) == (403, FORBIDDEN)


@pytest.mark.parametrize("method, path", ALL_API)
@pytest.mark.parametrize("sent", [None, "", "wrong", "sécret"])
def test_a_missing_or_invalid_token_is_401_everywhere_exactly_as_before(grid, method, path, sent):
    status, body = call(grid.app, method, _fill(path), token=sent, extra_headers=_json_headers())
    assert status == 401
    if method != "HEAD":
        assert body == UNAUTHORIZED


SUBMIT = json.dumps({"chunk_id": "nope", "runner_id": "w1", "results": []}).encode()
# Pod endpoint calls that get past authentication and answer from the endpoint itself.
POD_CALLS = [
    ("GET", "/api/runner/lease", "runner_id=w1&browser=chrome&lease_tokens=1", b"", 404),
    ("POST", "/api/results/submit", "", SUBMIT, 404),
    ("PUT", "/api/chunks/nope/allure", "", b"", 404),
    ("POST", "/api/chunks/nope/release", "runner_id=w1", b"", 200),
    ("POST", "/api/runners/w1/heartbeat", "", b"", 200),
    ("POST", "/api/runners/w1/exit", "", b"", 200),
    ("GET", f"/api/code/demo/{SHA}", "", b"", 404),
    ("HEAD", f"/api/code/demo/{SHA}", "", b"", 404),
]


def test_the_pod_call_table_covers_every_pod_endpoint():
    for method, template in POD_ENDPOINTS:
        pattern = re.sub(r"\{\w+\}", "[^/]+", template)
        assert any(m == method and re.fullmatch(pattern, p) for m, p, *_ in POD_CALLS), (method, template)


@pytest.mark.parametrize("token", [POD_TOKEN, FULL_TOKEN])
@pytest.mark.parametrize("method, path, query, body, expected", POD_CALLS)
def test_both_tokens_open_every_pod_endpoint(grid, coordinator, monkeypatch, token, method, path, query, body,
                                             expected):
    monkeypatch.setattr(coordinator, "lease_next_chunk", lambda *a, **k: None)  # PostgreSQL-only SQL
    status, _ = call(grid.app, method, path, token=token, query=query, body=body, extra_headers=_json_headers())
    assert status == expected


def test_the_full_token_opens_every_full_only_endpoint(grid):
    for method, path in sorted(FULL_ONLY_ENDPOINTS):
        status, _ = call(grid.app, method, _fill(path), token=FULL_TOKEN, extra_headers=_json_headers())
        assert status not in (401, 403), (method, path, status)
    assert call(grid.app, "GET", "/api/config", token=FULL_TOKEN)[0] == 200
    assert call(grid.app, "GET", "/api/runs", token=FULL_TOKEN) == (200, {"runs": []})


def test_health_and_ready_need_no_token(grid):
    assert call(grid.app, "GET", "/health") == (200, {"status": "healthy"})
    assert call(grid.app, "GET", "/ready") == (200, {"status": "ready"})


def test_without_a_pod_token_configured_pods_use_the_full_token_as_before(grid, monkeypatch):
    monkeypatch.setattr(schemas, "POD_API_TOKEN", "")
    assert call(grid.app, "POST", "/api/runners/w1/heartbeat", token=FULL_TOKEN)[0] == 200
    assert call(grid.app, "GET", "/api/runs", token=FULL_TOKEN)[0] == 200
    assert call(grid.app, "POST", "/api/runners/w1/heartbeat", token=POD_TOKEN) == (401, UNAUTHORIZED)


def test_a_pod_token_equal_to_the_full_token_is_the_full_token(grid, monkeypatch):
    monkeypatch.setattr(schemas, "POD_API_TOKEN", FULL_TOKEN)
    assert call(grid.app, "GET", "/api/runs", token=FULL_TOKEN) == (200, {"runs": []})


def test_with_authentication_disabled_no_token_is_needed_anywhere(grid, monkeypatch):
    monkeypatch.setattr(schemas, "API_TOKEN", "")
    assert call(grid.app, "GET", "/api/runs") == (200, {"runs": []})
    assert call(grid.app, "POST", "/api/runners/w1/heartbeat")[0] == 200
    assert call(grid.app, "GET", "/api/runs", token=POD_TOKEN)[0] == 200
