import base64
import hashlib
import hmac
import io
import json
import urllib.error
import urllib.parse

import pytest

from jira_insights.jira_client import JiraError
from jira_insights.settings import JiraSettings
from jira_insights.zephyr import STATUSES_PATH, ZAPI_BASE, ZephyrCloud, build_qsh, canonical_qs, zephyr_jwt

SETTINGS = JiraSettings("https://acme.atlassian.net", "qa", "tok", True, "AK", "SK", "acct-1")


def _decode(part: str) -> dict:
    return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))


def test_canonical_query_is_sorted_encoded_and_skips_none():
    assert canonical_qs({"versionId": "-1", "projectId": "10 000", "skip": None, "ids": ["b", "a"]}) == \
        "ids=a&ids=b&projectId=10%20000&versionId=-1"
    assert canonical_qs(None) == ""


def test_qsh_matches_the_atlassian_canonical_request():
    expected = hashlib.sha256(b"GET&/public/rest/api/1.0/cycles/search&projectId=1&versionId=-1").hexdigest()
    assert build_qsh("get", "public/rest/api/1.0/cycles/search", {"versionId": "-1", "projectId": "1"}) == expected


def test_jwt_claims_and_signature():
    token = zephyr_jwt("AK", "SK", "acct-1", "GET", STATUSES_PATH, now=1_700_000_000, expires_in=60)
    header, payload, signature = token.split(".")
    claims = _decode(payload)
    assert _decode(header) == {"alg": "HS256", "typ": "JWT"}
    assert (claims["iss"], claims["sub"], claims["iat"], claims["exp"]) == ("AK", "acct-1", 1_700_000_000, 1_700_000_060)
    assert claims["qsh"] == build_qsh("GET", STATUSES_PATH)
    expected = base64.urlsafe_b64encode(
        hmac.new(b"SK", f"{header}.{payload}".encode(), hashlib.sha256).digest()).rstrip(b"=").decode()
    assert signature == expected


class FakeResponse:
    def __init__(self, body):
        self._raw = json.dumps(body).encode()

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_client_signs_requests_and_reports_success():
    seen = []

    def opener(req, timeout=None, context=None):
        seen.append(req)
        return FakeResponse({"1": {"name": "PASS"}, "2": {"name": "FAIL"}})

    message = ZephyrCloud(SETTINGS, opener=opener).test()
    req = seen[0]
    assert req.full_url == ZAPI_BASE + STATUSES_PATH
    assert req.get_header("Authorization").startswith("JWT ")
    assert req.get_header("Zapiaccesskey") == "AK"
    assert "2 execution statuses" in message


def test_client_surfaces_zephyr_errors():
    def opener(req, timeout=None, context=None):
        raise urllib.error.HTTPError(req.full_url, 401, "no", {}, io.BytesIO(b'{"message": "Invalid JWT"}'))

    with pytest.raises(JiraError) as err:
        ZephyrCloud(SETTINGS, opener=opener).test()
    assert err.value.status == 401 and "Invalid JWT" in str(err.value)


def test_requires_all_three_zephyr_credentials():
    with pytest.raises(JiraError, match="Zephyr Squad Cloud needs"):
        ZephyrCloud(JiraSettings("https://x", "u", "t"))


# ── Cycles / executions / ZQL ─────────────────────────────────────────────────

from jira_insights.zephyr import ZephyrServer, normalize_cycles, zephyr_client  # noqa: E402

DC_SETTINGS = JiraSettings("https://jira.corp.example", "svc", "pat", True, zephyr_type="server")


class Recorder:
    """Opener returning queued JSON bodies and recording each request."""

    def __init__(self, *bodies):
        self.bodies, self.requests = list(bodies), []

    def __call__(self, req, timeout=None, context=None):
        self.requests.append(req)
        return FakeResponse(self.bodies.pop(0))

    def query(self, i):
        return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.requests[i].full_url).query))

    def path(self, i):
        return urllib.parse.urlsplit(self.requests[i].full_url).path


def test_cycles_normalise_list_and_dict_shapes():
    assert normalize_cycles({"-1": {"name": "Ad hoc"}, "12": {"name": "Sprint 5"}, "recordsCount": 2}) == \
        [{"id": "-1", "name": "Ad hoc"}, {"id": "12", "name": "Sprint 5"}]
    assert normalize_cycles([{"id": 7, "name": "Regression"}]) == [{"id": "7", "name": "Regression"}]


