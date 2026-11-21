#!/usr/bin/env bash
# Build and start the local Docker deployment described in config/docker-compose.yml.
# First run creates config/.env (git-ignored, mode 600) with fresh random secrets; later runs reuse it.
#
#   scripts/local-up.sh            build + start (Amplify, coordinator, PostgreSQL) and build the runner image
#   scripts/local-up.sh down       stop the stack (volumes, and so users and run history, are kept)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/config/.env"
compose=(docker compose -f "$ROOT/config/docker-compose.yml" --env-file "$ENV_FILE")

if [ ! -f "$ENV_FILE" ]; then
  ( umask 077
    {
      echo "PG_PASSWORD=$(openssl rand -hex 16)"
      echo "GRID_API_TOKEN=$(openssl rand -hex 24)"
      echo "POD_API_TOKEN=$(openssl rand -hex 24)"
      echo "JWT_SECRET_KEY=$(openssl rand -hex 32)"
    } > "$ENV_FILE" )
  echo "Created $ENV_FILE with fresh random secrets"
fi

if [ "${1:-up}" = "down" ]; then
  "${compose[@]}" down
  exit 0
fi

"${compose[@]}" up -d --build --wait
"${compose[@]}" --profile runners build runner

echo
echo "Amplify QEA:    http://localhost:8080"
echo "Jira Insights:  http://localhost:8501  (opened from the Insights tab)"
echo "Runner pods:    python3 $ROOT/scripts/local_pod_launcher.py"
