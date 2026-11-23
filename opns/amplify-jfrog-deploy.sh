#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════
# Amplify QEA — Build, Push to JFrog, Deploy on OpenShift
# ═══════════════════════════════════════════════════════════════════════
#
# Prerequisites:
#   1. Docker installed and running
#   2. oc CLI installed and logged into OpenShift
#   3. JFrog Artifactory access (URL, username, token/password)
#
# Usage:
#   ./opns/amplify-jfrog-deploy.sh
#
# Environment variables (set before running, or you'll be prompted):
#   JFROG_REGISTRY    — e.g. yourcompany.jfrog.io
#   JFROG_REPO        — e.g. docker-local (the Docker repo in Artifactory)
#   JFROG_USERNAME    — your JFrog username or email
#   JFROG_PASSWORD    — your JFrog password or API key
#   OC_PROJECT        — OpenShift project name (default: amplify-qea)
#   IMAGE_TAG         — image tag (default: latest)
# ═══════════════════════════════════════════════════════════════════════

set -e

# ── Collect variables ─────────────────────────────────────────────────
JFROG_REGISTRY="${JFROG_REGISTRY:-}"
JFROG_REPO="${JFROG_REPO:-docker-local}"
JFROG_USERNAME="${JFROG_USERNAME:-}"
JFROG_PASSWORD="${JFROG_PASSWORD:-}"
OC_PROJECT="${OC_PROJECT:-amplify-qea}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
RUNNER_BROWSERS="${RUNNER_BROWSERS:-chromium,firefox,webkit}"
DEPLOY_POSTGRES="${DEPLOY_POSTGRES:-1}"

if [ -z "$JFROG_REGISTRY" ]; then
  read -p "JFrog Registry (e.g. yourcompany.jfrog.io): " JFROG_REGISTRY
fi
if [ -z "$JFROG_USERNAME" ]; then
  read -p "JFrog Username: " JFROG_USERNAME
fi
if [ -z "$JFROG_PASSWORD" ]; then
  read -s -p "JFrog Password/API Key: " JFROG_PASSWORD
  echo
fi

FULL_IMAGE="${JFROG_REGISTRY}/${JFROG_REPO}/amplify-qea:${IMAGE_TAG}"
INSIGHTS_IMAGE="${JFROG_REGISTRY}/${JFROG_REPO}/amplify-insights:${IMAGE_TAG}"   # Jira Insights: its own pod
COORDINATOR_IMAGE="${JFROG_REGISTRY}/${JFROG_REPO}/playwright-coordinator:${IMAGE_TAG}"

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  Registry:  ${JFROG_REGISTRY}"
echo "  Repo:      ${JFROG_REPO}"
echo "  Image:     ${FULL_IMAGE}"
echo "  Insights:  ${INSIGHTS_IMAGE}"
echo "  Coordinator: ${COORDINATOR_IMAGE}"
echo "  Runners:   ${RUNNER_BROWSERS}"
echo "  Project:   ${OC_PROJECT}"
echo "═══════════════════════════════════════════════════════════════"
echo ""

# ── Step 1: Build Docker image ───────────────────────────────────────
echo "▶ Step 1: Building Docker image..."
docker build -t amplify-qea:${IMAGE_TAG} .
docker tag amplify-qea:${IMAGE_TAG} ${FULL_IMAGE}
docker build -f Dockerfile.insights -t amplify-insights:${IMAGE_TAG} .
docker tag amplify-insights:${IMAGE_TAG} ${INSIGHTS_IMAGE}
docker build -f Dockerfile.coordinator -t playwright-coordinator:${IMAGE_TAG} .
docker tag playwright-coordinator:${IMAGE_TAG} ${COORDINATOR_IMAGE}

