"""A chunk's extra pytest arguments (admin-configured in Amplify): checked again on the pod, turned into words placed
before the "--" that precedes the test ids, and a chunk whose arguments break the rules fails with the reason."""
import pytest

import pytest_args as pa


def test_each_style_becomes_the_right_words():
    assert pa.words([
        {"flag": "--env", "style": "separate", "value": "qa3"},
        {"flag": "--browser-channel", "style": "joined", "value": "msedge"},
        {"flag": "-m", "style": "separate", "value": "smoke and not slow"},
        {"flag": "--lf", "style": "switch", "value": None},
        {"flag": "-c", "value": "config/pytest.ini"},
    ]) == ["--env", "qa3", "--browser-channel=msedge", "-m", "smoke and not slow", "--lf", "-c", "config/pytest.ini"]


@pytest.mark.parametrize("args", [None, []])
def test_no_arguments_no_words(args):
    assert pa.words(args) == []


@pytest.mark.parametrize("args", [
    [{"flag": "-p", "value": "no:gridtest.results_plugin"}],
    [{"flag": "--alluredir", "style": "joined", "value": "/tmp/x"}],
    [{"flag": "-o", "value": "cache_dir=/app"}],
    [{"flag": "--", "value": "x"}],
    [{"flag": "tests/other.py", "style": "switch"}],
    [{"flag": "--lf", "style": "switch", "value": "tests/other.py"}],
    [{"flag": "--env", "value": "--collect-only"}],
    [{"flag": "--env", "value": "a\nb"}],
    [{"flag": "-m", "style": "joined", "value": "x"}],
    [{"flag": "--env", "value": "a"}, {"flag": "--env", "value": "b"}],
    [{"flag": f"--o{i}", "style": "switch"} for i in range(21)],
    "--env qa", ["--env"], {"flag": "--env"},
])
def test_arguments_that_break_the_rules_are_refused(args):
    with pytest.raises(pa.InvalidArguments):
        pa.words(args)


def test_the_runner_passes_the_chunks_arguments_to_pytest(runner, monkeypatch):
    seen = []
    monkeypatch.setattr(runner.chunk_runner, "run_pytest",
                        lambda args, env, cwd, timeout: seen.append(args) or runner.chunk_runner.PytestOutcome(0, "", ""))

    runner._run_chunk(["t.py::a"], "qa3", True, None, None, [{"flag": "--env", "style": "separate", "value": "qa3"}])

    assert seen[0][seen[0].index("--env"):] == ["--env", "qa3", "--", "t.py::a"]


def test_a_chunk_with_bad_arguments_fails_with_the_reason_and_runs_nothing(runner, monkeypatch):
    monkeypatch.setattr(runner.chunk_runner, "run_pytest", lambda *a: pytest.fail("pytest must not run"))

    result = runner._run_chunk(["t.py::a"], "qa", True, None, None, [{"flag": "-p", "value": "evil"}])

    assert result["success"] is False and "Invalid pytest arguments" in result["stderr"] and "-p" in result["stderr"]


def test_without_arguments_the_command_is_as_before(runner, monkeypatch):
    seen = []
    monkeypatch.setattr(runner.chunk_runner, "run_pytest",
                        lambda args, env, cwd, timeout: seen.append(args) or runner.chunk_runner.PytestOutcome(0, "", ""))

    runner._run_chunk(["t.py::a"], "qa", True, None, None)

    assert "--env" not in seen[0] and seen[0][-2:] == ["--", "t.py::a"]


def test_the_poll_loop_hands_the_leased_arguments_to_the_chunk(runner, coordinator, monkeypatch, keep_sigterm_handler):
    args = [{"flag": "--env", "style": "separate", "value": "qa3"}]
    coordinator.leases.append({"chunk_id": "c-1", "tests": ["t.py::a"], "environment": "qa3", "pytest_args": args})
    coordinator.leases.append({"chunk_id": "c-2", "tests": ["t.py::b"], "environment": "qa3"})     # an older coordinator
    seen = []
    monkeypatch.setattr(runner, "run_assigned_tests", lambda tests, environment="", repo=None, sha=None, allure_dir=None,
                        pytest_args=None: seen.append(pytest_args) or [{"tests": tests, "success": True, "test_results": []}])

    runner.poll_and_execute()

    assert seen == [args, None]
