# Amplify QEA — OpenShift Deployment Guide

> Running plain Kubernetes rather than OpenShift? Use [`../k8s`](../k8s/README.md) (Ingress + Kustomize) instead.

## Prerequisites

| Requirement | Details |
|-------------|---------|
| **Docker** | Installed and running (`docker --version`) |
| **OpenShift CLI** | `oc` installed and logged in (`oc login https://your-cluster`) |
| **JFrog Artifactory** | Docker repository with push/pull access |
| **OpenShift Project** | Namespace with permissions to create deployments, secrets, PVCs, routes |

### Credentials Needed

| Credential | Where to get it |
|------------|-----------------|
| **JFrog Registry URL** | e.g. `yourcompany.jfrog.io` — JFrog Platform > Settings |
| **JFrog Docker Repo** | e.g. `docker-local` — Artifactory > Repositories |
| **JFrog Username** | Your JFrog login email |
| **JFrog API Key** | User Profile > Generate API Key |
| **Jira URL** | `https://yourcompany.atlassian.net` |
| **Jira API Token** | `id.atlassian.com` > Security > API tokens |
| **Zephyr Keys** | Jira > Zephyr app > API Keys |

---

## Architecture

```
 Developer Machine               JFrog Artifactory              OpenShift Cluster
┌──────────────────┐           ┌──────────────────┐          ┌─────────────────────────┐
│                  │  docker   │                  │  oc pull  │                         │
│  Source Code     │──push───→ │  docker-local/   │────────→ │  Pod: amplify-qea-grid       │
│  + Dockerfile    │           │  amplify-qea-grid     │          │  ├── Flask :8080         │
│                  │           │  :latest         │          │  └── Streamlit :8501     │
└──────────────────┘           └──────────────────┘          │                         │
                                                             │  PVC: /app/data (10Gi)  │
                                                             │  ├── amplify.db         │
                                                             │  ├── server_config.json  │
                                                             │  └── workspaces/         │
                                                             │      ├── user-1/         │
                                                             │      ├── user-2/         │
                                                             │      └── ...             │
                                                             │                         │
                                                             │  Secret: amplify-secrets │
                                                             │  Secret: jfrog-pull      │
                                                             │  Route: HTTPS            │
                                                             └─────────────────────────┘
```

---

## Deployment (Step by Step)

### Step 1: Build the Docker Image

```bash
cd /path/to/amplify

docker build -t amplify-qea-grid:latest .
```

Verify the build:
```bash
docker images | grep amplify-qea-grid
```

### Step 2: Tag for JFrog Artifactory

```bash
# Format: <jfrog-registry>/<docker-repo>/amplify-qea-grid:<tag>
docker tag amplify-qea-grid:latest yourcompany.jfrog.io/docker-local/amplify-qea-grid:latest
```

Replace:
- `yourcompany.jfrog.io` — your JFrog Artifactory hostname
- `docker-local` — your Docker repository name in Artifactory

### Step 3: Push to JFrog

```bash
# Login to JFrog
docker login yourcompany.jfrog.io -u your-email@company.com

# Enter your JFrog API key when prompted for password

# Push the image
docker push yourcompany.jfrog.io/docker-local/amplify-qea-grid:latest
```

Verify in JFrog UI: Artifactory > docker-local > amplify-qea-grid > latest

### Step 4: Create OpenShift Project

```bash
oc login https://your-openshift-cluster

oc new-project amplify-qea-grid --display-name="Amplify QEA"
# or switch to existing:
oc project amplify-qea-grid
```

### Step 5: Create JFrog Pull Secret

OpenShift needs credentials to pull the image from JFrog:

```bash
oc create secret docker-registry jfrog-pull-secret \
  --docker-server=yourcompany.jfrog.io \
  --docker-username=your-email@company.com \
  --docker-password=YOUR_JFROG_API_KEY

# Link to default service account so pods can use it
oc secrets link default jfrog-pull-secret --for=pull
```

### Step 6: Create Application Secrets

```bash
oc create secret generic amplify-secrets \
  --from-literal=jwt-secret-key="$(openssl rand -base64 32)" \
  --from-literal=jira-url="https://yourcompany.atlassian.net" \
  --from-literal=jira-username="qa@yourcompany.com" \
  --from-literal=jira-api-token="YOUR_JIRA_API_TOKEN" \
  --from-literal=zephyr-access-key="YOUR_ZEPHYR_ACCESS_KEY" \
  --from-literal=zephyr-secret-key="YOUR_ZEPHYR_SECRET_KEY" \
  --from-literal=zephyr-account-id="YOUR_ATLASSIAN_ACCOUNT_ID"
```

