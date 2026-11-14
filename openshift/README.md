# Amplify QEA — OpenShift Deployment Guide

## Prerequisites

| Requirement | Details |
|-------------|---------|
| **OpenShift CLI** | `oc` installed and logged in (`oc login`) |
| **Namespace/Project** | An OpenShift project to deploy into (`oc project myproject`) |
| **Image Registry** | Access to push images (internal registry or external like Quay/Docker Hub) |
| **PVC Support** | Storage class available for persistent volume claims |
| **Git** | To clone this repository |
| **Docker** (optional) | To build locally before pushing; or use OpenShift S2I/binary build |

### Access Requirements
- `oc` CLI authenticated with permissions to create deployments, services, routes, secrets, PVCs
- If using internal registry: `oc registry login` or `podman login`

---

## Quick Deploy (5 Steps)

### Step 1: Create the Project

```bash
oc new-project amplify-qea --display-name="Amplify QEA"
# or switch to existing:
oc project amplify-qea
```

### Step 2: Create Secrets

```bash
# Generate a strong JWT secret and create the secret
oc create secret generic amplify-secrets \
  --from-literal=jwt-secret-key="$(openssl rand -base64 32)" \
  --from-literal=jira-url="https://yourcompany.atlassian.net" \
  --from-literal=jira-username="qa@yourcompany.com" \
  --from-literal=jira-api-token="YOUR_JIRA_API_TOKEN" \
  --from-literal=zephyr-access-key="YOUR_ZEPHYR_ACCESS_KEY" \
  --from-literal=zephyr-secret-key="YOUR_ZEPHYR_SECRET_KEY" \
  --from-literal=zephyr-account-id="YOUR_ATLASSIAN_ACCOUNT_ID"
```

**If you don't have Jira/Zephyr yet**, skip those — they're optional and can be added later from the Admin Panel:
```bash
oc create secret generic amplify-secrets \
  --from-literal=jwt-secret-key="$(openssl rand -base64 32)"
```

### Step 3: Build & Push Image

**Option A: Binary build (from local source)**
```bash
cd /path/to/amplify
oc new-build --binary --name=amplify-qea --strategy=docker
oc start-build amplify-qea --from-dir=. --follow
```

**Option B: Push to external registry**
```bash
# Build locally
docker build -t quay.io/yourorg/amplify-qea:latest .
docker push quay.io/yourorg/amplify-qea:latest

# Update deployment.yaml image field to:
#   image: quay.io/yourorg/amplify-qea:latest
```

**Option C: Build from Git repo**
```bash
oc new-build https://github.com/yourorg/amplify.git \
  --name=amplify-qea --strategy=docker
```

### Step 4: Deploy

```bash
# Apply manifests in order
oc apply -f openshift/pvc.yaml
oc apply -f openshift/service.yaml
oc apply -f openshift/route.yaml

# For binary build, update the image reference first:
NAMESPACE=$(oc project -q)
sed "s/\${NAMESPACE}/$NAMESPACE/" openshift/deployment.yaml | oc apply -f -

# Or for external registry, edit deployment.yaml image field and apply:
oc apply -f openshift/deployment.yaml
```

### Step 5: Verify & Access

```bash
# Check pod is running
oc get pods -l app=amplify-qea

# Check route URL
oc get route amplify-qea -o jsonpath='{.spec.host}'

# View logs
oc logs -f deployment/amplify-qea

# Open in browser
open "https://$(oc get route amplify-qea -o jsonpath='{.spec.host}')"
```

---

## What Gets Deployed

```
┌─────────────────────────────────────────────────┐
│  OpenShift Project: amplify-qea                 │
│                                                 │
│  ┌──────────────────────────────────────────┐   │
│  │  Pod: amplify-qea                        │   │
│  │                                          │   │
│  │  Flask (gunicorn) ──── port 8080         │   │
│  │  Streamlit ─────────── port 8501         │   │
│  │                                          │   │
│  │  /app/data/ ← PVC (10Gi)                │   │
│  │    ├── amplify.db        (SQLite)        │   │
│  │    ├── server_config.json (Jira creds)   │   │
│  │    └── workspaces/                       │   │
│  │        ├── 1/  (user 1)                  │   │
│  │        │   ├── config/config.json        │   │
│  │        │   ├── history/                  │   │
│  │        │   ├── repos/  (cloned repos)    │   │
│  │        │   └── jira_insights/            │   │
│  │        ├── 2/  (user 2)                  │   │
│  │        └── ...                           │   │
│  └──────────────────────────────────────────┘   │
│                                                 │
│  Secret: amplify-secrets                        │
│    jwt-secret-key, jira-*, zephyr-*             │
│                                                 │
│  Service: amplify-qea (8080, 8501)              │
│  Route: amplify-qea (HTTPS, edge TLS)           │
│  PVC: amplify-data-pvc (10Gi)                   │
└─────────────────────────────────────────────────┘
```

---

