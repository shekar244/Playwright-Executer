# Implementation Plan: Distributed Pytest-Playwright (Microsoft Edge) on OpenShift (SQL-Backed)
> **Superseded in part.** Sections 1-3 are the original, pre-feature specification, kept for background. Where they differ from the section **Test code delivery and Allure reports** below, that section wins: the coordinator is several modules (`artifacts.py`, `tar_limits.py`, ...) rather than one `coordinator.py`, it also stores code snapshots and Allure results, runners run a chunk from a delivered snapshot, and the manifests in `opns/` (OpenShift) and `k8s/` (Kubernetes) are authoritative, not the YAML embedded here.
>
> **Terminology: runners, not workers.** The pods that lease and execute chunks are **runners**; earlier revisions of this plan, the code and the database called them *workers*. Everything now uses the new name: the `runner/` folder and `Dockerfile.grid-runner`, the API (`/api/runner/lease`, `/api/runners/{id}/heartbeat`, `/api/runners/{id}/exit`; JSON fields `runner_id`, `assigned_runner`), the `runners` table, the `RUNNER_ID` / `RUNNER_STALE_SECONDS` / `RUNNER_POD_SELECTOR` environment variables, the `app=playwright-runner` pod label and the `playwright-runner-<browser>` images. An existing coordinator database is renamed in place the first time the new coordinator starts (rows are kept); this is one-way, so redeploy the coordinator, the runner images and Amplify together. pytest-xdist's own "workers" are a different thing and keep their name.
## 1. Architecture Overview

This project implements a multi-tier, distributed test automation execution framework running on **Red Hat OpenShift**, utilizing a **standard SQL database (PostgreSQL)** for both atomic test chunk queuing and durable test result storage.

### Core Architectural Tiers:

1. **Test Seeder (CI/CD or Local Trigger):** Discovers all `pytest` test cases, chunks them into batches of **3 tests**, and pushes them to the Central Coordination API.
2. **Central Coordination Tier (`coordinator`):** Lightweight Python FastAPI deployment backed by **PostgreSQL**. Uses atomic `FOR UPDATE SKIP LOCKED` SQL transactions to safely lease chunks to concurrent runners without race conditions, and persists execution telemetry.
3. **Ephemeral Runner Tier (`runner`):** Heavyweight container pods pre-baked with **Microsoft Edge (`microsoft-edge-stable`)**, `pytest`, and `playwright`. Each runner pod leases 3 tests, executes them in parallel across 3 headless Edge instances, posts results back to the coordinator, and terminates gracefully once the queue is empty.
4. **Autoscaling (KEDA / OpenShift Jobs):** Dynamically scales runner pods based on pending rows in the SQL `test_queue` table.

---

## 2. Directory Structure

Scaffold the repository with the following exact layout:

```text
distributed-playwright-openshift/
├── Dockerfile.coordinator
├── Dockerfile.grid-runner
├── coordinator-requirements.txt
├── runner-requirements.txt
├── coordinator/
│   └── coordinator.py
├── runner/
│   ├── runner_client.py
│   └── conftest.py
├── scripts/
│   └── seed_tests.py
├── tests/
│   └── test_sample.py
└── opns/
    ├── grid-01-postgres.yaml
    ├── grid-02-coordinator.yaml
    ├── grid-03-runner-job.yaml
    └── grid-04-keda-scaledjob.yaml
```

---

## 3. Step-by-Step Implementation Specifications

### Step 1: Central Coordination Tier (`coordinator/`)

#### `coordinator-requirements.txt`

```text
fastapi==0.115.6
uvicorn==0.32.1
sqlalchemy==2.0.36
psycopg2-binary==2.9.10
pydantic==2.10.2
```

#### `Dockerfile.coordinator`

Use a lightweight slim image since the coordinator does not require browser binaries.

```dockerfile
FROM python:3.12-slim

WORKDIR /app
COPY coordinator-requirements.txt .
RUN pip install --no-cache-dir -r coordinator-requirements.txt

COPY coordinator.py .

# OpenShift Restricted SCC compliance (non-root user)
RUN useradd -u 1001 -ms /bin/bash appuser && chown -R appuser:appuser /app
USER 1001

EXPOSE 8000
CMD ["uvicorn", "coordinator:app", "--host", "0.0.0.0", "--port", "8000"]
```

#### `coordinator/coordinator.py`

