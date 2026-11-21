# Amplify QEA — OpenShift Deployment Guide

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
│  Source Code     │──push───→ │  docker-local/   │────────→ │  Pod: amplify-qea       │
│  + Dockerfile    │           │  amplify-qea     │          │  ├── Flask :8080         │
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

docker build -t amplify-qea:latest .
```

Verify the build:
```bash
docker images | grep amplify-qea
```

### Step 2: Tag for JFrog Artifactory

```bash
# Format: <jfrog-registry>/<docker-repo>/amplify-qea:<tag>
docker tag amplify-qea:latest yourcompany.jfrog.io/docker-local/amplify-qea:latest
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
docker push yourcompany.jfrog.io/docker-local/amplify-qea:latest
```

Verify in JFrog UI: Artifactory > docker-local > amplify-qea > latest

### Step 4: Create OpenShift Project

```bash
oc login https://your-openshift-cluster

oc new-project amplify-qea --display-name="Amplify QEA"
# or switch to existing:
oc project amplify-qea
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

Edit `openshift/deployment.yaml` — change the image line to your JFrog path:

```yaml
# Find this line:
image: image-registry.openshift-image-registry.svc:5000/${NAMESPACE}/amplify-qea:latest

# Change to:
image: yourcompany.jfrog.io/docker-local/amplify-qea:latest
```

Add `imagePullSecrets` under `spec.template.spec`:
```yaml
spec:
  template:
    spec:
      imagePullSecrets:
        - name: jfrog-pull-secret
      containers:
        - name: amplify-qea
          image: yourcompany.jfrog.io/docker-local/amplify-qea:latest
```

### Step 8: Deploy All Resources

```bash
# Apply in order: storage → service → route → deployment
oc apply -f openshift/pvc.yaml
oc apply -f openshift/service.yaml
oc apply -f openshift/route.yaml
oc apply -f openshift/deployment.yaml

# Wait for rollout
oc rollout status deployment/amplify-qea --timeout=120s
```

### Step 9: Verify Deployment

```bash
# Check pod status
oc get pods -l app=amplify-qea

# Expected output:
# NAME                           READY   STATUS    RESTARTS   AGE
# amplify-qea-xxxxx-yyyyy        1/1     Running   0          30s

# Get the route URL
oc get route amplify-qea -o jsonpath='{.spec.host}'

# Check logs
oc logs -f deployment/amplify-qea

# Open in browser
open "https://$(oc get route amplify-qea -o jsonpath='{.spec.host}')"
```

### Step 10: Create Admin User

```bash
# 1. Register at https://<route-url>/register
# 2. Promote to admin:
oc exec deployment/amplify-qea -- \
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
./openshift/jfrog-deploy.sh
```

The script handles all steps: build, tag, push, create secrets, deploy, and verify.

---

## Updating the Deployment

When you make code changes and need to redeploy:

```bash
# 1. Rebuild
docker build -t amplify-qea:latest .
docker tag amplify-qea:latest yourcompany.jfrog.io/docker-local/amplify-qea:latest

# 2. Push new image
docker push yourcompany.jfrog.io/docker-local/amplify-qea:latest

# 3. Restart the deployment to pull the new image
oc rollout restart deployment/amplify-qea

# 4. Watch rollout
oc rollout status deployment/amplify-qea
```

For versioned releases, use tags:
```bash
docker tag amplify-qea:latest yourcompany.jfrog.io/docker-local/amplify-qea:v1.2.0
docker push yourcompany.jfrog.io/docker-local/amplify-qea:v1.2.0

# Update deployment to use the new tag
oc set image deployment/amplify-qea \
  amplify-qea=yourcompany.jfrog.io/docker-local/amplify-qea:v1.2.0
```

---

## What Gets Deployed

```
┌──────────────────────────────────────────────────────┐
│  OpenShift Project: amplify-qea                      │
│                                                      │
│  ┌───────────────────────────────────────────────┐   │
│  │  Pod: amplify-qea                             │   │
│  │  Image: jfrog.io/docker-local/amplify-qea     │   │
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
│  Service:  amplify-qea (ports 8080, 8501)            │
│  Route:    amplify-qea (HTTPS, edge TLS)             │
│  PVC:      amplify-data-pvc (10Gi ReadWriteOnce)     │
└──────────────────────────────────────────────────────┘
```

---

## Manifest Files Reference

| File | Resource | Purpose |
|------|----------|---------|
| `deployment.yaml` | Deployment | Pod spec, env vars from Secret, PVC mount, health probes, resource limits |
| `service.yaml` | Service | ClusterIP service exposing ports 8080 (Flask) and 8501 (Streamlit) |
| `route.yaml` | Route | HTTPS route with edge TLS termination |
| `pvc.yaml` | PersistentVolumeClaim | 10Gi storage for database and user workspaces |
| `secrets.yaml` | Secret (template) | Template for JWT key and Jira/Zephyr credentials |
| `jfrog-deploy.sh` | Script | Automated build, push, and deploy |

---

## Environment Variables Reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `CONTAINER` | Auto | `1` | Set by Dockerfile — skips browser launch |
| `PORT` | Auto | `8080` | Flask/gunicorn listen port |
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
oc logs -f deployment/amplify-qea
```

### Check Database

```bash
oc exec deployment/amplify-qea -- \
  sqlite3 /app/data/amplify.db "SELECT id, username, is_admin FROM users;"
```

### Backup Data

```bash
oc rsync $(oc get pod -l app=amplify-qea -o name):/app/data/ ./amplify-backup/
```

### Restore Data

```bash
oc rsync ./amplify-backup/ $(oc get pod -l app=amplify-qea -o name):/app/data/
oc rollout restart deployment/amplify-qea
```

### Update Secrets

```bash
# Update Jira API token
oc patch secret amplify-secrets -p \
  '{"stringData":{"jira-api-token":"NEW_TOKEN_VALUE"}}'

# Restart to pick up changes
oc rollout restart deployment/amplify-qea
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
oc delete deployment,service,route,pvc,secret -l app=amplify-qea
oc delete secret jfrog-pull-secret
```

---

## Troubleshooting

### ImagePullBackOff

```bash
oc describe pod -l app=amplify-qea | grep -A5 "Events"
```
- Check JFrog pull secret exists: `oc get secret jfrog-pull-secret`
- Check it's linked: `oc describe sa default | grep pull`
- Verify image path matches JFrog: `oc get deployment amplify-qea -o jsonpath='{.spec.template.spec.containers[0].image}'`
- Test pull manually: `docker pull yourcompany.jfrog.io/docker-local/amplify-qea:latest`

### CrashLoopBackOff

```bash
oc logs deployment/amplify-qea --previous
```
- Usually a Python import error or missing dependency
- Check the Dockerfile built correctly

### Pending PVC

```bash
oc get pvc amplify-data-pvc
oc get sc   # list available storage classes
```
- If no default storage class, add `storageClassName` to pvc.yaml

### Permission Errors

OpenShift runs containers as a random non-root UID (GID 0). The Dockerfile handles this with `chmod g=u` and `chmod 777` on data directories.

```bash
oc exec deployment/amplify-qea -- ls -la /app/data/
```

### Route Not Accessible

```bash
oc get route amplify-qea -o yaml
```
- Check TLS termination matches your cluster configuration
- Try `insecureEdgeTerminationPolicy: Allow` for debugging

### Database Locked

SQLite under heavy concurrent writes. Solutions:
1. `oc rollout restart deployment/amplify-qea`
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
