# ── Jira Report Builder — Docker / OpenShift image ───────────────────────────
#
# Build:  docker build -t jira-report-builder .
# Run:    docker run -p 8501:8501 -v jira-rb-data:/app/workspace \
#           -e JIRA_URL=https://yourcompany.atlassian.net \
#           -e JIRA_USERNAME=you@company.com -e JIRA_API_TOKEN=... \
#           jira-report-builder
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Datasets, saved reports, chart specs and the saved Jira connection live here —
# mount a volume to keep them. OpenShift runs as a random UID in group 0.
ENV JIRA_INSIGHTS_HOME=/app/workspace
RUN mkdir -p /app/workspace && chgrp -R 0 /app && chmod -R g=u /app

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8501/_stcore/health', timeout=5).read().strip() == b'ok' else 1)"

CMD ["python", "-m", "streamlit", "run", "app.py", \
     "--server.address", "0.0.0.0", "--server.port", "8501", "--server.headless", "true"]