Implements SQLAlchemy models and atomic row-level locking (`SELECT ... FOR UPDATE SKIP LOCKED`) so concurrent runner pods never collide when leasing chunks.

```python
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from sqlalchemy import create_engine, Column, String, JSON, DateTime, text
from sqlalchemy.orm import declarative_base, sessionmaker, Session
import os

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://testuser:testpassword@postgres-service:5432/testdb"
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# --- Database Models ---
class TestChunkRecord(Base):
    __tablename__ = "test_queue"

    chunk_id = Column(String, primary_key=True, index=True)
    status = Column(String, default="pending", index=True)  # pending, running, completed
    tests = Column(JSON, nullable=False)
    assigned_runner = Column(String, nullable=True)
    results = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )

# Auto-create tables on startup
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Central Test Coordination Pod (SQL-Backed)")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- Pydantic Schemas ---
class TestChunk(BaseModel):
    chunk_id: str
    tests: List[str]

class TestResultSubmission(BaseModel):
    chunk_id: str
    runner_id: str
    results: List[Dict[str, Any]]

# --- Endpoints ---
@app.get("/health")
def health_check():
    return {"status": "healthy"}

@app.post("/api/queue/push")
def push_tests(chunk: TestChunk, db: Session = Depends(get_db)):
    record = TestChunkRecord(
        chunk_id=chunk.chunk_id,
        status="pending",
        tests=chunk.tests
    )
    db.add(record)
    db.commit()
    return {"status": "queued", "chunk_id": chunk.chunk_id}

@app.get("/api/runner/lease")
def lease_chunk(runner_id: str, db: Session = Depends(get_db)):
    # Atomic row-level lock skipping rows already locked by other runners
    query = text("""
        UPDATE test_queue
        SET status = 'running',
            assigned_runner = :runner_id,
            updated_at = NOW()
        WHERE chunk_id = (
            SELECT chunk_id
            FROM test_queue
            WHERE status = 'pending'
            ORDER BY created_at ASC
            LIMIT 1
            FOR UPDATE SKIP LOCKED
        )
        RETURNING chunk_id, tests;
    """)
    row = db.execute(query, {"runner_id": runner_id}).fetchone()
    db.commit()

    if not row:
        raise HTTPException(status_code=404, detail="No tests available in queue")

    return {"chunk_id": row.chunk_id, "tests": row.tests}

@app.post("/api/results/submit")
def submit_results(payload: TestResultSubmission, db: Session = Depends(get_db)):
    record = db.query(TestChunkRecord).filter(
        TestChunkRecord.chunk_id == payload.chunk_id
    ).first()

    if not record:
        raise HTTPException(status_code=404, detail="Chunk ID not found")

    record.status = "completed"
    record.assigned_runner = payload.runner_id
    record.results = payload.results
    db.commit()

    return {"status": "recorded", "chunk_id": payload.chunk_id}

@app.get("/api/results/summary")
def get_summary(db: Session = Depends(get_db)):
    pending = db.query(TestChunkRecord).filter(TestChunkRecord.status == "pending").count()
    running = db.query(TestChunkRecord).filter(TestChunkRecord.status == "running").count()
    completed_records = db.query(TestChunkRecord).filter(TestChunkRecord.status == "completed").all()

    details = {
        rec.chunk_id: {
            "runner_id": rec.assigned_runner,
            "results": rec.results
        }
        for rec in completed_records
    }

    return {
        "pending_chunks": pending,
        "running_chunks": running,
        "completed_chunks": len(completed_records),
        "details": details
    }
```

---

### Step 2: Ephemeral Runner Tier (`runner/`)

#### `runner-requirements.txt`

```text
pytest==8.3.4
pytest-xdist==3.6.1
playwright==1.49.0
requests==2.32.3
allure-pytest==2.16.2
python-dotenv==1.2.4
pytest-playwright==0.10.0
pytest-html==4.2.0
pymysql==1.2.4
pyyaml==6.0.4
anthropic==2.12.0
openai==3.57.0
trcli==1.16.0
allure-combine==1.0.11
python-dateutil==4.0.0
faker==40.63.0
```

The runner image is the test setup, ready before any pod starts: the browser, Python 3.12 and every package the test repos import are baked in, and nothing is installed when a chunk runs. A repo's own `requirements.txt` is **not** read on the pods; when a test repo needs another package, add it (pinned) to `runner-requirements.txt` and rebuild and push the runner images.

#### `Dockerfile.grid-runner`