**Minimal (Jira/Zephyr can be configured later from Admin Panel):**
```bash
oc create secret generic amplify-secrets \
  --from-literal=jwt-secret-key="$(openssl rand -base64 32)"
```

### Step 7: Update Deployment Image Reference

Edit `opns/amplify-deployment.yaml` — change the image line to your JFrog path:

```yaml
# Find this line:
image: image-registry.openshift-image-registry.svc:5000/${NAMESPACE}/amplify-qea-grid:latest

# Change to:
image: yourcompany.jfrog.io/docker-local/amplify-qea-grid:latest
```

Add `imagePullSecrets` under `spec.template.spec`:
```yaml
spec:
  template:
    spec:
      imagePullSecrets:
        - name: jfrog-pull-secret
      containers:
        - name: amplify-qea-grid
          image: yourcompany.jfrog.io/docker-local/amplify-qea-grid:latest
```

### Step 8: Deploy All Resources

```bash
# Apply in order: storage → service → route → deployment
oc apply -f opns/amplify-pvc.yaml
oc apply -f opns/amplify-service.yaml
oc apply -f opns/amplify-route.yaml
oc apply -f opns/amplify-deployment.yaml

# Wait for rollout
oc rollout status deployment/amplify-qea-grid --timeout=120s
```

### Step 9: Verify Deployment

```bash
# Check pod status
oc get pods -l app=amplify-qea-grid

# Expected output:
# NAME                           READY   STATUS    RESTARTS   AGE
# amplify-qea-grid-xxxxx-yyyyy        1/1     Running   0          30s

# Get the route URL
oc get route amplify-qea-grid -o jsonpath='{.spec.host}'

# Check logs
oc logs -f deployment/amplify-qea-grid

# Open in browser
open "https://$(oc get route amplify-qea-grid -o jsonpath='{.spec.host}')"
```

### Jira Hub (its own pod, at `/jira-hub`)

Jira Hub runs in its own pod (`jira-hub`, Streamlit on port 8501) and is reached on QEA's own host at **`https://<amplify-qea-grid host>/jira-hub`**: the `jira-hub` route claims the path `/jira-hub` of that host, the `amplify-qea-grid` route keeps everything else. `amplify-jfrog-deploy.sh` builds and deploys both; by hand:

```bash
oc apply -f opns/amplify-deployment-insights.yaml            # Deployment + Service (set the image first)
HOST=$(oc get route amplify-qea-grid -o jsonpath='{.spec.host}')
sed "s|AMPLIFY_HOST|$HOST|" opns/amplify-route-insights.yaml | oc apply -f -
oc delete route amplify-qea-insights --ignore-not-found       # legacy side-car routes
oc delete route amplify-qea-grid-insights --ignore-not-found
oc delete route amplify-insights --ignore-not-found
```

What makes it one app to users:

| | Setting | Where |
|---|---|---|
| Served under the path | `STREAMLIT_SERVER_BASE_URL_PATH=jira-hub` (probes on `/jira-hub/_stcore/health`) | `amplify-deployment-insights.yaml` |
| Where browsers go | `INSIGHTS_PUBLIC_URL=/jira-hub` | `amplify-deployment.yaml` |
| Where QEA checks it (and never starts its own Streamlit) | `INSIGHTS_INTERNAL_URL=http://jira-hub:8501/jira-hub` | `amplify-deployment.yaml` |
| Same sign-in | the same `JWT_SECRET_KEY` (both read `amplify-secrets`): QEA hands Insights a 60-second single-use token for the logged-in user; Insights' own sign-in form goes through `QEA_API_URL=http://amplify-qea-grid:8080` | both deployments |
| Same users and workspaces | both mount `amplify-data-pvc` at `/app/data` (`workspaces/<user id>/jira_insights`); the PVC is ReadWriteOnce, so the Insights pod has a pod affinity to the QEA pod's node (drop it with a ReadWriteMany class) | both deployments |
| WebSockets | `haproxy.router.openshift.io/timeout: 3600s` on the route | `amplify-route-insights.yaml` |

