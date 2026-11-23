#!/bin/bash
# Build and push the Grid images, then apply the K8s PostgreSQL, coordinator and runner manifests.
# This script performs deployment only when explicitly run; it is not executed by repository setup.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAMESPACE="${NAMESPACE:-amplify}"
REGISTRY="${REGISTRY:-}"
REPOSITORY="${REPOSITORY:-docker-local}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
RUNNER_BROWSERS="${RUNNER_BROWSERS:-chromium,firefox,webkit}"

if [ -z "$REGISTRY" ]; then
  echo "Set REGISTRY, for example: REGISTRY=registry.example.com" >&2
  exit 1
fi

docker build -f "$ROOT/Dockerfile.coordinator" -t "${REGISTRY}/${REPOSITORY}/playwright-coordinator:${IMAGE_TAG}" "$ROOT"
docker push "${REGISTRY}/${REPOSITORY}/playwright-coordinator:${IMAGE_TAG}"

for browser in $(echo "$RUNNER_BROWSERS" | tr ',' ' '); do
  docker build -f "$ROOT/Dockerfile.grid-runner" --build-arg "BROWSER=$browser" \
    -t "${REGISTRY}/${REPOSITORY}/playwright-runner-${browser}:${IMAGE_TAG}" "$ROOT"
  docker push "${REGISTRY}/${REPOSITORY}/playwright-runner-${browser}:${IMAGE_TAG}"
done

kubectl get namespace "$NAMESPACE" >/dev/null 2>&1 || kubectl create namespace "$NAMESPACE"

if ! kubectl -n "$NAMESPACE" get secret playwright-db-credentials >/dev/null 2>&1; then
  echo "Creating playwright-db-credentials. Set managed database values with your secret manager first if not using in-cluster PostgreSQL." >&2
  PG_PASSWORD="$(openssl rand -hex 24)"
  API_TOKEN="$(openssl rand -hex 32)"
  POD_API_TOKEN="$(openssl rand -hex 32)"
  kubectl -n "$NAMESPACE" create secret generic playwright-db-credentials \
    --from-literal=postgres-user=testuser \
    --from-literal=postgres-password="$PG_PASSWORD" \
    --from-literal=postgres-db=testdb \
    --from-literal=api-token="$API_TOKEN" \
    --from-literal=pod-api-token="$POD_API_TOKEN" \
    --from-literal=database-url="postgresql://testuser:${PG_PASSWORD}@postgres-service:5432/testdb" \
    --from-literal=keda-connection="postgresql://testuser:${PG_PASSWORD}@postgres-service.${NAMESPACE}.svc.cluster.local:5432/testdb?sslmode=disable"
fi

kubectl apply -n "$NAMESPACE" -k "$ROOT/k8s/grid-postgres"
kubectl apply -n "$NAMESPACE" -k "$ROOT/k8s/grid-coordinator"
kubectl -n "$NAMESPACE" patch deployment central-coordinator --type merge \
  -p "{\"spec\":{\"template\":{\"spec\":{\"containers\":[{\"name\":\"coordinator\",\"image\":\"${REGISTRY}/${REPOSITORY}/playwright-coordinator:${IMAGE_TAG}\"}]}}}}" >/dev/null

if kubectl -n "$NAMESPACE" get secret amplify-secrets >/dev/null 2>&1; then
  API_TOKEN="$(kubectl -n "$NAMESPACE" get secret playwright-db-credentials -o jsonpath='{.data.api-token}' | base64 --decode)"
  COORDINATOR_URL="http://central-coordinator-svc.${NAMESPACE}.svc.cluster.local:8000"
  kubectl -n "$NAMESPACE" patch secret amplify-secrets --type merge \
    -p "{\"stringData\":{\"grid-coordinator-url\":\"${COORDINATOR_URL}\",\"grid-api-token\":\"${API_TOKEN}\"}}" >/dev/null
  kubectl -n "$NAMESPACE" rollout restart deployment/amplify-qea-grid >/dev/null 2>&1 || true
fi

if kubectl api-resources --api-group=keda.sh >/dev/null 2>&1; then
  kubectl apply -n "$NAMESPACE" -k "$ROOT/k8s/grid-runners"
  for browser in $(echo "$RUNNER_BROWSERS" | tr ',' ' '); do
    kubectl -n "$NAMESPACE" patch scaledjob "playwright-${browser}-autoscaler" --type merge \
      -p "{\"spec\":{\"jobTargetRef\":{\"template\":{\"spec\":{\"containers\":[{\"name\":\"runner\",\"image\":\"${REGISTRY}/${REPOSITORY}/playwright-runner-${browser}:${IMAGE_TAG}\"}]}}}}}" >/dev/null 2>&1 || true
  done
else
  echo "KEDA is not installed; apply k8s/grid-runners/job-example.yaml manually." >&2
fi