Installs **Microsoft Edge (`microsoft-edge-stable`)** only, configures `dumb-init` to prevent zombie browser processes, and sets non-root permissions for OpenShift SCC compatibility.

```dockerfile
FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

# Install Python 3.12, curl, gnupg, dumb-init, and base browser dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    software-properties-common \
    curl \
    gnupg \
    ca-certificates \
    dumb-init \
    && add-apt-repository ppa:deadsnakes/ppa \
    && apt-get update && apt-get install -y --no-install-recommends \
    python3.12 \
    python3.12-venv \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

# Set Python 3.12 as default
RUN update-alternatives --install /usr/bin/python python /usr/bin/python3.12 1 && \
    update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.12 1

# Install official Microsoft Edge stable binary
RUN curl -sSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor > /usr/share/keyrings/microsoft-archive-keyring.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/microsoft-archive-keyring.gpg] https://packages.microsoft.com/repos/edge stable main" > /etc/apt/sources.list.d/microsoft-edge.list \
    && apt-get update && apt-get install -y --no-install-recommends \
    microsoft-edge-stable \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY runner-requirements.txt .
RUN python -m pip install --no-cache-dir --upgrade pip && \
    python -m pip install --no-cache-dir -r runner-requirements.txt

# Install Playwright OS system dependencies without downloading extra browsers
RUN playwright install-deps chromium

# Copy runner scripts and test suite
COPY runner/runner_client.py .
COPY runner/conftest.py .
COPY tests/ ./tests/

# OpenShift Restricted SCC compliance (non-root user)
RUN useradd -u 1001 -ms /bin/bash testrunner && \
    chown -R testrunner:testrunner /app
USER 1001

ENV HOME=/home/testrunner
ENTRYPOINT ["dumb-init", "--"]
CMD ["python", "runner_client.py"]
```

#### `runner/conftest.py`

Configures Playwright to launch the installed Microsoft Edge binary with shared memory flags safe for OpenShift containers.

```python
import pytest
from playwright.sync_api import sync_playwright

@pytest.fixture(scope="function")
def page():
    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="msedge",
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu"
            ]
        )
        context = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = context.new_page()
        yield page
        context.close()
        browser.close()
```

#### `runner/runner_client.py`

Leases chunks of 3 tests and executes them **in parallel** inside the pod using `pytest -n 3` (`pytest-xdist`), spinning up 3 concurrent Edge browser instances per pod.

```python
import os
import subprocess
import requests
import socket
import time

COORDINATOR_URL = os.getenv("COORDINATOR_URL", "http://central-coordinator-svc:8000")
RUNNER_ID = socket.gethostname()

def run_assigned_tests(tests: list[str]) -> list[dict]:
    # Execute up to 3 tests concurrently in separate Edge browser instances via pytest-xdist
    runner_count = min(len(tests), 3)
    test_args = ["pytest", "-v", f"-n={runner_count}"] + tests
    
    start_time = time.time()
    result = subprocess.run(test_args, capture_output=True, text=True)
    duration = round(time.time() - start_time, 2)
    
    return [{
        "tests": tests,
        "success": result.returncode == 0,
        "duration_seconds": duration,
        "stdout": result.stdout,
        "stderr": result.stderr
    }]

def poll_and_execute():
    print(f"[{RUNNER_ID}] Runner initialized. Polling {COORDINATOR_URL}...")
    while True:
        try:
            resp = requests.get(
                f"{COORDINATOR_URL}/api/runner/lease",
                params={"runner_id": RUNNER_ID},
                timeout=10
            )
            if resp.status_code == 404:
                print(f"[{RUNNER_ID}] Queue empty. Terminating runner pod cleanly.")
                break
            
            resp.raise_for_status()
            data = resp.json()
            chunk_id = data["chunk_id"]
            tests = data["tests"]
            
            print(f"[{RUNNER_ID}] Leased {chunk_id}: {tests}")
            results = run_assigned_tests(tests)
            
            requests.post(
                f"{COORDINATOR_URL}/api/results/submit",
                json={
                    "chunk_id": chunk_id,
                    "runner_id": RUNNER_ID,
                    "results": results
                },
                timeout=10
            )
            print(f"[{RUNNER_ID}] Submitted results for {chunk_id}.")
            
        except requests.exceptions.ConnectionError:
            print(f"[{RUNNER_ID}] Coordinator unreachable. Retrying in 3s...")
            time.sleep(3)
        except Exception as e:
            print(f"[{RUNNER_ID}] Unexpected error: {e}")
            break

if __name__ == "__main__":
    poll_and_execute()
```

