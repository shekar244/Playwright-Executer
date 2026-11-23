import pytest

from jira_insights import zephyr
from jira_insights.jira_client import JiraError


class FakeAmplify:
    """Stands in for routes.zephyr._z_call / _jira_call — records calls, returns queued (data, code)."""

    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def __call__(self, method, path, params=None, body=None):
        self.calls.append((method, path, params, body))
        return self.responses.pop(0)


@pytest.fixture
def cfg(monkeypatch):
    values = {"jira_url": "https://acme.atlassian.net", "access_key": "AK", "secret_key": "SK",
              "account_id": "acct", "project_key": "abc"}
    monkeypatch.setattr(zephyr, "_z_cfg", lambda: values)
    return values


def test_configured_check_and_default_project_key(cfg):
    assert zephyr.is_configured() and zephyr.default_project_key() == "ABC"
    cfg["secret_key"] = " "
    assert not zephyr.is_configured()


def test_uses_amplify_jira_and_zephyr_calls(monkeypatch, cfg):
    jira = FakeAmplify(({"id": "10500", "key": "ABC"}, 200), ([{"id": "1", "name": "R1"}], 200))
    z = FakeAmplify(({"-1": {"name": "Ad hoc"}, "33": {"name": "Sprint 5"}, "recordsCount": 2}, 200))
    monkeypatch.setattr(zephyr, "_jira_call", jira)
    monkeypatch.setattr(zephyr, "_z_call", z)
    client = zephyr.zephyr_client()
    assert client.project("ABC")["id"] == "10500"
    assert client.versions("ABC") == [{"id": "1", "name": "R1"}]
    assert client.cycles("10500", "1") == [{"id": "-1", "name": "Ad hoc"}, {"id": "33", "name": "Sprint 5"}]
    assert jira.calls[0][:2] == ("GET", "/project/ABC") and jira.calls[1][:2] == ("GET", "/project/ABC/versions")
    assert z.calls[0] == ("GET", "/public/rest/api/1.0/cycles/search", {"projectId": "10500", "versionId": "1"}, None)


def test_zql_pages_until_total(monkeypatch, cfg):
    page1 = ({"searchObjectList": [{"execution": {"id": i}} for i in range(50)], "totalCount": 70}, 200)
    page2 = ({"searchObjectList": [{"execution": {"id": i}} for i in range(50, 70)], "totalCount": 70}, 200)
    z = FakeAmplify(page1, page2)
    monkeypatch.setattr(zephyr, "_z_call", z)
    rows = zephyr.zephyr_client().zql('project = "ABC" AND cycleName = "Sprint 5"')
    assert len(rows) == 70 and [c[3]["offset"] for c in z.calls] == [0, 50]


def test_rate_limited_calls_pause_and_retry(monkeypatch, cfg):
    z = FakeAmplify(({"error": "429"}, 429), ({"error": "429"}, 429),
                    ({"searchObjectList": [{"execution": {"id": 1}}], "totalCount": 1}, 200))
    monkeypatch.setattr(zephyr, "_z_call", z)
    sleeps = []
    rows = zephyr.AmplifyZephyr(sleep=sleeps.append).zql('project = "ABC"')
    assert len(rows) == 1 and sleeps == [1.0, 2.0]
    monkeypatch.setattr(zephyr, "_z_call", FakeAmplify(*[({}, 429)] * 5))
    with pytest.raises(JiraError, match="rate limit"):
        zephyr.AmplifyZephyr(sleep=lambda s: None).cycles("1", "-1")


def test_errors_surface_instead_of_returning_partial_data(monkeypatch, cfg):
    monkeypatch.setattr(zephyr, "_z_call", FakeAmplify(({"error": {"message": "Invalid JWT"}}, 401)))
    with pytest.raises(JiraError) as err:
        zephyr.zephyr_client().test()
    assert err.value.status == 401 and "Invalid JWT" in str(err.value)
    monkeypatch.setattr(zephyr, "_z_call", FakeAmplify(({"error": "Zephyr not configured"}, 400)))
    with pytest.raises(JiraError, match="not configured"):
        zephyr.zephyr_client().cycles("1", "-1")


def test_zql_posts_the_query(monkeypatch, cfg):
    z = FakeAmplify(({"searchObjectList": [{"execution": {"id": 1}}], "totalCount": 1}, 200))
    monkeypatch.setattr(zephyr, "_z_call", z)
    assert len(zephyr.zephyr_client().zql('project = "ABC"')) == 1
    method, path, _, body = z.calls[0]
    assert (method, path) == ("POST", "/public/rest/api/1.0/zql/search")
    assert body == {"zqlQuery": 'project = "ABC"', "offset": 0, "maxRecords": 50}


def test_display_names_via_amplify_jira_call(monkeypatch, cfg):
    jira = FakeAmplify(({"displayName": "Ben Ortiz"}, 200), ({"errorMessages": ["no"]}, 404))
    monkeypatch.setattr(zephyr, "_jira_call", jira)
    assert zephyr.zephyr_client().display_names(["acc-1", "acc-x"]) == {"acc-1": "Ben Ortiz"}
    assert jira.calls[0] == ("GET", "/user", {"accountId": "acc-1"}, None)       # Cloud → account id
    cfg["jira_url"] = "https://jira.corp.example"
    jira = FakeAmplify(({}, 404), ({"displayName": "Chloe Park"}, 200))
    monkeypatch.setattr(zephyr, "_jira_call", jira)
    assert zephyr.zephyr_client().display_names(["cpark"]) == {"cpark": "Chloe Park"}
    assert [c[2] for c in jira.calls] == [{"key": "cpark"}, {"username": "cpark"}]   # DC → key, then username
