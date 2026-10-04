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
from jira_insights.zephyr import STATUSES_PATH, ZAPI_BASE, ZephyrClient, build_qsh, canonical_qs, zephyr_jwt

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

    message = ZephyrClient(SETTINGS, opener=opener).test()
    req = seen[0]
    assert req.full_url == ZAPI_BASE + STATUSES_PATH
    assert req.get_header("Authorization").startswith("JWT ")
    assert req.get_header("Zapiaccesskey") == "AK"
    assert "2 execution statuses" in message


def test_client_surfaces_zephyr_errors():
    def opener(req, timeout=None, context=None):
        raise urllib.error.HTTPError(req.full_url, 401, "no", {}, io.BytesIO(b'{"message": "Invalid JWT"}'))

    with pytest.raises(JiraError) as err:
        ZephyrClient(SETTINGS, opener=opener).test()
    assert err.value.status == 401 and "Invalid JWT" in str(err.value)


def test_requires_all_three_zephyr_credentials():
    with pytest.raises(JiraError, match="Zephyr is not configured"):
        ZephyrClient(JiraSettings("https://x", "u", "t"))
