"""The per-chunk lease secret: asked for on the lease, kept with the chunk, sent on the three writes.

The fake coordinator enforces the secret like the real one (403 for a missing or wrong X-Lease-Token),
so a runner that does not really send the header cannot pass these tests.
"""
import signal
from pathlib import Path

import pytest

import lease_token
from runner_fakes import CHUNK, OTHER, TOKEN, make_lease

SUBMIT = ("POST", "/api/results/submit")
ALLURE = ("PUT", f"/api/chunks/{CHUNK}/allure")


def _kinds(coordinator):
    return [e[0] for e in coordinator.events]


def _no_secret_in_urls(coordinator):
    for _, path, query in coordinator.queries:
        assert TOKEN not in path and OTHER not in path and TOKEN not in str(query) and OTHER not in str(query)


def _no_secret_printed(capsys):
    seen = capsys.readouterr()
    for secret in (TOKEN, OTHER):
        assert secret not in seen.out + seen.err
    return seen.out


@pytest.fixture
def fast_chunks(runner, monkeypatch):
    """run_assigned_tests without pytest: one passing chunk and one Allure result file to upload."""
    def run(tests, environment="", repo=None, sha=None, allure_dir=None, pytest_args=None):
        if allure_dir:
            Path(allure_dir, "r-result.json").write_text('{"status": "passed"}')
        return [{"tests": tests, "environment": environment, "browser": "chromium", "success": True,
                 "duration_seconds": 0.1, "test_results": [], "stdout": "", "stderr": ""}]

    monkeypatch.setattr(runner, "run_assigned_tests", run)


@pytest.fixture
def no_sleep(runner, monkeypatch):
    monkeypatch.setattr(runner.time, "sleep", lambda seconds: None)  # the submit retries wait 3 s each


# ---------------------------------------------------------------- asking for and sending the secret

def test_the_lease_request_asks_for_lease_tokens(runner, coordinator, keep_sigterm_handler):
    runner.poll_and_execute()  # the queue is empty: the runner ends cleanly

    (_, _, query), = [q for q in coordinator.queries if q[1] == "/api/runner/lease"]
    assert query == {"runner_id": ["pod-test-1"], "browser": ["chromium"], "lease_tokens": ["1"]}


def test_the_secret_goes_on_submit_and_allure_and_on_nothing_else(runner, coordinator, fast_chunks,
                                                                   keep_sigterm_handler, capsys):
    coordinator.lease_tokens[CHUNK] = TOKEN
    coordinator.leases.append(make_lease())

    runner.poll_and_execute()

    assert _kinds(coordinator) == ["submit", "allure"] and coordinator.rejected == []  # the fake enforced it
    assert coordinator.secrets_sent() == {SUBMIT: TOKEN, ALLURE: TOKEN}
    assert all(h.get("X-API-Token") == "secret-token-value" for _, _, h, _ in coordinator.requests)
    _no_secret_in_urls(coordinator)
    _no_secret_printed(capsys)


def test_the_fake_really_refuses_a_missing_or_wrong_secret_on_all_three_writes(runner, coordinator):
    coordinator.required["c1"] = TOKEN

    assert runner.submit_results("c1", []) is False
    assert runner.submit_results("c1", [], OTHER) is False
    assert runner.upload_allure("c1", b"x") is False
    assert runner.upload_allure("c1", b"x", lease_token=OTHER) is False
    runner.release_chunk("c1")
    runner.release_chunk("c1", OTHER)
    assert len(coordinator.rejected) == 6 and coordinator.events == [] and coordinator.releases == []

    assert runner.submit_results("c1", [], TOKEN) is True
    assert runner.upload_allure("c1", b"x", lease_token=TOKEN) is True
    runner.release_chunk("c1", TOKEN)
    assert _kinds(coordinator) == ["submit", "allure"]
    assert coordinator.releases == [("c1", "pod-test-1", TOKEN)]


def test_the_allure_upload_does_not_retry_a_refused_secret(runner, coordinator, capsys):
    coordinator.required["c1"] = TOKEN

    assert runner.upload_allure("c1", b"x", lease_token=OTHER) is False

    assert len(coordinator.requests) == 1  # 403: retrying cannot help
    assert "HTTP 403" in capsys.readouterr().out


def test_publish_allure_passes_the_secret_on(runner, coordinator, tmp_path):
    (tmp_path / "a-result.json").write_text("{}")
    coordinator.required["c1"] = TOKEN

    runner.publish_allure("c1", str(tmp_path), TOKEN)

    assert _kinds(coordinator) == ["allure"] and coordinator.rejected == []


# ---------------------------------------------------------------- no secret: nothing changes