def test_cloud_zql_pages_until_total():
    page1 = {"searchObjectList": [{"execution": {"id": i}} for i in range(50)], "totalCount": 60}
    page2 = {"searchObjectList": [{"execution": {"id": i}} for i in range(50, 60)], "totalCount": 60}
    rec = Recorder(page1, page2)
    seen = []
    rows = ZephyrCloud(SETTINGS, opener=rec).zql('project = "ABC"', on_progress=lambda n, t: seen.append((n, t)))
    assert len(rows) == 60 and seen == [(50, 60), (60, 60)]
    assert [json.loads(r.data)["offset"] for r in rec.requests] == [0, 50]


def test_cloud_retries_when_rate_limited():
    calls, sleeps = [], []

    def opener(req, timeout=None, context=None):
        calls.append(req)
        if len(calls) < 3:
            raise urllib.error.HTTPError(req.full_url, 429, "slow down", {"Retry-After": "3"}, io.BytesIO(b"{}"))
        return FakeResponse({"searchObjectList": [{"execution": {"id": 1}}], "totalCount": 1})

    rows = ZephyrCloud(SETTINGS, opener=opener, sleep=sleeps.append).zql('project = "ABC"')
    assert len(rows) == 1 and len(calls) == 3 and sleeps == [3.0, 3.0]


def test_cloud_gives_a_clear_message_when_still_limited():
    def opener(req, timeout=None, context=None):
        raise urllib.error.HTTPError(req.full_url, 429, "slow down", {}, io.BytesIO(b"{}"))

    with pytest.raises(JiraError) as err:
        ZephyrCloud(SETTINGS, opener=opener, sleep=lambda s: None).cycles("1", "-1")
    assert err.value.status == 429 and "rate limit" in str(err.value)


def test_cloud_zql_posts_query_and_pages():
    rec = Recorder({"searchObjectList": [{"execution": {"id": 1}}], "totalCount": 1})
    ZephyrCloud(SETTINGS, opener=rec).zql('project = "ABC"')
    req = rec.requests[0]
    assert req.get_method() == "POST" and rec.path(0).endswith("/public/rest/api/1.0/zql/search")
    assert json.loads(req.data) == {"zqlQuery": 'project = "ABC"', "offset": 0, "maxRecords": 50}


def test_server_uses_jira_login_and_zapi_endpoints():
    rec = Recorder([{"id": 1, "name": "PASS"}], {"-1": {"name": "Ad hoc"}, "recordsCount": 1},
                   {"executions": [{"id": 9, "issueKey": "ABC-1"}], "totalCount": 1})
    server = ZephyrServer(DC_SETTINGS, opener=rec)
    assert "Zephyr Server authenticated" in server.test()
    assert server.cycles("10001", "-1") == [{"id": "-1", "name": "Ad hoc"}]
    rows = server.zql('project = "ABC" AND fixVersion = "Unscheduled" AND cycleName = "Ad hoc"')
    assert rows == [{"id": 9, "issueKey": "ABC-1"}]
    assert rec.path(0) == "/rest/zapi/latest/util/testExecutionStatus"
    assert rec.path(1) == "/rest/zapi/latest/cycle" and rec.query(1) == {"projectId": "10001", "versionId": "-1"}
    assert rec.path(2) == "/rest/zapi/latest/zql/executeSearch"
    assert rec.query(2)["zqlQuery"] == 'project = "ABC" AND fixVersion = "Unscheduled" AND cycleName = "Ad hoc"'
    assert rec.requests[0].get_header("Authorization").startswith("Basic ")      # Jira login, no JWT


def test_client_choice_follows_zephyr_mode():
    assert isinstance(zephyr_client(SETTINGS), ZephyrCloud)                       # keys → Cloud
    assert isinstance(zephyr_client(JiraSettings("https://jira.corp", "u", "t")), ZephyrServer)


def test_server_display_names_use_the_jira_login():
    rec = Recorder({"displayName": "Dev Patel"})
    assert ZephyrServer(DC_SETTINGS, opener=rec).display_names(["JIRAUSER10100"]) == {"JIRAUSER10100": "Dev Patel"}
    assert rec.path(0) == "/rest/api/2/user" and rec.query(0) == {"key": "JIRAUSER10100"}


def test_cloud_display_names_need_jira_credentials():
    from dataclasses import replace
    assert ZephyrCloud(replace(SETTINGS, url="", token=""), opener=Recorder()).display_names(["a"]) == {}
    rec = Recorder({"displayName": "Ava Chen"})
    assert ZephyrCloud(SETTINGS, opener=rec).display_names(["acc"]) == {"acc": "Ava Chen"}
    assert rec.query(0) == {"accountId": "acc"}
