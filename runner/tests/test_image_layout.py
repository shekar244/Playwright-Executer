"""Static checks of the runner image and manifests (W1, W3). The real image is exercised with docker run."""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "Dockerfile.grid-runner"
MANIFESTS = ["k8s/grid-runners/scaledjobs.yaml", "k8s/grid-runners/job-example.yaml",
             "opns/grid-03-runner-job.yaml", "opns/grid-04-keda-scaledjob.yaml"]


def _instructions(text: str) -> list[str]:
    """Dockerfile instructions with line continuations joined and comments dropped."""
    joined = re.sub(r"\\\n", " ", "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#")))
    return [" ".join(line.split()) for line in joined.splitlines() if line.strip()]


def test_app_is_root_owned_and_not_writable_by_the_runtime_uid():
    instructions = _instructions(DOCKERFILE.read_text())
    runs = [i for i in instructions if i.startswith("RUN ")]

    assert any(re.search(r"chown -R root:root /app(\s|$)", r) for r in runs)
    assert any(re.search(r"chmod -R \S*go-w\S* /app(\s|$)", r) for r in runs)
    for run in runs:  # nothing may hand /app (or /app/src) back to uid 1001 / group 0 or make it group-writable
        for command in re.split(r"&&|;", run):
            if re.search(r"(^|\s)/app(/\S*)?(\s|$)", command):
                assert "1001" not in command and not re.search(r"chown\b.*:0\b", command), command
                assert "g=u" not in command and "g+w" not in command and "a+w" not in command, command
    user_at = instructions.index("USER 1001")
    assert instructions.index(next(r for r in runs if "chown -R root:root /app" in r)) < user_at


def test_home_and_the_work_dir_stay_writable_for_group_zero():
    runs = [i for i in _instructions(DOCKERFILE.read_text()) if i.startswith("RUN ")]

    assert any("chown -R 1001:0 /home/testrunner" in r and "chmod -R g=u /home/testrunner" in r for r in runs)
    assert any("chown 1001:0 /tmp/grid-work" in r and "chmod 0775 /tmp/grid-work" in r for r in runs)


def test_every_runner_module_is_copied_into_the_image():
    copies = " ".join(i for i in _instructions(DOCKERFILE.read_text()) if i.startswith("COPY "))
    modules = sorted(p.name for p in (ROOT / "runner").glob("*.py"))

    assert "runner_client.py" in modules and len(modules) >= 4
    for module in modules:
        assert f"runner/{module}" in copies, f"{module} is not COPY'd into the runner image"


def test_the_dockerfile_says_the_token_stays_readable_by_test_code():
    text = DOCKERFILE.read_text()

    assert "GRID_API_TOKEN stays readable by test code" in text
    assert "GRID_CHUNK_TIMEOUT_SECONDS" in text


@pytest.mark.parametrize("manifest", MANIFESTS)
def test_the_chunk_timeout_is_documented_in_every_runner_manifest(manifest):
    text = (ROOT / manifest).read_text()
    containers = text.count("name: GRID_API_TOKEN")

    assert containers >= 1
    assert text.count("# - name: GRID_CHUNK_TIMEOUT_SECONDS") == containers  # one per runner container
    assert text.count("default 1800") == containers


def _token_refs(node):
    """The secretKeyRef of every GRID_API_TOKEN env entry in a parsed manifest."""
    if isinstance(node, dict):
        if node.get("name") == "GRID_API_TOKEN" and "valueFrom" in node:
            yield node["valueFrom"]["secretKeyRef"]
        for value in node.values():
            yield from _token_refs(value)
    elif isinstance(node, list):
        for value in node:
            yield from _token_refs(value)


@pytest.mark.parametrize("manifest", MANIFESTS)
def test_runners_get_the_pod_only_token_and_never_the_full_one(manifest):
    yaml = pytest.importorskip("yaml")
    text = (ROOT / manifest).read_text()
    refs = [r for doc in yaml.safe_load_all(text) for r in _token_refs(doc)]

    assert len(refs) == text.count("name: GRID_API_TOKEN") >= 1  # every runner container, none missed
    assert all(r["key"] == "pod-api-token" and r["optional"] is True for r in refs)
    assert not re.search(r"^\s*key: api-token\s*$", text, re.M)  # the full token (Amplify's) is not handed to pods
    assert text.count("create key pod-api-token in this Secret with the coordinator's POD_API_TOKEN") == len(refs)
    assert text.count("Runners must NOT receive the full token") == len(refs)


def test_the_dockerfile_points_to_the_pod_token_and_the_lease_secret():
    text = DOCKERFILE.read_text()

    assert "POD-ONLY token" in text and "POD_API_TOKEN" in text and "lease secret" in text