---

### Step 3: Test Discovery & Queue Seeder (`scripts/seed_tests.py`)

```python
import os
import uuid
import requests
import pytest
from typing import List

COORDINATOR_URL = os.getenv("COORDINATOR_URL", "http://localhost:8000")

class TestItemExtractor:
    def __init__(self):
        self.tests: List[str] = []

    def pytest_collection_modifyitems(self, items):
        for item in items:
            self.tests.append(item.nodeid)

def discover_pytest_tests(test_dir: str = "tests") -> List[str]:
    extractor = TestItemExtractor()
    pytest.main(["--collect-only", "-q", test_dir], plugins=[extractor])
    return extractor.tests

def chunk_list(lst: List[str], chunk_size: int = 3) -> List[List[str]]:
    return [lst[i:i + chunk_size] for i in range(0, len(lst), chunk_size)]

def seed_queue(chunks: List[List[str]]):
    endpoint = f"{COORDINATOR_URL}/api/queue/push"
    for chunk in chunks:
        chunk_id = f"chunk-{uuid.uuid4().hex[:8]}"
        payload = {"chunk_id": chunk_id, "tests": chunk}
        resp = requests.post(endpoint, json=payload, timeout=5)
        resp.raise_for_status()
        print(f"Queued {chunk_id} -> {chunk}")

if __name__ == "__main__":
    tests = discover_pytest_tests()
    print(f"Discovered {len(tests)} tests.")
    chunks = chunk_list(tests, chunk_size=3)
    seed_queue(chunks)
    print(f"Successfully seeded {len(chunks)} chunks into coordinator.")
```

---

### Step 4: Sample Test Suite (`tests/test_sample.py`)

```python
def test_example_title(page):
    page.goto("https://example.com")
    assert "Example Domain" in page.title()

def test_example_heading(page):
    page.goto("https://example.com")
    heading = page.locator("h1").inner_text()
    assert heading == "Example Domain"

def test_example_link(page):
    page.goto("https://example.com")
    link = page.locator("a")
    assert link.is_visible()
```

---

### Step 5: OpenShift Deployment Manifests (`opns/`)

#### `opns/grid-01-postgres.yaml`

*(Note: If connecting to an existing external enterprise database, skip this file and pass your existing connection string into `DATABASE_URL` in `grid-02-coordinator.yaml`.)*

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: postgres-db
spec:
  replicas: 1
  selector:
    matchLabels:
      app: postgres-db
  template:
    metadata:
      labels:
        app: postgres-db
    spec:
      containers:
      - name: postgres
        image: postgres:16-alpine
        ports:
        - containerPort: 5432
        env:
        - name: POSTGRES_USER
          value: "testuser"
        - name: POSTGRES_PASSWORD
          value: "testpassword"
        - name: POSTGRES_DB
          value: "testdb"
        - name: PGDATA
          value: "/var/lib/postgresql/data/pgdata"
        volumeMounts:
        - name: pg-storage
          mountPath: /var/lib/postgresql/data
      volumes:
      - name: pg-storage
        emptyDir: {}
---
apiVersion: v1
kind: Service
metadata:
  name: postgres-service
spec:
  ports:
  - port: 5432
    targetPort: 5432
  selector:
    app: postgres-db
```

#### `opns/grid-02-coordinator.yaml`

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: central-coordinator
spec:
  replicas: 1
  selector:
    matchLabels:
      app: central-coordinator
  template:
    metadata:
      labels:
        app: central-coordinator
    spec:
      containers:
      - name: coordinator
        image: <ARTIFACTORY_REGISTRY>/playwright-coordinator:latest
        ports:
        - containerPort: 8000
        env:
        - name: DATABASE_URL
          value: "postgresql://testuser:testpassword@postgres-service:5432/testdb"
---
apiVersion: v1
kind: Service
metadata:
  name: central-coordinator-svc
spec:
  ports:
  - port: 8000
    targetPort: 8000
  selector:
    app: central-coordinator
```

#### `opns/grid-03-runner-job.yaml` (Manual Batch Trigger)