for browser in $(echo "${RUNNER_BROWSERS}" | tr ',' ' '); do
  runner_image="${JFROG_REGISTRY}/${JFROG_REPO}/playwright-runner-${browser}:${IMAGE_TAG}"
  docker build -f Dockerfile.grid-runner --build-arg BROWSER="${browser}" -t "playwright-runner-${browser}:${IMAGE_TAG}" .
  docker tag "playwright-runner-${browser}:${IMAGE_TAG}" "${runner_image}"
done
echo "✓ Amplify, Insights, Coordinator and runner images built"
echo ""

# ── Step 2: Login to JFrog ───────────────────────────────────────────
echo "▶ Step 2: Logging into JFrog Artifactory..."
echo "${JFROG_PASSWORD}" | docker login ${JFROG_REGISTRY} -u ${JFROG_USERNAME} --password-stdin
echo "✓ Logged in to ${JFROG_REGISTRY}"
echo ""

# ── Step 3: Push to JFrog ────────────────────────────────────────────
echo "▶ Step 3: Pushing image to JFrog..."
docker push ${FULL_IMAGE}
docker push ${INSIGHTS_IMAGE}
docker push ${COORDINATOR_IMAGE}
for browser in $(echo "${RUNNER_BROWSERS}" | tr ',' ' '); do
  docker push "${JFROG_REGISTRY}/${JFROG_REPO}/playwright-runner-${browser}:${IMAGE_TAG}"
done
echo "✓ Images pushed: ${FULL_IMAGE}, ${INSIGHTS_IMAGE}"
echo ""

# ── Step 4: Create OpenShift pull secret ─────────────────────────────
echo "▶ Step 4: Creating OpenShift pull secret..."
oc project ${OC_PROJECT} 2>/dev/null || oc new-project ${OC_PROJECT}

# Delete existing pull secret if present, then recreate
oc delete secret jfrog-pull-secret --ignore-not-found=true

oc create secret docker-registry jfrog-pull-secret \
  --docker-server=${JFROG_REGISTRY} \
  --docker-username=${JFROG_USERNAME} \
  --docker-password=${JFROG_PASSWORD}

# Link pull secret to default service account
oc secrets link default jfrog-pull-secret --for=pull
echo "✓ Pull secret created and linked"
echo ""

# ── Step 5: Deploy ───────────────────────────────────────────────────
echo "▶ Step 5: Deploying to OpenShift..."

# Apply PVC, Service, Route
oc apply -f opns/amplify-pvc.yaml
oc apply -f opns/amplify-service.yaml
oc apply -f opns/amplify-route.yaml

# Create app secrets if they don't exist
if ! oc get secret amplify-secrets &>/dev/null; then
  echo "  Creating amplify-secrets (JWT key only — configure Jira from Admin Panel)..."
  oc create secret generic amplify-secrets \
    --from-literal=jwt-secret-key="$(openssl rand -base64 32)"
fi

# Apply a deployment with its JFrog image and the pull secret (added to the pod spec: the 4-space "spec:")
apply_with_image() {
  sed "s|image:.*|image: $2|" "$1" | \
    sed '/imagePullSecrets/d' | \
    sed "/^    spec:/a\\
\\      imagePullSecrets:\\
\\        - name: jfrog-pull-secret" | \
    oc apply -f -
}
apply_with_image opns/amplify-deployment.yaml "${FULL_IMAGE}"

# Jira Insights in its own pod, at /jira-insights on the same host as QEA (same users, same workspaces: the
# shared amplify-data PVC and the same JWT_SECRET_KEY). Drop the side-car route of older versions.
oc delete route amplify-qea-insights --ignore-not-found=true
apply_with_image opns/amplify-deployment-insights.yaml "${INSIGHTS_IMAGE}"
AMPLIFY_HOST=$(oc get route amplify-qea -o jsonpath='{.spec.host}')
sed "s|AMPLIFY_HOST|${AMPLIFY_HOST}|" opns/amplify-route-insights.yaml | oc apply -f -

# Add Grid credentials and deploy the Grid backend.
if [ -z "${GRID_API_TOKEN:-}" ]; then
  GRID_API_TOKEN="$(openssl rand -hex 32)"
