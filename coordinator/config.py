"""Environment-driven settings for the coordinator."""
import os

# No default: credentials must be injected (Kubernetes Secret / env), never baked in.
DATABASE_URL = os.environ["DATABASE_URL"]
# Optional shared secret. When set, every /api/* call must send X-API-Token.
# This is the FULL token (Amplify, the seeder): it may call every endpoint.
API_TOKEN = os.getenv("API_TOKEN", "")
# Optional POD token for the runner pods. Test code runs inside those pods and can read the token
# they hold, so it only opens the endpoints a pod needs (see coordinator.py: pod_api). Unset: pods
# use the full token as before. Equal to API_TOKEN: it is simply the full token.
POD_API_TOKEN = os.getenv("POD_API_TOKEN", "")

SUPPORTED_BROWSERS = ("edge", "chrome", "chromium", "firefox", "webkit")
DEFAULT_BROWSER = "edge"
SUPPORTED_ENVIRONMENTS = [
    e.strip().lower()
    for e in os.getenv("SUPPORTED_ENVIRONMENTS", "dev,qa,perf,prod").split(",")
    if e.strip()
]
DEFAULT_ENVIRONMENT = os.getenv("DEFAULT_ENVIRONMENT", SUPPORTED_ENVIRONMENTS[0]).lower()
DEFAULT_CHUNK_SIZE = 3
# Capabilities clients can probe in GET /api/config before relying on them (Amplify checks this
# before packaging a repo): runs carry repo/sha and PUT/GET /api/code stores snapshots; runners
# upload Allure results and GET /api/runs/{id}/report builds the report; "lease-tokens": a lease
# with ?lease_tokens=1 returns a per-chunk lease_token (X-Lease-Token); "pod-token": the
# coordinator understands POD_API_TOKEN.
FEATURES = ["code-delivery", "allure-reports", "lease-tokens", "pod-token"]

# A busy/idle runner that has not heartbeated for this long is reported as "lost".
RUNNER_STALE_SECONDS = int(os.getenv("RUNNER_STALE_SECONDS", "60"))
# A chunk leased to a runner that has not heartbeated for this long (crash, OOMKill, node loss)
# is returned to the queue by the coordinator's reaper, which checks every REAPER_INTERVAL_SECONDS.
LEASE_TIMEOUT_SECONDS = int(os.getenv("LEASE_TIMEOUT_SECONDS", "180"))
REAPER_INTERVAL_SECONDS = int(os.getenv("REAPER_INTERVAL_SECONDS", "30"))
RUNNER_POD_SELECTOR = os.getenv("RUNNER_POD_SELECTOR", "app=playwright-runner")
KUBE_SA_DIR = "/var/run/secrets/kubernetes.io/serviceaccount"
