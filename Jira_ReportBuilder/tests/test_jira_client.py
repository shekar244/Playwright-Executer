import base64
import io
import json
import urllib.error
import urllib.parse

import pytest

from jira_insights.jira_client import JiraClient, JiraError
from jira_insights.settings import JiraSettings

CLOUD = JiraSettings(url="https://acme.atlassian.net", username="qa@acme.com", token="tok")


class FakeResponse:
    def __init__(self, body):
        self._raw = json.dumps(body).encode()

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(code, body=None, headers=None):
    raw = json.dumps(body or {}).encode()
    return urllib.error.HTTPError("https://x", code, "err", headers or {}, io.BytesIO(raw))


class FakeOpener:
    """Returns queued responses (dicts) or raises queued HTTPErrors; records requests."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, req, timeout=None, context=None):
        self.requests.append(req)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return FakeResponse(item)

    def params(self, i):
        return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.requests[i].full_url).query))

    def path(self, i):
        return urllib.parse.urlsplit(self.requests[i].full_url).path


def issues(*keys):
    return [{"key": k, "fields": {}} for k in keys]


def test_requires_configuration():
    with pytest.raises(JiraError, match="not configured"):
        JiraClient(JiraSettings(url="", token=""))


def test_basic_auth_when_username_present():
    opener = FakeOpener({"name": "me"})
    JiraClient(CLOUD, opener=opener).myself()
    expected = base64.b64encode(b"qa@acme.com:tok").decode()
    assert opener.requests[0].get_header("Authorization") == f"Basic {expected}"


def test_bearer_auth_for_data_center_pat():
    opener = FakeOpener({"name": "me"})
    JiraClient(JiraSettings(url="https://jira.corp", token="pat"), opener=opener).myself()
    assert opener.requests[0].get_header("Authorization") == "Bearer pat"


def test_enhanced_search_follows_next_page_token_until_last():
    opener = FakeOpener(
        {"issues": issues("A-1", "A-2"), "nextPageToken": "t2", "isLast": False},
        {"issues": issues("A-3"), "nextPageToken": "t3", "isLast": True},
    )
    seen = []
    result = JiraClient(CLOUD, opener=opener).search("project = A", on_progress=lambda n, t: seen.append((n, t)))

    assert [i["key"] for i in result] == ["A-1", "A-2", "A-3"]
    assert opener.path(0) == "/rest/api/latest/search/jql"            # same endpoint as Amplify QEA
    assert opener.params(0)["jql"] == "project = A"
    assert "nextPageToken" not in opener.params(0)
    assert opener.params(1)["nextPageToken"] == "t2"
    assert seen == [(2, None), (3, None)]


def test_search_caps_at_max_issues():
    opener = FakeOpener({"issues": issues("A-1", "A-2", "A-3"), "nextPageToken": "t2"})
    result = JiraClient(CLOUD, opener=opener).search("x", max_issues=3)
    assert len(result) == 3
    assert opener.params(0)["maxResults"] == "3"
    assert len(opener.requests) == 1


def test_falls_back_to_classic_search_with_start_at_paging():
    opener = FakeOpener(
        http_error(404),
        {"issues": issues("S-1", "S-2"), "total": 3},
        {"issues": issues("S-3"), "total": 3},
    )
    result = JiraClient(JiraSettings(url="https://jira.corp", token="pat"), opener=opener).search("x")

    assert [i["key"] for i in result] == ["S-1", "S-2", "S-3"]
    assert opener.path(1) == "/rest/api/2/search"
    assert opener.params(1)["startAt"] == "0"
    assert opener.params(2)["startAt"] == "2"


def test_jql_errors_surface_jira_messages_without_fallback():
    opener = FakeOpener(http_error(400, {"errorMessages": ["Field 'sprintt' does not exist"], "errors": {}}))
    with pytest.raises(JiraError) as err:
        JiraClient(CLOUD, opener=opener).search("sprintt = 1")
    assert err.value.status == 400
    assert "sprintt" in str(err.value)
    assert len(opener.requests) == 1


def test_rate_limit_is_retried_with_retry_after():
    sleeps = []
    opener = FakeOpener(http_error(429, headers={"Retry-After": "7"}), {"issues": [], "isLast": True})
    JiraClient(CLOUD, opener=opener, sleep=sleeps.append).search("x")
    assert sleeps == [7.0]
    assert len(opener.requests) == 2


def test_rate_limit_gives_up_after_max_retries():
    opener = FakeOpener(*[http_error(429) for _ in range(JiraClient.MAX_RETRIES + 1)])
    with pytest.raises(JiraError) as err:
        JiraClient(CLOUD, opener=opener, sleep=lambda s: None).search("x")
    assert err.value.status == 429


def test_unreachable_host_is_reported():
    opener = FakeOpener(urllib.error.URLError("nodename nor servname provided"))
    with pytest.raises(JiraError, match="Cannot reach Jira"):
        JiraClient(CLOUD, opener=opener).myself()


def test_field_names_maps_ids_to_display_names():
    opener = FakeOpener([{"id": "customfield_10016", "name": "Story Points"}, {"id": "status", "name": "Status"}])
    assert JiraClient(CLOUD, opener=opener).field_names() == {
        "customfield_10016": "Story Points", "status": "Status"}


DC = JiraSettings(url="https://jira.corp.example", username="svc-user", token="A" * 44)


def test_headers_match_amplify():
    opener = FakeOpener({"name": "me"})
    JiraClient(CLOUD, opener=opener).myself()
    req = opener.requests[0]
    assert req.get_header("Content-type") == "application/json"
    assert req.get_header("Accept") == "application/json"


def test_auto_auth_retries_basic_401_as_bearer_on_data_center():
    opener = FakeOpener(http_error(401), {"name": "svc"}, {"name": "svc"})
    client = JiraClient(DC, opener=opener)
    assert client.myself() == {"name": "svc"}
    assert opener.requests[0].get_header("Authorization").startswith("Basic ")
    assert opener.requests[1].get_header("Authorization") == "Bearer " + "A" * 44
    client.myself()                                                    # remembers Bearer
    assert opener.requests[2].get_header("Authorization").startswith("Bearer ")
    assert client.auth_scheme == "bearer"


def test_no_bearer_retry_on_cloud_or_when_auth_type_is_explicit():
    with pytest.raises(JiraError):
        JiraClient(CLOUD, opener=FakeOpener(http_error(401))).myself()
    from dataclasses import replace
    with pytest.raises(JiraError):
        JiraClient(replace(DC, auth_type="basic"), opener=FakeOpener(http_error(401))).myself()
    opener = FakeOpener({"name": "x"})
    JiraClient(replace(DC, auth_type="bearer"), opener=opener).myself()
    assert opener.requests[0].get_header("Authorization").startswith("Bearer ")


def test_enhanced_search_without_issues_falls_back_to_classic():
    opener = FakeOpener({"unexpected": True}, {"issues": issues("S-1"), "total": 1})
    result = JiraClient(DC, opener=opener).search("x")
    assert [i["key"] for i in result] == ["S-1"]
    assert opener.path(1) == "/rest/api/2/search"


def test_project_and_versions():
    opener = FakeOpener({"id": "10001", "key": "ABC"}, [{"id": "1", "name": "R1"}])
    client = JiraClient(CLOUD, opener=opener)
    assert client.project("ABC")["id"] == "10001"
    assert client.versions("ABC") == [{"id": "1", "name": "R1"}]
    assert opener.path(1) == "/rest/api/2/project/ABC/versions"
