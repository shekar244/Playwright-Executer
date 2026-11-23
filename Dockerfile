# ── Amplify QEA — OpenShift / Docker Image ────────────────────────────────────
#
# Build (public):   docker build -t amplify-qea-grid .
# Build (JFrog):    docker build --build-arg BASE_REGISTRY=yourcompany.jfrog.io/docker-remote/ -t amplify-qea-grid .
# Run:              docker run -p 8080:8080 amplify-qea-grid
# ─────────────────────────────────────────────────────────────────────────────

ARG BASE_REGISTRY=
ARG NODE_VERSION=24.14.0

# Stage 1: only the source of Node.js + npm for the final image (the Allure 3 CLI is installed there, below).
FROM ${BASE_REGISTRY}node:${NODE_VERSION}-bookworm-slim AS node

FROM ${BASE_REGISTRY}python:3.13-slim

# ── System dependencies ───────────────────────────────────────────────────────
# git  : needed if the app clones/pulls repos at runtime
# curl : healthcheck and misc tooling
#
# APT_PROXY: optional JFrog Debian remote repo URL to avoid direct internet access
#   Build with: --build-arg APT_PROXY=https://yourcompany.jfrog.io/artifactory/debian-remote
ARG APT_PROXY=
RUN if [ -n "$APT_PROXY" ]; then \
      echo "deb $APT_PROXY bookworm main" > /etc/apt/sources.list && \
      echo "deb $APT_PROXY bookworm-updates main" >> /etc/apt/sources.list; \
    fi && \
    apt-get update && apt-get install -y --no-install-recommends \
        git \
    && rm -rf /var/lib/apt/lists/*

# ── Node.js, npm and the Allure 3 CLI (HTML report generation) ────────────────
# `npm install -g allure` puts the CLI in $(npm config get prefix)/bin, i.e. /usr/local/bin here; ALLURE3_BIN
# is that folder, which Amplify uses as the default for the "Allure 3 binary path" (allure3_bin) setting and
# which makes Allure 3 the default report format. Pin another release with --build-arg ALLURE_VERSION=<x.y.z>
# (3.16.0 is the tested one). Allure 2 needs Java and is not included.
ARG ALLURE_VERSION=3.16.0
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm && \
    ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx && \
    npm install -g --omit=dev --no-audit --no-fund --no-update-notifier "allure@${ALLURE_VERSION}" && \
    npm cache clean --force
ENV ALLURE3_BIN=/usr/local/bin
RUN test "$(npm config get prefix)/bin" = "$ALLURE3_BIN" && "$ALLURE3_BIN/allure" --version

# ── App directory ─────────────────────────────────────────────────────────────
WORKDIR /app

# OpenShift runs containers as a random non-root UID in the root group (GID 0).
# Making /app group-writable lets OpenShift write config.json / report_history.json.
RUN chown -R 0:0 /app && chmod -R g=u /app

# ── Python dependencies ───────────────────────────────────────────────────────
# Copy requirements first so Docker layer-caches the pip install step.
# The layer is only rebuilt when amplify-requirements.txt changes.
COPY amplify-requirements.txt .
RUN pip install --no-cache-dir --trusted-host pypi.org --trusted-host pypi.python.org --trusted-host files.pythonhosted.org -r amplify-requirements.txt

# ── Application source ────────────────────────────────────────────────────────
COPY . .

# ── Runtime configuration ─────────────────────────────────────────────────────
ENV PORT=8080
ENV CONTAINER=1
ENV INSIGHTS_PORT=8501
ENV HOME=/tmp
ENV PYTHONPATH=/app/src

EXPOSE 8080
EXPOSE 8501

# ── Permissions ──────────────────────────────────────────────────────────────
# OpenShift runs as a random non-root UID in GID 0.
# Everything must be group-writable. Set AFTER copy so all files get it.
RUN mkdir -p /app/data /app/workspaces /tmp/.gunicorn /tmp/.streamlit && \
    chown -R 0:0 /app && chmod -R g=u /app && \
    chmod -R 777 /app/data /app/workspaces /tmp/.gunicorn /tmp/.streamlit

# ── Health check ──────────────────────────────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/login', timeout=5)" || exit 1

# ── Start command ─────────────────────────────────────────────────────────────
CMD ["sh", "-c", "python -m amplify_server.models.db && gunicorn amplify_server.server:app --bind 0.0.0.0:8080 --workers 1 --threads 16 --timeout 300 --access-logfile - --error-logfile -"]