fi
if [ -z "${POD_API_TOKEN:-}" ]; then
  POD_API_TOKEN="$(openssl rand -hex 32)"
fi
if [ -z "${PG_PASSWORD:-}" ]; then
  PG_PASSWORD="$(openssl rand -hex 24)"
fi

oc delete secret playwright-db-credentials --ignore-not-found=true
DATABASE_URL="postgresql://testuser:${PG_PASSWORD}@postgres-service:5432/testdb"
KEDA_CONNECTION="postgresql://testuser:${PG_PASSWORD}@postgres-service.${OC_PROJECT}.svc.cluster.local:5432/testdb?sslmode=disable"
oc create secret generic playwright-db-credentials \
  --from-literal=postgres-user=testuser \
  --from-literal=postgres-password="${PG_PASSWORD}" \
  --from-literal=postgres-db=testdb \
  --from-literal=api-token="${GRID_API_TOKEN}" \
  --from-literal=pod-api-token="${POD_API_TOKEN}" \
  --from-literal=database-url="${DATABASE_URL}" \
  --from-literal=keda-connection="${KEDA_CONNECTION}"

oc patch secret amplify-secrets --type merge -p \
  "{\"stringData\":{\"grid-coordinator-url\":\"http://central-coordinator-svc:8000\",\"grid-api-token\":\"${GRID_API_TOKEN}\"}}" || true

if [ "${DEPLOY_POSTGRES}" = "1" ]; then
  oc apply -f opns/grid-01-postgres.yaml
fi

sed "s|artifactory.lipstack.sephoraus.com:443/playwright-coordinator:latest|${COORDINATOR_IMAGE}|" \
  opns/grid-02-coordinator.yaml | oc apply -f -

for browser in $(echo "${RUNNER_BROWSERS}" | tr ',' ' '); do
  runner_image="${JFROG_REGISTRY}/${JFROG_REPO}/playwright-runner-${browser}:${IMAGE_TAG}"
  if oc api-resources --api-group=keda.sh -o name 2>/dev/null | grep -q keda; then
    oc process -f opns/grid-04-keda-scaledjob.yaml \
      -p BROWSER="${browser}" \
      -p REGISTRY="${JFROG_REGISTRY}/${JFROG_REPO}" \
      -p IMAGE_TAG="${IMAGE_TAG}" \
      | oc apply -f -
  else
    echo "KEDA is not installed; runner ${browser} is not autoscaled. Use grid-03 manually."
  fi
done

oc rollout restart deployment/amplify-qea deployment/amplify-insights

echo "✓ Deployments applied"
echo ""

# ── Step 6: Wait for rollout ─────────────────────────────────────────
echo "▶ Step 6: Waiting for rollout..."
oc rollout status deployment/amplify-qea --timeout=120s
oc rollout status deployment/amplify-insights --timeout=180s
oc rollout status deployment/central-coordinator --timeout=180s

ROUTE_URL=$(oc get route amplify-qea -o jsonpath='{.spec.host}' 2>/dev/null || echo "")

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  ✅ DEPLOYMENT COMPLETE"
echo ""
echo "  Image:     ${FULL_IMAGE}"
echo "  Project:   ${OC_PROJECT}"
if [ -n "$ROUTE_URL" ]; then
echo "  URL:       https://${ROUTE_URL}"
echo "  Insights:  https://${ROUTE_URL}/jira-insights  (opened from the Jira Insights tab, signed in)"
fi
echo ""
echo "  Next steps:"
echo "    1. Register at https://${ROUTE_URL}/register"
echo "    2. Promote to admin:"
echo "       oc exec deployment/amplify-qea -- \\"
echo "         sqlite3 /app/data/amplify.db \\"
echo "         \"UPDATE users SET is_admin = 1 WHERE username = 'YOUR_USER';\""
echo "    3. Configure Jira/Zephyr in Admin Panel"
echo "═══════════════════════════════════════════════════════════════"