Includes `/dev/shm` memory mount required for 3 parallel Microsoft Edge instances.

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: playwright-edge-runners
spec:
  parallelism: 4
  completions: 4
  ttlSecondsAfterFinished: 300
  template:
    spec:
      restartPolicy: Never
      containers:
      - name: edge-runner
        image: <ARTIFACTORY_REGISTRY>/playwright-edge-runner:latest
        env:
        - name: COORDINATOR_URL
          value: "http://central-coordinator-svc:8000"
        resources:
          requests:
            cpu: "1500m"
            memory: "3Gi"
          limits:
            cpu: "3000m"
            memory: "6Gi"
        volumeMounts:
        - mountPath: /dev/shm
          name: dshm
      volumes:
      - name: dshm
        emptyDir:
          medium: Memory
          sizeLimit: 2Gi
```

#### `opns/grid-04-keda-scaledjob.yaml` (Optional: Event-Driven Auto-Scaling via OpenShift Custom Metrics Autoscaler)

Automatically queries the PostgreSQL `test_queue` table for pending chunks and spins up 1 runner pod per pending chunk (up to `maxReplicaCount`), scaling down to 0 when idle.

```yaml
apiVersion: keda.sh/v1alpha1
kind: ScaledJob
metadata:
  name: playwright-edge-autoscaler
spec:
  jobTargetRef:
    parallelism: 1
    completions: 1
    template:
      spec:
        restartPolicy: Never
        containers:
        - name: edge-runner
          image: <ARTIFACTORY_REGISTRY>/playwright-edge-runner:latest
          env:
          - name: COORDINATOR_URL
            value: "http://central-coordinator-svc:8000"
          resources:
            requests:
              cpu: "1500m"
              memory: "3Gi"
            limits:
              cpu: "3000m"
              memory: "6Gi"
          volumeMounts:
          - mountPath: /dev/shm
            name: dshm
        volumes:
        - name: dshm
          emptyDir:
            medium: Memory
            sizeLimit: 2Gi
  pollingInterval: 10
  successfulJobsHistoryLimit: 5
  failedJobsHistoryLimit: 5
  maxReplicaCount: 20
  triggers:
  - type: postgresql
    metadata:
      connection: "postgresql://testuser:testpassword@postgres-service:5432/testdb?sslmode=disable"
      query: "SELECT COUNT(*) FROM test_queue WHERE status = 'pending';"
      targetQueryValue: "1"