## Environment Variables Reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `CONTAINER` | Yes | `1` | Set by Dockerfile; skips browser launch |
| `PORT` | Yes | `8080` | Flask/gunicorn listen port |
| `JWT_SECRET_KEY` | **Yes** | — | **Must be set**. Token signing key. Generate: `openssl rand -base64 32` |
| `JIRA_URL` | No | — | Jira instance URL |
| `JIRA_USERNAME` | No | — | Jira/Atlassian email |
| `JIRA_API_TOKEN` | No | — | Jira API token |
| `ZEPHYR_ACCESS_KEY` | No | — | Zephyr for Jira Cloud access key |
| `ZEPHYR_SECRET_KEY` | No | — | Zephyr for Jira Cloud secret key |
| `ZEPHYR_ACCOUNT_ID` | No | — | Atlassian account ID |
| `JIRA_VERIFY_SSL` | No | `false` | Set to `true` for SSL verification |
| `INSIGHTS_PORT` | No | `8501` | Streamlit sidecar port |
| `INSIGHTS_PUBLIC_URL` | No | — | Public URL for Streamlit if behind separate route |

---

## Post-Deployment Setup

### 1. Create Admin User

The first user to register becomes a regular user. Promote them to admin:

```bash
# Option A: Via the database directly
oc exec deployment/amplify-qea -- \
  sqlite3 /app/data/amplify.db \
  "UPDATE users SET is_admin = 1 WHERE username = 'your-admin-username';"

# Option B: Register, then promote via API (after promoting one admin)
# Use the admin panel at https://your-route/admin
```

### 2. Configure Jira/Zephyr (if not done via env vars)

1. Login as admin
2. Go to Admin Panel (user menu > Admin Panel)
3. Fill in Server Secrets section
4. Click Save

### 3. Test the Deployment

```bash
# Register a user
curl -X POST "https://$(oc get route amplify-qea -o jsonpath='{.spec.host}')/api/auth/register" \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"SecurePass123!"}'

# Login
curl -X POST "https://$(oc get route amplify-qea -o jsonpath='{.spec.host}')/api/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"SecurePass123!"}'
```

---

## Day-2 Operations

### Update the Image

```bash
# Binary build (from local changes)
oc start-build amplify-qea --from-dir=. --follow

# The deployment auto-rolls out after build completes
oc rollout status deployment/amplify-qea
```

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
# Copy the entire data directory locally
oc rsync $(oc get pod -l app=amplify-qea -o name):/app/data/ ./backup/
```

### Restore Data

```bash
oc rsync ./backup/ $(oc get pod -l app=amplify-qea -o name):/app/data/
```

### Scale (not recommended for SQLite)

The app uses SQLite and in-process state, so **keep replicas at 1**. For multi-replica scaling, migrate to PostgreSQL first.

### Update Secrets

```bash
# Update a single secret value
oc patch secret amplify-secrets -p \
  '{"stringData":{"jira-api-token":"NEW_TOKEN_VALUE"}}'

# Restart pod to pick up new secrets
oc rollout restart deployment/amplify-qea
```

### Delete Everything

```bash
oc delete deployment,service,route,pvc,secret -l app=amplify-qea
```

---

## Troubleshooting

### Pod not starting

```bash
oc describe pod -l app=amplify-qea
oc logs deployment/amplify-qea --previous
```

Common issues:
- **ImagePullBackOff**: Image not found in registry. Check `oc get builds`.
- **CrashLoopBackOff**: Check logs for Python errors.
- **Pending PVC**: No storage class available. Check `oc get sc`.

### Permission errors

OpenShift runs as a random non-root UID. The Dockerfile sets `chmod g=u` on `/app` and `chmod 777` on `/app/data`, which handles this. If files are still inaccessible:

```bash
oc exec deployment/amplify-qea -- ls -la /app/data/
```

### Database locked

SQLite can lock under concurrent writes. If you see "database is locked" errors, the app is receiving too many concurrent writes. Solutions:
1. Restart the pod: `oc rollout restart deployment/amplify-qea`
2. Migrate to PostgreSQL for high-concurrency use

### Route not accessible

```bash
oc get route amplify-qea
# Check TLS settings match your cluster
```

---

## Production Hardening Checklist

- [ ] **JWT_SECRET_KEY**: Generated with `openssl rand -base64 32` (not a default)
- [ ] **TLS**: Route uses `edge` termination (HTTPS)
- [ ] **PVC**: Adequate storage (10Gi default, increase for many users/repos)
- [ ] **Resource limits**: CPU/memory set in deployment.yaml
- [ ] **Backups**: Scheduled `oc rsync` of /app/data/ to backup storage
- [ ] **Admin user**: At least one user promoted to admin
- [ ] **Jira secrets**: Set via OpenShift Secret (not hardcoded)
- [ ] **Network policy**: Restrict ingress to route only (if required)
- [ ] **Image scanning**: Scan Docker image for CVEs before deploying
