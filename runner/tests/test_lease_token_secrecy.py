"""The lease secret stays in the runner's memory: not in the pytest child (environment, arguments), not in
files, not in a URL, not in anything the runner prints. Test code can read the pod's own environment and
every file the pod writes, so anything that leaked there would be as good as the secret itself."""
import io
import json
import os
import tarfile

from runner_fakes import CHUNK, REPO, SHA, TOKEN, make_lease, make_tar

SUBMIT = ("POST", "/api/results/submit")
ALLURE = ("PUT", f"/api/chunks/{CHUNK}/allure")

DUMP_SNAPSHOT = {
    # Every pytest process (the main one and the xdist worker) records what test code can see of it.
    "conftest.py": b'''import json, os, sys


def _files(*roots):
    found = {}
    for root in roots:
        for dirpath, _, names in os.walk(root):
            for name in names:
                path = os.path.join(dirpath, name)
                try:
                    with open(path, "rb") as f:
                        found[path] = f.read(1_000_000).decode("utf-8", "replace")
                except OSError:
                    pass
    return found


def pytest_configure(config):
    work = os.path.dirname(os.environ["GRID_RESULTS_FILE"])  # where the runner keeps this chunk's files
    with open("%s.%d" % (os.environ["GRID_DUMP"], os.getpid()), "w") as f:
        json.dump({"argv": sys.argv, "invocation": list(config.invocation_params.args), "env": dict(os.environ),
                   "cwd": os.getcwd(), "files": _files(work, os.getcwd())}, f)
''',
    "tests/test_dump.py": b"def test_ok():\n    assert True\n",
}
DUMP_ID = "tests/test_dump.py::test_ok"


def test_the_pytest_child_gets_neither_the_secret_nor_the_grid_credentials(runner, coordinator, monkeypatch,
                                                                            keep_sigterm_handler, capsys):
    seen = []

    def run_pytest(args, env, cwd, timeout):
        assert TOKEN not in os.environ.values()  # not in the runner's own environment either (/proc/<pid>/environ)
        seen.append((list(args), dict(env)))
        return runner.chunk_runner.PytestOutcome(0, "", "")

    monkeypatch.setattr(runner.chunk_runner, "run_pytest", run_pytest)
    coordinator.lease_tokens[CHUNK] = TOKEN
    coordinator.leases.append(make_lease(repo=None, sha=None))  # the baked-tests path: no snapshot to fetch

    runner.poll_and_execute()

    (args, env), = seen
    assert TOKEN not in " ".join(args)
    assert all(TOKEN not in key and TOKEN not in value for key, value in env.items())
    assert "GRID_API_TOKEN" not in env and "COORDINATOR_URL" not in env
    assert [e[0] for e in coordinator.events] == ["submit"] and coordinator.secrets_sent() == {SUBMIT: TOKEN}
    assert TOKEN not in os.environ.values()
    seen_output = capsys.readouterr()
    assert TOKEN not in seen_output.out + seen_output.err


def test_real_pytest_never_sees_the_secret_and_no_file_holds_it(runner, coordinator, tmp_path, monkeypatch,
                                                                 keep_sigterm_handler, capsys):
    monkeypatch.setenv("GRID_DUMP", str(tmp_path / "dump"))
    coordinator.serve_code(REPO, SHA, make_tar(tmp_path / "snap.tgz", DUMP_SNAPSHOT))
    coordinator.lease_tokens[CHUNK] = TOKEN
    coordinator.leases.append({**make_lease(), "tests": [DUMP_ID]})

    runner.poll_and_execute()

    # the chunk really ran, and the coordinator took it with the secret
    assert [e[0] for e in coordinator.events] == ["submit", "allure"] and coordinator.rejected == []
    (chunk,) = coordinator.events[0][1]["results"]
    assert chunk["success"] is True and [r["outcome"] for r in chunk["test_results"]] == ["passed"]
    assert coordinator.secrets_sent() == {SUBMIT: TOKEN, ALLURE: TOKEN}

    # what test code could see: environment, argv, working directory and the chunk's files, per pytest process
    dumps = sorted(tmp_path.glob("dump.*"))
    assert dumps and any("-v" in json.loads(d.read_text())["invocation"] for d in dumps)  # the main process is in
    for dump in dumps:
        text = dump.read_text()
        assert TOKEN not in text and "secret-token-value" not in text, dump.name

    # what was sent back: the results (stdout, stderr included), the Allure archive, every URL
    assert TOKEN not in json.dumps(coordinator.events[0][1])
    with tarfile.open(fileobj=io.BytesIO(coordinator.events[1][2]), mode="r:gz") as tar:
        assert all(TOKEN.encode() not in tar.extractfile(m).read() for m in tar.getmembers() if m.isfile())
    assert all(TOKEN not in path and TOKEN not in str(query) for _, path, query in coordinator.queries)

    # what the pod wrote: nothing under the code root, the work dir or the temp dirs contains it
    for path in tmp_path.rglob("*"):
        if path.is_file() and not path.name.startswith("dump."):
            assert TOKEN.encode() not in path.read_bytes(), path
    printed = capsys.readouterr()
    assert TOKEN not in printed.out + printed.err