@pytest.mark.parametrize("returned", ["absent", "null"])
def test_a_lease_without_a_secret_sends_no_header_and_changes_nothing(runner, coordinator, fast_chunks,
                                                                      keep_sigterm_handler, capsys, returned):
    lease = make_lease()
    if returned == "null":
        lease["lease_token"] = None
    coordinator.leases.append(lease)  # an old coordinator: not in coordinator.lease_tokens

    runner.poll_and_execute()

    assert _kinds(coordinator) == ["submit", "allure"] and coordinator.rejected == []
    assert coordinator.secrets_sent() == {}
    assert "malformed" not in capsys.readouterr().out


# ---------------------------------------------------------------- retries and failure paths

def test_a_retried_submit_sends_the_same_secret_every_time(runner, coordinator, fast_chunks, no_sleep,
                                                           keep_sigterm_handler, capsys):
    coordinator.lease_tokens[CHUNK] = TOKEN
    coordinator.leases.append(make_lease())
    coordinator.submit_statuses = [500, 503]

    runner.poll_and_execute()

    submits = [h for m, p, h, _ in coordinator.requests if (m, p) == SUBMIT]
    assert len(submits) == 3 and all(h.get("X-Lease-Token") == TOKEN for h in submits)
    assert _kinds(coordinator) == ["submit", "allure"] and coordinator.rejected == []
    assert "Submit attempt 2/3 failed" in _no_secret_printed(capsys)


def test_giving_up_on_the_submit_releases_the_chunk_with_its_secret(runner, coordinator, fast_chunks, no_sleep,
                                                                    keep_sigterm_handler, capsys):
    coordinator.lease_tokens[CHUNK] = TOKEN
    coordinator.leases.append(make_lease())
    coordinator.submit_statuses = [500, 500, 500]

    with pytest.raises(SystemExit) as exit_info:
        runner.poll_and_execute()

    assert exit_info.value.code == 1
    assert coordinator.releases == [(CHUNK, "pod-test-1", TOKEN)] and coordinator.rejected == []
    assert "Release of run1-chromium-000: released" in _no_secret_printed(capsys)
    _no_secret_in_urls(coordinator)


def test_a_401_on_submit_is_still_retried_and_still_fatal(runner, coordinator, no_sleep):
    coordinator.submit_statuses = [401, 401, 401]

    with pytest.raises(RuntimeError, match="Could not submit results for c1"):
        runner.submit_results("c1", [], TOKEN)

    assert len(coordinator.requests) == 3  # unchanged: only 403 is final


@pytest.mark.parametrize("secret", [TOKEN, None])
def test_release_on_sigterm_sends_the_secret(runner, coordinator, monkeypatch, keep_sigterm_handler, capsys,
                                             secret):
    if secret:
        coordinator.lease_tokens[CHUNK] = secret
    coordinator.leases.append(make_lease())

    def killed_while_running(*args, **kwargs):
        signal.raise_signal(signal.SIGTERM)  # the runner's handler raises SystemExit(143)
        raise AssertionError("the SIGTERM handler should have unwound the run")

    monkeypatch.setattr(runner, "run_assigned_tests", killed_while_running)

    with pytest.raises(SystemExit) as exit_info:
        runner.poll_and_execute()

    assert exit_info.value.code == 143
    assert coordinator.releases == [(CHUNK, "pod-test-1", secret)]  # accepted: the fake enforced the secret
    assert coordinator.rejected == [] and coordinator.events == []
    assert ("POST", "/api/runners/pod-test-1/exit") in [(m, p) for m, p, _, _ in coordinator.requests]
    _no_secret_printed(capsys)
    _no_secret_in_urls(coordinator)


def test_no_release_is_attempted_for_a_chunk_that_was_submitted(runner, coordinator, fast_chunks,
                                                                 keep_sigterm_handler):
    coordinator.lease_tokens[CHUNK] = TOKEN
    coordinator.leases.append(make_lease())

    runner.poll_and_execute()

    assert coordinator.releases == []  # nothing in flight at the end: the secret is forgotten with the chunk


# ---------------------------------------------------------------- 403: this pod no longer holds the chunk