```

---

## Test code delivery and Allure reports

A run can carry the code it tests and yields one Allure 3 report. All coordinator logic lives in `coordinator/artifacts.py`, `tar_limits.py` (bounded tar reading), `report_support.py` and `report_environments.py` (no database imports; `coordinator.py` only wraps them in endpoints).

**Flow**

1. The client (Amplify) makes an immutable snapshot of a repo at a commit available, then creates the run with `repo` + `sha` (`POST /api/runs`, both or neither, else 422; the response echoes `repo` and `sha`, null when absent). Before packaging, the client checks that `GET /api/config` lists `code-delivery` under `features` (the others are `allure-reports`, `lease-tokens` and `pod-token`), so an older coordinator is detected instead of silently ignoring `repo`/`sha`. Delivery is either a directory `<GRID_CODE_ROOT>/<repo>/<sha>/` on a shared read-only volume, or a gzip tarball (git archive layout) uploaded with `PUT /api/code/{repo}/{sha}`.
2. `GET /api/runner/lease` returns `repo` and `sha` (null when the run has none). The runner runs the leased tests from the snapshot (volume first, else `GET /api/code/{repo}/{sha}`); no git, no pip on pods.
3. After submitting results the runner uploads its raw allure-results (tar.gz of the directory contents) to `PUT /api/chunks/{chunk_id}/allure`.
4. `GET /api/runs/{run_id}/report` merges all chunk archives of the run and builds ONE single-file report with the npm CLI: `allure awesome <results> --output <out> --single-file --report-name <run_id> --config <work>/environments.config.mjs` (Allure 3 only; no Allure 2, no Java). The result is cached and served with an `ETag`.
   **Environments.** The config declares one [Allure environment](https://allurereport.org/docs/environments/) per browser found in the results' `browser` labels (the runner's `gridtest.allure_labels` plugin adds them), and lists the run's Grid environment (`qa`, ...) as the report variable *Test environment*. The report then has an **Environment** dropdown, groups results by browser and gives each test an **Environments** tab comparing its outcome across browsers. It replaces `--group-by browser` and is the native answer to a problem the runner otherwise works around: Allure 3 identifies a test by its name and parameters, so the same test on two browsers collapses into one with a hidden retry (and a wrong pass rate) unless the results carry a `browser` parameter, which `gridtest.allure_labels` still adds. The config is JavaScript that the CLI executes, so a browser name must be a valid environment id (`[A-Za-z0-9_-]{1,64}`, at most 20) and everything else is written as escaped JSON. It is passed only with `--config`, never named `allurerc.*` (the CLI loads that from its working directory unasked). Fail soft: if no result has a `browser` label, or the CLI rejects the config, it retries with `--group-by browser`, then with neither. Allure 3.16.0 supports `environments` but not `dynamicEnvironments`, which is why the browsers are listed explicitly.

**Endpoints** (all under `/api`, behind `X-API-Token`: the full token, or the pod token on the pod endpoints, see below)

| Endpoint | Behaviour |
|---|---|
| `PUT /code/{repo}/{sha}` | Raw gzip body + required `X-Content-SHA256`. 201 `stored`, 200 `exists` (same checksum), 400 bad checksum/names, 409 different content for the same commit, 413 over `MAX_CODE_BYTES`. |
| `GET`/`HEAD /code/{repo}/{sha}` | The archive (`application/gzip`) with `X-Content-SHA256`; 404 if absent. |
| `PUT /chunks/{chunk_id}/allure` | Raw tar.gz. 404 unknown chunk, 409 not completed, 400 unsafe/corrupt archive, 413 over `MAX_ALLURE_BYTES`; overwriting (retry) is allowed. |
| `GET /runs/{run_id}/report/info` | Cheap, never generates: `available`, `chunks_with_allure`, `chunks_total`, `cached`. |
| `GET /runs/{run_id}/report` | `text/html`, `ETag`, `Cache-Control: no-store`, 304 on `If-None-Match`. 404 no Allure data, 503 CLI missing (or busy, with `Retry-After: 5`), 502 generation failed, 504 timeout. |
| `GET /runs`, `/runs/{id}` | Runs carry `repo`/`sha`; `chunk_details[*].has_allure` says whether a chunk's archive exists. A run's `status` is `queued`, `running`, `completed` or `cancelled`; `chunks` counts `pending`, `running`, `completed` and `cancelled`. |
| `POST /runs/{run_id}/cancel` | **Full token only** (a pod cannot cancel). Every `pending` or `running` chunk of the run becomes `cancelled`; finished chunks keep their results, so a partly done run ends as `cancelled` with its counts. Answers `cancelled_chunks` plus the run summary. 404 unknown run. Idempotent: a finished or already cancelled run is unchanged. A cancelled chunk is never leased again (KEDA's pending count drops and runners scale down). A runner that is mid-chunk finishes its pytest, and its `POST /results/submit` is then refused with **403** `This chunk's run was cancelled`: runners treat 403 on submit as "this pod no longer holds the chunk", drop the results and carry on (any other error would be retried and end the pod). The check precedes the lease-token check, because a chunk without a token would otherwise accept the write. |

**Environment variables** (all optional; defaults shown)

| Variable | Default | Meaning |
|---|---|---|
| `SUPPORTED_BROWSERS`, `DEFAULT_BROWSER` | all five, `edge` | Comma list of the browsers the coordinator accepts and offers (`edge`, `chrome`, `chromium`, `firefox`, `webkit`; unknown names are ignored), and the one a run gets when none is chosen. Limit it to browsers that have runner images: a run for any other waits in the queue forever. The local Docker stack sets both to `chromium`. |
| `SUPPORTED_ENVIRONMENTS`, `DEFAULT_ENVIRONMENT` | `dev,qa,perf,prod`, `dev` | Comma list of target environments the coordinator validates and offers, and the default environment for a run. Amplify can override these choices in **Admin Panel → Grid**, while the coordinator still performs the final validation. |
| `CODE_DIR` / `ALLURE_DIR` / `REPORT_DIR` | `/data/code` / `/data/allure` / `/data/reports` | Stores, all on the coordinator-only `/data` volume. |
| `MAX_CODE_BYTES` | 104857600 | Largest code archive. |
| `MAX_ALLURE_BYTES` | 52428800 | Largest per-chunk Allure archive. |
| `MAX_ALLURE_UNPACKED_BYTES`, `MAX_ALLURE_FILES` | 524288000, 20000 | Unpack limits for a whole run's report. |
| `ALLURE3_BIN` | `allure` | The Allure 3 CLI: a command, `npx` (= `npx allure`), or the npm bin folder (`echo $(npm config get prefix)/bin`). |
| `REPORT_TIMEOUT_SECONDS`, `REPORT_CONCURRENCY` | 120, 1 | CLI timeout and wait-for-a-slot limit; at most this many generations at once. |
| `CODE_RETENTION_DAYS`, `ALLURE_RETENTION_DAYS` | 30, 14 | Older code archives, or Allure archives and cached reports, are deleted (by mtime, swept hourly); reusing a snapshot (`PUT` of an existing one, `GET`/`HEAD`) or building a report touches its files, so retention counts from last use. |

