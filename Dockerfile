# ── Amplify QEA — OpenShift / Docker Image ────────────────────────────────────
#
# Build:   docker build -t amplify-qea .
# Run:     docker run -p 8080:8080 amplify-qea
# OpenShift: oc new-build --binary --name=amplify-qea --strategy=docker
#            oc start-build amplify-qea --from-dir=. --follow
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.13-slim

# ── System dependencies ───────────────────────────────────────────────────────
# git  : needed if the app clones/pulls repos at runtime
# curl : healthcheck and misc tooling
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        curl \
    && rm -rf /var/lib/apt/lists/*

# ── App directory ─────────────────────────────────────────────────────────────
WORKDIR /app

# OpenShift runs containers as a random non-root UID in the root group (GID 0).
# Making /app group-writable lets OpenShift write config.json / report_history.json.
RUN chown -R 0:0 /app && chmod -R g=u /app

# ── Python dependencies ───────────────────────────────────────────────────────
# Copy requirements first so Docker layer-caches the pip install step.
# The layer is only rebuilt when requirements.txt changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Application source ────────────────────────────────────────────────────────
COPY . .

# ── Runtime configuration ─────────────────────────────────────────────────────
# PORT=8080   : OpenShift routes external traffic to 8080 by default
# CONTAINER=1 : tells server.py to skip browser launch and hosts-file edit
ENV PORT=8080
ENV CONTAINER=1

# Jira Insights (Streamlit side-car) — started on demand from the Jira Insights tab.
# On OpenShift expose 8501 with its own route and set INSIGHTS_PUBLIC_URL to it.
ENV INSIGHTS_PORT=8501

EXPOSE 8080
EXPOSE 8501

# ── Health check ──────────────────────────────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8080/ || exit 1

# ── Start command ─────────────────────────────────────────────────────────────
# workers=1  : app uses in-process state (SSE stream, subprocess handle);
#              multiple workers would not share that state.
# threads=8  : handles concurrent SSE + API requests within the single worker.
# timeout=300: long timeout for test runs that can take several minutes.
CMD ["gunicorn", "server:app", \
     "--bind", "0.0.0.0:8080", \
     "--workers", "1", \
     "--threads", "8", \
     "--timeout", "300", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
