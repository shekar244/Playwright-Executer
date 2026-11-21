"""Local stand-in for KEDA, with real containers.

For each browser that has pending chunks in the coordinator's PostgreSQL, start runner containers (one per
pending chunk, up to MAX_PER_BROWSER). A runner exits by itself when the queue is empty.

Runs on the host and talks to Docker through the `docker` CLI; nothing is mounted into a container.
Defaults match config/docker-compose.yml (project "amplify-grid", secrets in config/.env):

    python3 scripts/local_pod_launcher.py

Options (environment): COMPOSE_PROJECT (amplify-grid), ENV_FILE (config/.env), MAX_PER_BROWSER (2),
RUNNER_IMAGE_TEMPLATE (amplify-grid-runner-{browser}:local), WITH_VOLUME=1 + CODE_STORE_DIR to mount a
tests-store folder read-only at /code. Stop it with Ctrl-C / SIGTERM: the runners it started are stopped too.
"""
import os
import secrets
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECT = os.getenv("COMPOSE_PROJECT", "amplify-grid")
ENV_FILE = Path(os.getenv("ENV_FILE", REPO_ROOT / "config" / ".env"))
NETWORK = f"{PROJECT}_default"
IMAGE_TEMPLATE = os.getenv("RUNNER_IMAGE_TEMPLATE", "amplify-grid-runner-{browser}:local")
MAX_PER_BROWSER = int(os.getenv("MAX_PER_BROWSER", "2"))
CODE_STORE_DIR = os.getenv("CODE_STORE_DIR", "")
WITH_VOLUME = os.getenv("WITH_VOLUME") == "1" and CODE_STORE_DIR

pods: dict[str, str] = {}      # container name -> browser
warned: set[str] = set()
stop = False


def say(msg: str) -> None:
    print(f"{datetime.now():%H:%M:%S} {msg}", flush=True)


def read_env(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text().splitlines()
    except OSError:
        sys.exit(f"Cannot read {path}: run scripts/local-up.sh first")
    return dict(line.split("=", 1) for line in lines if "=" in line and not line.lstrip().startswith("#"))


def docker(*args: str, env: dict | None = None) -> str:
    return subprocess.run(["docker", *args], capture_output=True, text=True, env=env).stdout


def postgres_container() -> str:
    out = docker("ps", "-q", "-f", f"label=com.docker.compose.project={PROJECT}",
                 "-f", "label=com.docker.compose.service=postgres")
    return out.split()[0] if out.split() else ""


def pending(pg: str) -> dict[str, int]:
    out = docker("exec", pg, "psql", "-U", "postgres", "-d", "grid", "-Atc",
                 "SELECT browser, count(*) FROM test_queue WHERE status='pending' GROUP BY browser")
    return {b: int(n) for b, n in (line.split("|") for line in out.split() if "|" in line)}


def alive(name: str) -> bool:
    return bool(docker("ps", "-q", "-f", f"name=^{name}$").strip())


def start(browser: str, token: str) -> None:
    image = IMAGE_TEMPLATE.format(browser=browser)
    if not docker("images", "-q", image).strip():
        if browser not in warned:
            warned.add(browser)
            say(f"no runner image {image}: chunks for '{browser}' stay queued (build it, see config/docker-compose.yml)")
        return
    name = f"pw-{browser}-{secrets.token_hex(3)}"
    cmd = ["run", "-d", "--rm", "--name", name, "--network", NETWORK, "--shm-size=1g",
           "-e", f"BROWSER={browser}", "-e", f"RUNNER_ID={name}", "-e", "COORDINATOR_URL=http://coordinator:8000",
           "-e", "GRID_API_TOKEN"]            # value comes from this process's environment, not from argv
    if WITH_VOLUME:
        cmd += ["-v", f"{CODE_STORE_DIR}:/code:ro"]
    docker(*cmd, image, env={**os.environ, "GRID_API_TOKEN": token})
    pods[name] = browser
    say(f"scale up   {name}")


def main() -> None:
    global stop
    env = read_env(ENV_FILE)
    token = env.get("POD_API_TOKEN") or env.get("GRID_API_TOKEN", "")   # pods only ever hold the pod token
    if not token:
        sys.exit(f"{ENV_FILE} has neither POD_API_TOKEN nor GRID_API_TOKEN")
    signal.signal(signal.SIGTERM, lambda *a: globals().update(stop=True))
    signal.signal(signal.SIGINT, lambda *a: globals().update(stop=True))
    say(f"pod launcher ready (project={PROJECT}, up to {MAX_PER_BROWSER} per browser, volume={'on' if WITH_VOLUME else 'off'})")
    while not stop:
        pg = postgres_container()
        if pg:
            for name in [n for n in pods if not alive(n)]:
                say(f"scale down {name}")
                del pods[name]
            for browser, waiting in pending(pg).items():
                running = sum(1 for b in pods.values() if b == browser)
                for _ in range(max(0, min(waiting, MAX_PER_BROWSER) - running)):
                    start(browser, token)
        time.sleep(2)
    for name in pods:
        docker("stop", "-t", "5", name)
    say("stopped")


if __name__ == "__main__":
    main()