**Extra pytest arguments** (Amplify's Admin Panel → Grid → *Pod pytest arguments*; chosen per run in the Grid tab)

An admin defines which pytest options a run passes to the pods and the values users may pick: *Run's environment* (a dropdown of the environment names given on that row; the choice is the run's environment — the coordinator's `SUPPORTED_ENVIRONMENTS` must include them — and the option's value, e.g. `--env qa3`; this replaces the Admin → Grid *Grid Environments* fields, whose saved values still apply while the row lists none), *Pick from values* (a dropdown; optional unless *Required*) or *On/off switch* (the bare option). Format *separate* gives `--env qa3`, *joined* `--env=qa3`. Nothing is passed until an admin defines something (the environment always reaches the pods as `TEST_ENV`), and every option must exist in the test repo (its `conftest.py` or a plugin), or pytest stops with "unrecognized arguments".

- `POST /api/runs` takes `pytest_args: [{"flag", "style": "separate"|"joined"|"switch", "value"}]` (at most 20, each option once). The coordinator stores them on the run and on every chunk, returns them with `GET /runs`, `/runs/{id}` and in the lease (`pytest_args`, `[]` when none), and announces the `pytest-args` feature; Amplify refuses to start a run with arguments on a coordinator without it, rather than have them dropped.
- The runner places the words after its own options and before the `--` that precedes the test ids. A chunk whose arguments break the rules fails with the reason instead of running without them.
- The same rules are applied in `amplify/grid_args.py`, `coordinator/pytest_args.py` and `runner/pytest_args.py` (a test keeps them identical): an option is `-x` or `--name`; values are 1-200 printable characters and, in the separate style, cannot start with `-`; short options cannot be joined; a switch has no value. Options the runner sets itself are reserved: `-p -n --numprocesses --maxprocesses --dist --alluredir -o --override-ini --headed --basetemp --rootdir --confcutdir --pyargs -h --help -V --version`.
- The gridtest plugin no longer registers `--env` (a repo that defines `--env` itself, like p13n-mktg-exp-qe-automation, failed at start-up with a conflicting option); the baked sample suite registers it in its own `tests/conftest.py`.

**Live view** (watching a pod's browser while its tests run; Amplify's Grid tab → **👁 Live**)

A runner started with `GRID_LIVE_VIEW=1` (the default in the runner manifests and in `scripts/local_pod_launcher.py`) starts `Xvfb :99` and `x11vnc` on port **5900**, view-only and behind a random 8-character password, then registers with the coordinator. pytest gets `DISPLAY=:99`, `HEADLESS=false` and, when pytest-playwright is installed, `--headed`, so the browser draws on that display. If Xvfb or x11vnc cannot start, or the registration is refused, the runner logs it and runs headless as before.

| Endpoint | Behaviour |
|---|---|
| `POST /runners/{runner_id}/view` | **Pod token.** Body `{"password": "<8-64 of A-Za-z0-9_->"}`. The coordinator records the address the request came **from** (never one in the body) and the fixed port 5900. 409 when another address registered a still-live runner of that name (all pods share the pod token); 400 when the caller has no IP address (e.g. a proxy in front of the coordinator rewrites it). |
| `GET /runners/{runner_id}/view` | **Full token only.** `host`, `port`, `password`, `status`, `browser`, `run_id`, `chunk_id`; 404 when the pod has no live view, exited (its address and password are cleared on exit) or stopped heartbeating. |
| `GET /pods` | Each pod carries `live_view` (true/false), never the address or password. |