def test_a_403_on_submit_is_reported_and_the_loop_carries_on(runner, coordinator, fast_chunks,
                                                              keep_sigterm_handler, capsys):
    coordinator.lease_tokens.update({"c0": TOKEN, "c1": OTHER})
    coordinator.required["c0"] = "f" * 32  # c0 was requeued and leased by another pod meanwhile
    coordinator.leases.extend([make_lease("c0"), make_lease("c1")])

    runner.poll_and_execute()  # returns normally: no crash, no exit

    assert coordinator.rejected == [SUBMIT]  # one attempt, no retries
    assert _kinds(coordinator) == ["submit", "allure"]
    assert coordinator.events[0][1]["chunk_id"] == "c1" and coordinator.events[1][1] == "c1"
    assert not any(p == "/api/chunks/c0/allure" for _, p, _, _ in coordinator.requests)  # nothing uploaded for c0
    assert coordinator.releases == []  # the chunk is not ours any more
    out = _no_secret_printed(capsys)
    assert ("Results for c0 were rejected (Invalid lease token for this chunk); "
            "this pod no longer holds the chunk. Dropping them.") in out
    assert "Submitted results for c1" in out and "Unexpected error" not in out
    _no_secret_in_urls(coordinator)


def test_a_rejected_last_chunk_is_neither_uploaded_nor_released(runner, coordinator, fast_chunks,
                                                                 keep_sigterm_handler, capsys):
    coordinator.lease_tokens["c0"] = TOKEN
    coordinator.required["c0"] = "f" * 32
    coordinator.leases.append(make_lease("c0"))

    runner.poll_and_execute()  # the queue runs dry right after the rejection: still a clean exit

    assert coordinator.rejected == [SUBMIT] and coordinator.events == []
    assert coordinator.releases == [] and not any(m == "PUT" for m, _, _, _ in coordinator.requests)
    assert "Queue empty. Terminating runner pod cleanly." in _no_secret_printed(capsys)


def test_a_403_without_any_secret_is_final_too(runner, coordinator):
    coordinator.submit_statuses = [403]

    assert runner.submit_results("c1", []) is False
    assert len(coordinator.requests) == 1


# ---------------------------------------------------------------- a malformed secret

@pytest.mark.parametrize("bad", [123, ["a" * 32], "", "short", "has a space 123456", "x" * 300,
                                 "looks-fine-12345\r\nX-Injected: 1"])
def test_a_malformed_secret_is_ignored_without_being_repeated(runner, coordinator, fast_chunks,
                                                               keep_sigterm_handler, capsys, bad):
    lease = make_lease()
    lease["lease_token"] = bad
    coordinator.leases.append(lease)

    runner.poll_and_execute()  # a bad header value would make requests raise an error that quotes it

    assert _kinds(coordinator) == ["submit", "allure"] and coordinator.secrets_sent() == {}
    out = capsys.readouterr().out
    assert "Ignoring a malformed lease_token in the lease response." in out
    if isinstance(bad, str) and len(bad) > 4:
        assert bad not in out


# ---------------------------------------------------------------- the helper module

def test_accept_takes_only_header_safe_strings():
    notes = []

    assert lease_token.accept(TOKEN, notes.append) == TOKEN
    assert lease_token.accept("abc_DEF-1.2~3", notes.append) == "abc_DEF-1.2~3"
    assert lease_token.accept(None, notes.append) is None and notes == []
    for bad in (7, "", "tiny", "a b c d e f g h", "x" * 257, "line\nbreak-12345", "ü" * 12, b"0123456789abcdef"):
        assert lease_token.accept(bad, notes.append) is None
    assert len(notes) == 8 and all(TOKEN not in n for n in notes)
    assert lease_token.accept("bad value!!", None) is None  # a missing logger is fine


def test_with_token_builds_a_new_dict_and_adds_the_header_only_for_a_secret():
    base = {"X-API-Token": "pod"}

    assert lease_token.with_token(base, TOKEN) == {"X-API-Token": "pod", "X-Lease-Token": TOKEN}
    assert lease_token.with_token(base, None) == base and lease_token.with_token(base, None) is not base
    assert lease_token.with_token({}, "") == {}
    assert base == {"X-API-Token": "pod"}


class _Reply:
    def __init__(self, status, payload):
        self.status_code, self._payload = status, payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def test_rejection_is_the_clean_short_detail_and_never_the_secret():
    assert lease_token.rejection(_Reply(403, {"detail": "Invalid lease token for this chunk"})) == \
        "Invalid lease token for this chunk"
    assert lease_token.rejection(_Reply(403, {"detail": f"bad {TOKEN} here"}), TOKEN) == "bad *** here"
    assert lease_token.rejection(_Reply(403, {"detail": "two\nlines\x1b[31m"})) == "two?lines?[31m"
    assert len(lease_token.rejection(_Reply(403, {"detail": "d" * 5000}))) == 120
    for payload in (ValueError("not json"), {"detail": ["a list"]}, {"detail": ""}, {}, [1]):
        assert lease_token.rejection(_Reply(403, payload)) == "HTTP 403"