Users always enter through QEA (**Jira Hub** tab, or **↗ New tab**), already signed in as themselves.

### Distributed Grid

The OpenShift Grid consists of PostgreSQL, the Coordinator, and browser runner pods.

```bash
oc create secret generic playwright-db-credentials \
  --from-literal=postgres-user=testuser \
  --from-literal=postgres-password="$(openssl rand -hex 24)" \
  --from-literal=postgres-db=testdb \
  --from-literal=api-token="$(openssl rand -hex 32)" \
  --from-literal=pod-api-token="$(openssl rand -hex 32)" \
  --from-literal=database-url='postgresql://testuser:PASSWORD@postgres-service:5432/testdb' \
  --from-literal=keda-connection='postgresql://testuser:PASSWORD@postgres-service.NAMESPACE.svc.cluster.local:5432/testdb?sslmode=disable'

oc apply -f opns/grid-01-postgres.yaml
oc apply -f opns/grid-02-coordinator.yaml
```

The Coordinator image and runner images must be pushed to JFrog or the OpenShift internal registry before starting runners.

```bash
docker build -f Dockerfile.coordinator -t <registry>/playwright-coordinator:latest .
docker push <registry>/playwright-coordinator:latest

for browser in chromium firefox webkit; do
  docker build -f Dockerfile.grid-runner --build-arg BROWSER="$browser" \
    -t "<registry>/playwright-runner-${browser}:latest" .
  docker push "<registry>/playwright-runner-${browser}:latest"
done
```

Add `grid-coordinator-url` and `grid-api-token` to `amplify-secrets`, then restart Amplify so the deployment receives the values. The Coordinator supports `dev, qa, perf, prod, qa3, qa4, int`.

Start runner pods manually:

```bash
oc process -f opns/grid-03-runner-job.yaml \
  -p BROWSER=chromium -p PARALLELISM=4 \
  -p REGISTRY="<registry>" -p IMAGE_TAG=latest \
  | oc create -f -
```

For KEDA autoscaling:

```bash
oc process -f opns/grid-04-keda-scaledjob.yaml \
  -p BROWSER=chromium -p REGISTRY="<registry>" -p IMAGE_TAG=latest \
  | oc apply -f -
```

### Step 10: Create Admin User

```bash
# 1. Register at https://<route-url>/register
# 2. Promote to admin:
oc exec deployment/amplify-qea-grid -- \
  sqlite3 /app/data/amplify.db \
  "UPDATE users SET is_admin = 1 WHERE username = 'your-admin-username';"
```

---

## Automated Deploy Script

For a one-command deployment, use the included script:

```bash
JFROG_REGISTRY=yourcompany.jfrog.io \
JFROG_REPO=docker-local \
JFROG_USERNAME=your-email@company.com \
JFROG_PASSWORD=your-api-key \
./opns/amplify-jfrog-deploy.sh
```

The script handles all steps: build, tag, and push Amplify, Jira Hub, Coordinator, and runner images; create secrets; deploy Amplify, Grid, and verify rollouts.

---

## Updating the Deployment

When you make code changes and need to redeploy:

```bash
# 1. Rebuild
docker build -t amplify-qea-grid:latest .
docker tag amplify-qea-grid:latest yourcompany.jfrog.io/docker-local/amplify-qea-grid:latest

# 2. Push new image
docker push yourcompany.jfrog.io/docker-local/amplify-qea-grid:latest

# 3. Restart the deployment to pull the new image
oc rollout restart deployment/amplify-qea-grid

# 4. Watch rollout
oc rollout status deployment/amplify-qea-grid
```

For versioned releases, use tags:
```bash
docker tag amplify-qea-grid:latest yourcompany.jfrog.io/docker-local/amplify-qea-grid:v1.2.0
docker push yourcompany.jfrog.io/docker-local/amplify-qea-grid:v1.2.0

# Update deployment to use the new tag
oc set image deployment/amplify-qea-grid \
  amplify-qea-grid=yourcompany.jfrog.io/docker-local/amplify-qea-grid:v1.2.0
```

---

## What Gets Deployed