Amplify (`amplify/routes/grid_live.py`) is the only client: `GET /api/grid/pods/<pod>/view` returns the password to the person who started the run the pod is executing, or to an admin (a pod between runs: admins only), and `GET /api/grid/pods/<pod>/vnc` is a WebSocket (subprotocol `binary`) that Amplify relays to the pod's 5900, after the same check, refusing other origins and any address that is not a plain unicast IP. The viewer is noVNC 1.7.0 (MPL-2.0), vendored under `amplify/static/vendor/novnc/`. Amplify settings: `GRID_LIVE_VIEW_MAX` (viewers at once, default 4; each holds a gunicorn thread, so the image runs 16) and `GRID_LIVE_VIEW_MAX_SECONDS` (default 3600). In a cluster Amplify must reach the runner pods on TCP 5900 (allow it in any NetworkPolicy); nothing else needs that port, and test code in other pods can reach it only with the password. With `pytest -n 3` the three browser windows share one display and overlap; the viewer shows the one on top.

**Operation notes**

- The image installs Node and the pinned Allure CLI at build time (`--build-arg ALLURE_VERSION`, default 3.16.0); there is no npm at runtime. Report generation needs memory: the manifests give the pod 2Gi and set `NODE_OPTIONS=--max-old-space-size=1024`.
- Cache: `REPORT_DIR/<run_id>/<key>.html`, where `key` is a SHA-256 over the sorted (chunk id, size, mtime) of the run's archives (read after the retention touch above), so a late chunk or a retried upload yields a fresh report. The CLI runs with an argument list, a clean environment (no tokens) and its own process group, killed on timeout.
- Names are validated before any path is built (`repo` `^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$`, `sha` `^[0-9a-f]{40}$`). Extraction accepts only regular files and directories, refuses absolute paths, `..`, links and devices, caps member count and unpacked bytes, and unpacks into a temp directory renamed into place only on success. Memory is bounded too (`tar_limits.py`): tar extension headers (GNU long name/link, PAX) over 64 KiB are refused before they are read, as are more than 8 chained ones and GNU sparse members, and the decompressed stream is capped at `members*2048 + MAX_ALLURE_UNPACKED_BYTES + 1 MiB`, so a tiny gzip cannot inflate in RAM; every refusal is a 400 (a 502 when a stored archive fails during a report build).
- The volume is `ReadWriteOnce` and only the coordinator mounts it, so the Deployment uses `strategy: Recreate`.

### Pod token and lease secrets

Test code runs inside the runner pods and can read their environment, so whatever token a pod holds is effectively public to a hostile test repo. The blast radius is limited instead (`schemas.py`, `leasing.py`):

- **Pod token.** `POD_API_TOKEN` (optional, secret key `pod-api-token`) is a second `X-API-Token` that opens only what a pod legitimately calls: `GET /api/runner/lease`, `POST /api/results/submit`, `PUT /api/chunks/{id}/allure`, `POST /api/chunks/{id}/release`, `POST /api/runners/{id}/heartbeat`, `POST /api/runners/{id}/exit`, `GET`/`HEAD /api/code/{repo}/{sha}`. Every other `/api` endpoint takes the full `API_TOKEN` only (so a new endpoint is full-only unless it is put on the `pod_api` router). An unknown token is 401 as before; a valid pod token on a full-only endpoint is 403 `This token is not allowed to call that endpoint`. `/health` and `/ready` stay open. Unset: pods use the full token as before; `API_TOKEN` unset (auth off) or `POD_API_TOKEN == API_TOKEN`: no change. Give the runners' `GRID_API_TOKEN` the pod token; Amplify and the seeder keep the full one.
- **Lease secret.** `GET /api/runner/lease?lease_tokens=1` stores `secrets.token_hex(16)` on the chunk (`test_queue.lease_token`, nullable, added by `_MIGRATIONS`) and returns it as `lease_token`. While a chunk has one, `POST /api/results/submit` (for that chunk), `PUT /api/chunks/{id}/allure` and `POST /api/chunks/{id}/release` need it in `X-Lease-Token`, else 403 `Invalid lease token for this chunk` (any API token). It is kept after completion (the Allure upload follows the submit; a retried submit works) and cleared when the chunk returns to pending (release, reaper requeue), so a stale pod cannot write to a re-leased chunk. It appears in no response but the lease response and in no log. Without `lease_tokens=1` (old runners) no token exists and nothing is enforced for that chunk.
- **Limit.** A chunk with no token (pending, or leased by an old runner) is still writable with the pod token, so the protection is complete only once every runner sends `lease_tokens=1`. Roll out coordinator first, then runners.

---

## 4. Instructions for Claude Code Execution

Run the following prompt in your terminal inside your project root to have Claude Code generate all files and verify the structure:

```bash
claude "Read implementation_plan.md and scaffold the entire directory structure, Python scripts, Dockerfiles, and OpenShift YAML manifests exactly as specified."
```