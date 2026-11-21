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
#   ./openshift/jfrog-deploy.sh
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

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  Registry:  ${JFROG_REGISTRY}"
echo "  Repo:      ${JFROG_REPO}"
echo "  Image:     ${FULL_IMAGE}"
echo "  Project:   ${OC_PROJECT}"
echo "═══════════════════════════════════════════════════════════════"
echo ""

# ── Step 1: Build Docker image ───────────────────────────────────────
echo "▶ Step 1: Building Docker image..."
docker build -t amplify-qea:${IMAGE_TAG} .
docker tag amplify-qea:${IMAGE_TAG} ${FULL_IMAGE}
echo "✓ Image built and tagged: ${FULL_IMAGE}"
echo ""

# ── Step 2: Login to JFrog ───────────────────────────────────────────
echo "▶ Step 2: Logging into JFrog Artifactory..."
echo "${JFROG_PASSWORD}" | docker login ${JFROG_REGISTRY} -u ${JFROG_USERNAME} --password-stdin
echo "✓ Logged in to ${JFROG_REGISTRY}"
echo ""

# ── Step 3: Push to JFrog ────────────────────────────────────────────
echo "▶ Step 3: Pushing image to JFrog..."
docker push ${FULL_IMAGE}
echo "✓ Image pushed: ${FULL_IMAGE}"
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
oc apply -f openshift/pvc.yaml
oc apply -f openshift/service.yaml
oc apply -f openshift/route.yaml

# Create app secrets if they don't exist
if ! oc get secret amplify-secrets &>/dev/null; then
  echo "  Creating amplify-secrets (JWT key only — configure Jira from Admin Panel)..."
  oc create secret generic amplify-secrets \
    --from-literal=jwt-secret-key="$(openssl rand -base64 32)"
fi

# Apply deployment with JFrog image
sed "s|image:.*|image: ${FULL_IMAGE}|" openshift/deployment.yaml | \
  sed '/imagePullSecrets/d' | \
  sed "/spec:/a\\
\\      imagePullSecrets:\\
\\        - name: jfrog-pull-secret" | \
  oc apply -f -

echo "✓ Deployment applied"
echo ""

# ── Step 6: Wait for rollout ─────────────────────────────────────────
echo "▶ Step 6: Waiting for rollout..."
oc rollout status deployment/amplify-qea --timeout=120s

ROUTE_URL=$(oc get route amplify-qea -o jsonpath='{.spec.host}' 2>/dev/null || echo "")

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  ✅ DEPLOYMENT COMPLETE"
echo ""
echo "  Image:     ${FULL_IMAGE}"
echo "  Project:   ${OC_PROJECT}"
if [ -n "$ROUTE_URL" ]; then
echo "  URL:       https://${ROUTE_URL}"
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
