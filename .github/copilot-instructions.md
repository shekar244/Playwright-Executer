# Instructions for AI assistants working in this repo

## What this repo is

One repository, three systems:

- **Amplify QEA** (`src/amplify_server/`): a multi-tenant Flask app for running, scheduling and reporting Playwright + pytest suites. JWT sign-in, an isolated workspace per user, and the first registered user becomes admin. **Jira Insights** (`src/jira_insights/`) is a separate Streamlit package that runs beside Flask or as its own image (`Dockerfile.insights`).
- **Grid backend**, which runs tests on many browser pods: `src/coordinator/` (FastAPI + PostgreSQL queue), `src/runner/` (the pod that leases and executes chunks of tests), `src/gridtest/` and `tests/sample/` (the framework and sample tests baked into the runner image), `scripts/seed_tests.py`.
- **Deployment**: `Dockerfile*`, `config/docker-compose.yml` (local), `k8s/` (Kubernetes + Kustomize), `opns/` (OpenShift).

Documentation lives in `docs/`: `ARCHITECTURE.md` (Amplify's multi-tenant design), `EXTENDING.md` (how to add a tab or route, and its "Key rules" table), `DOCKER.md`, and `distributed_playwright_implementation.md` (the Grid). The READMEs of the repo root, `k8s/` and `opns/` stay beside what they describe.

## Vocabulary

- **Runner** is a Grid pod that executes chunks of tests. Never call it a "worker". pytest-xdist's own workers, gunicorn's `--workers` and `ThreadPoolExecutor(max_workers=...)` are unrelated and keep their names (`PYTEST_XDIST_WORKER*` and `workerinput` are xdist's, too).
- `src/amplify_server/runner.py` (`TestRunner`) runs a local suite as a subprocess for the Executor tab. It is unrelated to Grid runners, and `Dockerfile.runner` is Amplify's browser-enabled test image, not the Grid runner (`Dockerfile.grid-runner`).
- A **run** is one request to the Grid; it is split into **chunks**, each leased to one runner.

## Conventions

- Deployment files are named `<system>-<resource>`: `amplify-*` or `grid-*` (see `.github/instructions/deployment.instructions.md`).
- Never commit secrets. `.env`, `*.env`, `config.json`, `config/amplify_server/config.json`, `report_history.json`, `src/jira_insights/workspace/`, and `**/jira_insights/workspace/` are git-ignored; the local stack keeps secrets in `config/.env`. Standalone Jira Insights state belongs outside source in `~/.amplify-qea/jira_insights/`; signed-in server users use `<workspace>/jira_insights/`. Do not put secrets in command-line arguments, images or manifests.
- Treat anything Amplify does not control as hostile: request bodies, pod test results, the coordinator's responses, history files, repo and folder names. Parse it defensively and escape it when it reaches HTML.
- A behaviour change comes with a test in the same change. Test names are full sentences describing the behaviour (`test_a_user_can_change_their_password`).
- Do not claim a manifest works on a cluster unless it was applied to one; the READMEs say these have only been rendered and checked by script.

## Running things

- **Amplify** (Python 3.13, `venv/`): `venv/bin/python -m pytest tests/amplify_server` (about 3 minutes; `test_ui_escaping.py` needs `node` and is skipped without it).
- **Grid** (Python 3.12, like its images): `pip install -r coordinator-requirements.txt -r runner-requirements.txt`, then `pytest tests/coordinator` and `pytest tests/runner`. Tests that need a real PostgreSQL run only when `GRID_TEST_DATABASE_URL` points at a scratch database.
- Always pass a path to pytest: the root `pytest.ini` sets `testpaths = tests/sample/test_sample.py`, so a bare `pytest` runs only the Playwright sample suite.
- **Local stack**: `scripts/local-up.sh` starts Amplify (http://localhost:8080), the coordinator and PostgreSQL; `python3 scripts/local_pod_launcher.py` then starts runner containers for queued chunks.
