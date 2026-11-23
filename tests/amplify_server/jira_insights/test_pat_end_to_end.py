"""A Jira Data Center personal access token (no username) against a real local HTTP server."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from jira_insights.jira_client import JiraClient, JiraError
from jira_insights.settings import JiraSettings
from amplify_server.routes import zephyr

PAT = "NjE2MjM0NTY3ODkw-pat"
ALL_ISSUES = [{"key": f"QA-{n}", "fields": {"summary": f"Issue {n}"}} for n in range(1, 8)]


class FakeDataCenter(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, body):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.headers.get("Authorization") != f"Bearer {PAT}":
            return self._send(401, {"errorMessages": ["You are not authenticated."]})
        path, _, query = self.path.partition("?")
        params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
        if path.endswith("/myself"):
            return self._send(200, {"name": "pat-user", "displayName": "PAT User"})
        if path == "/rest/api/2/search/jql":      # Cloud-only endpoint — Data Center answers 404
            return self._send(404, {"errorMessages": ["Not found"]})
        if path in ("/rest/api/2/search", "/rest/api/latest/search"):
            start, size = int(params.get("startAt", 0)), int(params.get("maxResults", 50))
            return self._send(200, {"startAt": start, "total": len(ALL_ISSUES),
                                    "issues": ALL_ISSUES[start:start + size]})
        self._send(404, {"errorMessages": ["Not found"]})


@pytest.fixture()
def jira_url():
    server = HTTPServer(("127.0.0.1", 0), FakeDataCenter)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_insights_client_authenticates_with_a_pat_and_pages_through_classic_search(jira_url):
    client = JiraClient(JiraSettings(url=jira_url, username="", token=PAT))
    assert client.myself()["name"] == "pat-user"
    client.PAGE_SIZE = 3
    assert [i["key"] for i in client.search("project = QA")] == [i["key"] for i in ALL_ISSUES]


def test_a_wrong_pat_is_reported_as_401(jira_url):
    with pytest.raises(JiraError) as err:
        JiraClient(JiraSettings(url=jira_url, token="wrong")).myself()
    assert err.value.status == 401


def test_test_management_jira_calls_send_the_pat_as_bearer(jira_url, monkeypatch):
    monkeypatch.setattr(zephyr, "_z_cfg", lambda: {"jira_url": jira_url, "username": "", "api_token": PAT})
    data, code = zephyr._jira_call("GET", "/myself")
    assert code == 200 and data["name"] == "pat-user"
    data, code = zephyr._jira_search_jql("project = QA")
    assert code == 200 and len(data["issues"]) == len(ALL_ISSUES)


def test_basic_auth_is_still_used_when_a_username_is_set():
    import base64
    assert zephyr._jira_auth({"username": "qa@acme.com", "api_token": "tok"}) == \
        "Basic " + base64.b64encode(b"qa@acme.com:tok").decode()


def test_a_pat_entered_together_with_a_username_still_works_on_data_center(jira_url, monkeypatch):
    client = JiraClient(JiraSettings(url=jira_url, username="me@corp.com", token=PAT))
    assert client.myself()["name"] == "pat-user"
    assert len(client.search("project = QA")) == len(ALL_ISSUES)

    monkeypatch.setattr(zephyr, "_z_cfg", lambda: {"jira_url": jira_url, "username": "me@corp.com", "api_token": PAT})
    assert zephyr._jira_call("GET", "/myself")[1] == 200
    data, code = zephyr._jira_search_jql("project = QA")
    assert code == 200 and len(data["issues"]) == len(ALL_ISSUES)


def test_cloud_hosts_do_not_fall_back_to_bearer(monkeypatch):
    cfg = {"jira_url": "https://acme.atlassian.net", "username": "me@acme.com", "api_token": "tok"}
    assert len(zephyr._jira_auth_options(cfg)) == 1