```
┌──────────────────────────────────────────────────────┐
│  OpenShift Project: amplify-qea-grid                      │
│                                                      │
│  ┌───────────────────────────────────────────────┐   │
│  │  Pod: amplify-qea-grid                             │   │
│  │  Image: jfrog.io/docker-local/amplify-qea-grid     │   │
│  │                                               │   │
│  │  Flask (gunicorn) ──── port 8080              │   │
│  │  Streamlit ─────────── port 8501              │   │
│  │                                               │   │
│  │  /app/data/ ← PVC (10Gi)                     │   │
│  │    ├── amplify.db           (user database)   │   │
│  │    ├── server_config.json   (Jira/Zephyr)     │   │
│  │    └── workspaces/                            │   │
│  │        ├── 1/  (user 1)                       │   │
│  │        │   ├── config/config.json             │   │
│  │        │   ├── history/report_history.json    │   │
│  │        │   ├── repos/ (cloned git repos)      │   │
│  │        │   └── jira_insights/ (datasets)      │   │
│  │        ├── 2/  (user 2)                       │   │
│  │        └── ...                                │   │
│  └───────────────────────────────────────────────┘   │
│                                                      │
│  Secrets:                                            │
│    amplify-secrets   (JWT key, Jira/Zephyr creds)    │
│    jfrog-pull-secret (Docker registry auth)          │
│                                                      │
│  Service:  amplify-qea-grid (ports 8080, 8501)            │
│  Route:    amplify-qea-grid (HTTPS, edge TLS)             │
│  PVC:      amplify-data-pvc (10Gi ReadWriteOnce)     │
└──────────────────────────────────────────────────────┘
```

---

## Manifest Files Reference

Files are named `<system>-<resource>`: `amplify-*` for Amplify QEA and Jira Hub, `grid-NN-*` for the Grid backend (`NN` is the apply order). The object names inside the manifests (`amplify-qea-grid`, `central-coordinator`, `coordinator-data`, ...) are deployed identifiers and are not tied to the file names.

| File | Resource | Purpose |
|------|----------|---------|
| `amplify-deployment.yaml` | Deployment | Pod spec, env vars from Secret, PVC mount, health probes, resource limits |
| `amplify-service.yaml` | Service | ClusterIP service for QEA, port 8080 |
| `amplify-route.yaml` | Route | HTTPS route with edge TLS termination |
| `amplify-pvc.yaml` | PersistentVolumeClaim | 10Gi storage for database and user workspaces |
| `amplify-secrets.yaml` | Secret (template) | Template for JWT key and Jira/Zephyr credentials |
| `amplify-deployment-insights.yaml` | Deployment + Service | Jira Hub in its own pod (port 8501, served under `/jira-hub`), same PVC and `JWT_SECRET_KEY` as QEA |
| `amplify-route-insights.yaml` | Route | The path `/jira-hub` of QEA's host → the `jira-hub` Service |
| `amplify-jfrog-deploy.sh` | Script | Automated build, push, and deploy of Amplify QEA, Jira Hub, Coordinator, and runner images |
| `grid-secrets.example.yaml` | Secret template | PostgreSQL and Grid API credentials; create it before applying Grid manifests |
| `grid-01-postgres.yaml` | PVC + Deployment + Service | Optional in-cluster PostgreSQL storage and service |
| `grid-02-coordinator.yaml` | ServiceAccount, Role, RoleBinding, PVC, Deployment, Service | Grid coordinator API and queue, its data volume, and read access to runner pods |
| `grid-03-runner-job.yaml` | Template | Manual batch trigger: one runner Job per browser (`oc process … \| oc create`) |
| `grid-04-keda-scaledjob.yaml` | Template | Optional KEDA ScaledJob per browser: one runner pod per pending chunk, scaling to 0 when idle |

---

## Environment Variables Reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `CONTAINER` | Auto | `1` | Set by Dockerfile — skips browser launch |
| `PORT` | Auto | `8080` | Flask/gunicorn listen port |
| `ALLURE3_BIN` | Auto | `/usr/local/bin` | Set by the image, which includes Node, npm and the Allure 3 CLI (`npm install -g allure`): the folder `echo $(npm config get prefix)/bin` prints. Default for the *Allure 3 binary path* in Config → Allure; makes Allure 3 the default report format |
| `JWT_SECRET_KEY` | **Yes** | — | Token signing key. Generate: `openssl rand -base64 32` |
| `JIRA_URL` | No | — | e.g. `https://yourcompany.atlassian.net` |
| `JIRA_USERNAME` | No | — | Atlassian email |
| `JIRA_API_TOKEN` | No | — | Atlassian API token |
| `ZEPHYR_ACCESS_KEY` | No | — | Zephyr for Jira Cloud access key |
| `ZEPHYR_SECRET_KEY` | No | — | Zephyr for Jira Cloud secret key |
| `ZEPHYR_ACCOUNT_ID` | No | — | Atlassian account ID |
| `JIRA_VERIFY_SSL` | No | `false` | SSL certificate verification |
| `INSIGHTS_PORT` | No | `8501` | Streamlit sidecar port |

