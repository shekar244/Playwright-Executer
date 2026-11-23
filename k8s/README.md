# Amplify QEA — Kubernetes deployment

Plain Kubernetes (any distribution with an NGINX ingress). On OpenShift use [`../opns`](../opns/README.md) instead — it uses Routes and the OpenShift registry.

Two independent apps share one volume (users database, `server_config.json`, every user's workspace):

| App | Image | Port | Reached at |
|---|---|---|---|
| **amplify-qea** — login, Executor, Dashboard, Admin Panel… | `amplify-qea` | 8080 | `https://amplify.example.com` |
| **amplify-insights** — Jira dashboards and sprint reports | `amplify-insights` | 8501 | `https://amplify.example.com/jira-insights` |

Signing in happens once, in QEA. Opening Jira Insights (embedded tab or **New tab**) goes through QEA, which hands Insights a short-lived signed token — that is why both apps need the **same `JWT_SECRET_KEY`**. Insights is served under `/jira-insights` on QEA's own host (`STREAMLIT_SERVER_BASE_URL_PATH=jira-insights`; the second Ingress sends that path to it); QEA sends browsers there (`INSIGHTS_PUBLIC_URL=/jira-insights`) and checks it on the service (`INSIGHTS_INTERNAL_URL`), never starting a Streamlit process of its own.

## Prerequisites

- `kubectl` pointed at your cluster, with permission to create a namespace, deployments, services, ingresses, PVCs and secrets
- An NGINX ingress controller (`ingressClassName: nginx`) and one DNS name pointing at it
- A storage class that can provision a 10Gi volume (ReadWriteMany is nicer — see [Storage](#storage))
- Optional: cert-manager for automatic TLS; a registry pull secret if the images are private (e.g. JFrog)

## Deploy

```bash
cd amplify

# 1. Your hostname — replace amplify.example.com in k8s/amplify-ingress.yaml (both Ingresses: QEA at /,
#    Jira Insights at /jira-insights)

# 2. Namespace and secrets (secrets are never applied from a file)
kubectl create namespace amplify
kubectl -n amplify create secret generic amplify-secrets \
  --from-literal=jwt-secret-key="$(openssl rand -base64 32)"
# optional Jira/Zephyr defaults — or set them later in Admin Panel → Server Secrets:
#   --from-literal=jira-url=... --from-literal=jira-username=... --from-literal=jira-api-token=... \
#   --from-literal=zephyr-access-key=... --from-literal=zephyr-secret-key=... --from-literal=zephyr-account-id=...

# 3. TLS (skip if cert-manager issues it — see the annotation in amplify-ingress.yaml)
kubectl -n amplify create secret tls amplify-tls    --cert=amplify.crt  --key=amplify.key
kubectl -n amplify create secret tls insights-tls   --cert=insights.crt --key=insights.key

# 4. Preview, then apply
kubectl kustomize k8s/ | less
kubectl apply -k k8s/

# 5. Watch it come up
kubectl -n amplify get pods -w
```

## Distributed Grid

The K8s Grid uses separate manifests and a `playwright-db-credentials` Secret:

```bash
kubectl -n amplify create secret generic playwright-db-credentials \
  --from-literal=postgres-user=testuser \
  --from-literal=postgres-password="$(openssl rand -hex 24)" \
  --from-literal=postgres-db=testdb \
  --from-literal=api-token="$(openssl rand -hex 32)" \
  --from-literal=pod-api-token="$(openssl rand -hex 32)" \
  --from-literal=database-url='postgresql://testuser:PASSWORD@postgres-service:5432/testdb' \
  --from-literal=keda-connection='postgresql://testuser:PASSWORD@postgres-service.amplify.svc.cluster.local:5432/testdb?sslmode=disable'

kubectl apply -n amplify -k k8s/grid-postgres
kubectl apply -n amplify -k k8s/grid-coordinator
kubectl apply -n amplify -k k8s/grid-runners
```

Add `grid-coordinator-url` and `grid-api-token` to `amplify-secrets`. The coordinator accepts `qa3`, `qa4`, `int`, `dev`, `qa`, `perf`, and `prod`.

Use `k8s/deploy-grid.sh` to build/push the coordinator and runner images and apply these manifests when a registry and namespace are configured.

The first account to register becomes the admin. To promote another user later (they must have registered first):

```bash
kubectl -n amplify exec deploy/amplify-qea -- \
  python -c "from amplify_server.models.user import User; u=User.get_by_username('NAME'); User.set_admin(u.id, True)"
```

## Images

`kustomization.yaml` defaults to the public Docker Hub images. To use another registry (JFrog):

```bash
kubectl -n amplify create secret docker-registry registry-pull-secret \
  --docker-server=yourcompany.jfrog.io --docker-username=USER --docker-password=API_KEY
```

then uncomment `imagePullSecrets` in both deployments and set `newName` in `kustomization.yaml` to e.g. `yourcompany.jfrog.io/docker-local/amplify-qea`. Pin a real tag in `newTag` rather than `latest`, and `kubectl apply -k k8s/` again.

## Update, roll back, remove

```bash
kubectl apply -k k8s/                                  # after changing a tag or manifest
kubectl -n amplify rollout status deploy/amplify-qea
kubectl -n amplify rollout undo deploy/amplify-qea     # roll back
kubectl delete -k k8s/                                 # remove Amplify (the grid-* directories are separate)
```

> `kubectl delete -k` also deletes the PVC, and with it every user and workspace. Back up first:
>
> ```bash
> kubectl -n amplify cp "$(kubectl -n amplify get pod -l app=amplify-qea -o jsonpath='{.items[0].metadata.name}')":/app/data ./backup
> ```

## Storage

Both apps mount the same volume. `ReadWriteOnce` (the default here) allows pods on **one node**, so `amplify-deployment-insights.yaml` carries a `podAffinity` that keeps Insights next to the QEA pod. With a `ReadWriteMany` class (NFS, CephFS, EFS, Azure Files) set `accessModes: [ReadWriteMany]` in `amplify-pvc.yaml` and delete the `podAffinity` block.

Both deployments use `strategy: Recreate` and `replicas: 1` on purpose: the users database is SQLite, so two QEA pods must never run at once — not even briefly during a rollout.

## Distributed Grid

Create the Grid credentials Secret, apply PostgreSQL and the Coordinator, then apply runner workloads:

```bash
kubectl -n amplify create secret generic playwright-db-credentials \
  --from-literal=postgres-user=testuser \
  --from-literal=postgres-password="$(openssl rand -hex 24)" \
  --from-literal=postgres-db=testdb \
  --from-literal=api-token="$(openssl rand -hex 32)" \
  --from-literal=pod-api-token="$(openssl rand -hex 32)" \
  --from-literal=database-url='postgresql://testuser:PASSWORD@postgres-service:5432/testdb' \
  --from-literal=keda-connection='postgresql://testuser:PASSWORD@postgres-service.amplify.svc.cluster.local:5432/testdb?sslmode=disable'

kubectl apply -n amplify -k k8s/grid-postgres
kubectl apply -n amplify -k k8s/grid-coordinator
kubectl apply -n amplify -k k8s/grid-runners
```

Set `grid-coordinator-url` and `grid-api-token` in `amplify-secrets` so QEA can reach the Coordinator. The `GRID_COORDINATOR_URL` value is normally `http://central-coordinator-svc.amplify.svc.cluster.local:8000` when Grid and Amplify share a namespace.

The Coordinator supports `dev, qa, perf, prod, qa3, qa4, int`. KEDA must be installed before applying `grid-runners`; without KEDA, use `k8s/grid-runners/job-example.yaml`.

`k8s/deploy-grid.sh` is the image/build/deploy helper:

```bash
REGISTRY=registry.example.com REPOSITORY=docker-local NAMESPACE=amplify \
  k8s/deploy-grid.sh
```

## Manifest files and the Grid backend

Files are named `<system>-<resource>`, as in `opns/`: `amplify-*` for Amplify QEA and Jira Insights, `grid-*` for the Grid backend (`kustomization.yaml` keeps its name because kustomize requires it). `kubectl apply -k k8s/` deploys **Amplify only**. Each part of the Grid is its own directory, a separate kustomize base, applied on its own; none of them sets a namespace, so pass `-n <namespace>`.

| Path | Contents | Apply |
|---|---|---|
| `kustomization.yaml`, `amplify-*.yaml` | Amplify QEA and Jira Insights: namespace, PVC, two Deployments, Service, Ingress | `kubectl apply -k k8s/` |
| `amplify-secrets.example.yaml` | Example of the Amplify Secret | not applied from a file — create it with `kubectl` (see [Deploy](#deploy)) |
| `grid-secrets.example.yaml` | Example of the Grid Secret `playwright-db-credentials` (database URL, API tokens) | create it before the coordinator; no kustomization references it |
| `grid-postgres/` | Optional in-cluster PostgreSQL (StatefulSet + Service) for dev/test; skip it for a managed database | `kubectl apply -n <ns> -k k8s/grid-postgres` |
| `grid-coordinator/` | Coordinator API and queue: ServiceAccount, Role, RoleBinding, data PVC, Deployment, Service | `kubectl apply -n <ns> -k k8s/grid-coordinator` |
| `grid-runners/` | KEDA ScaledJobs, one per browser (needs KEDA); `job-example.yaml` is a manual Job for clusters without KEDA | `kubectl apply -n <ns> -k k8s/grid-runners`, or `kubectl create -n <ns> -f k8s/grid-runners/job-example.yaml` |

## Differences from the OpenShift manifests

| | `k8s/` | `opns/` |
|---|---|---|
| External access | `Ingress` (nginx), TLS secret / cert-manager | `Route` (edge TLS) |
| Image source | registry via `kustomization.yaml` (+ optional pull secret) | OpenShift internal registry or JFrog |
| Parameters | Kustomize (`images`, `namespace`) | `sed` on `${NAMESPACE}` |
| User IDs | image default user (root) | random non-root UID, group 0 (the image is group-writable for this) |
| Rollout / volume | `Recreate` + pod affinity | default rolling update |
| CLI | `kubectl` | `oc` |

## Troubleshooting

- **Insights tab says it can't connect / blank** — QEA checks `INSIGHTS_INTERNAL_URL` (`http://amplify-insights:8501/jira-insights`) and sends browsers to `INSIGHTS_PUBLIC_URL` (`/jira-insights`); the Insights pod must run with `STREAMLIT_SERVER_BASE_URL_PATH=jira-insights` and the `amplify-insights` Ingress must route `/jira-insights` of the same host: `kubectl -n amplify describe ingress amplify-insights`.
- **Jira Insights asks you to sign in again** — the two apps have different `JWT_SECRET_KEY` values; both read it from `amplify-secrets`, so check that only one secret is in play and restart both deployments after changing it.
- **Insights pod stays `Pending`** — it is waiting for the QEA pod to be scheduled (affinity), or the PVC is unbound: `kubectl -n amplify get pvc,pods`.
- **Live test logs arrive in bursts** — a proxy in front of nginx is buffering; the QEA ingress already sets `proxy-buffering: "off"`.

## Validation status

The manifests render with `kubectl kustomize` and every cross-reference (selectors, ports, services, secret and volume names, ingress backends, hostnames) is checked by script, but they have **not been applied to a live cluster**. Run `kubectl apply -k k8s/ --dry-run=server` against yours first.