---

## Day-2 Operations

### View Logs

```bash
oc logs -f deployment/amplify-qea-grid
```

### Check Database

```bash
oc exec deployment/amplify-qea-grid -- \
  sqlite3 /app/data/amplify.db "SELECT id, username, is_admin FROM users;"
```

### Backup Data

```bash
oc rsync $(oc get pod -l app=amplify-qea-grid -o name):/app/data/ ./amplify-backup/
```

### Restore Data

```bash
oc rsync ./amplify-backup/ $(oc get pod -l app=amplify-qea-grid -o name):/app/data/
oc rollout restart deployment/amplify-qea-grid
```

### Update Secrets

```bash
# Update Jira API token
oc patch secret amplify-secrets -p \
  '{"stringData":{"jira-api-token":"NEW_TOKEN_VALUE"}}'

# Restart to pick up changes
oc rollout restart deployment/amplify-qea-grid
```

### Update JFrog Pull Secret

```bash
oc delete secret jfrog-pull-secret
oc create secret docker-registry jfrog-pull-secret \
  --docker-server=yourcompany.jfrog.io \
  --docker-username=new-user@company.com \
  --docker-password=NEW_API_KEY
oc secrets link default jfrog-pull-secret --for=pull
```

### Delete Everything

```bash
oc delete deployment,service,route,pvc,secret -l app=amplify-qea-grid
oc delete secret jfrog-pull-secret
```

---

## Troubleshooting

### ImagePullBackOff

```bash
oc describe pod -l app=amplify-qea-grid | grep -A5 "Events"
```
- Check JFrog pull secret exists: `oc get secret jfrog-pull-secret`
- Check it's linked: `oc describe sa default | grep pull`
- Verify image path matches JFrog: `oc get deployment amplify-qea-grid -o jsonpath='{.spec.template.spec.containers[0].image}'`
- Test pull manually: `docker pull yourcompany.jfrog.io/docker-local/amplify-qea-grid:latest`

### CrashLoopBackOff

```bash
oc logs deployment/amplify-qea-grid --previous
```
- Usually a Python import error or missing dependency
- Check the Dockerfile built correctly

### Pending PVC

```bash
oc get pvc amplify-data-pvc
oc get sc   # list available storage classes
```
- If no default storage class, add `storageClassName` to amplify-pvc.yaml

### Permission Errors

OpenShift runs containers as a random non-root UID (GID 0). The Dockerfile handles this with `chmod g=u` and `chmod 777` on data directories.

```bash
oc exec deployment/amplify-qea-grid -- ls -la /app/data/
```

### Route Not Accessible

```bash
oc get route amplify-qea-grid -o yaml
```
- Check TLS termination matches your cluster configuration
- Try `insecureEdgeTerminationPolicy: Allow` for debugging

### Database Locked

SQLite under heavy concurrent writes. Solutions:
1. `oc rollout restart deployment/amplify-qea-grid`
2. For high concurrency, migrate to PostgreSQL

---

## Production Hardening Checklist

- [ ] `JWT_SECRET_KEY` generated with `openssl rand -base64 32`
- [ ] Route uses HTTPS (edge TLS termination)
- [ ] PVC storage adequate (10Gi default — increase for many users/repos)
- [ ] Resource limits set (default: 250m-2 CPU, 512Mi-2Gi memory)
- [ ] JFrog pull secret created and linked
- [ ] At least one admin user promoted
- [ ] Jira/Zephyr secrets configured via OpenShift Secret or Admin Panel
- [ ] Backup schedule configured for `/app/data/`
- [ ] Network policy restricts access if required
- [ ] Docker image scanned for CVEs
- [ ] Non-admin users cannot view Jira credentials (verified)
