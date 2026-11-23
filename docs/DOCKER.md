# Docker: Build, Run, Test, and Ship

Use the **root Dockerfile** for the complete Amplify QEA application. The Dockerfile under `Jira_ReportBuilder/` is for the separate report-builder app.

## Verified Locally

- `amplify-qea:local` built successfully.
- Flask responded on `http://localhost:18080` and Docker reported `healthy`.
- Jira Insights started successfully and its Streamlit health endpoint returned `ok` on port 18501.
- `amplify-qea:runner` built successfully and launched headless Chromium to render a test page.
- Your external QA framework, Jira credentials, full suite, and Allure HTML generation have not been tested.

The local smoke-test container is named `amplify-qea-docker-test`. It uses a Docker volume named `amplify-qea-docker-test-insights` and does not mount your host configuration or QA repository.

## 1. Verify Docker

Start Docker Desktop, then open a terminal:

```bash
docker version
docker compose version
docker run --rm hello-world
cd /Users/admin/Documents/GitHub/Playwright-Executer
```

`docker version` should show both Client and Server. If the CLI is missing, enable Docker Desktop's CLI tools or add its installed CLI directory to PATH. If the daemon is unreachable, wait for Docker Desktop to start and inspect `docker context ls`.

## 2. Build the Application Image

```bash
docker build --pull -t amplify-qea:local .
```

This installs Flask, Gunicorn, Streamlit, and the application's dependencies. It does not install browser-test dependencies. Keep credentials, private datasets, and SSH keys outside the build context. `.dockerignore` excludes local configuration, workspaces, virtual environments, and agent logs; review other local files before sharing an image.

The current image runs as root under plain Docker. For shared or production hosting, test a non-root identity with suitable permissions on the mounted data and repository. The existing permission setup is not a guarantee that arbitrary UIDs can write every copied file.

## 3. Build the Browser-Enabled Runner

Skip this step if you only need the UI and Jira Insights.

```bash
docker build -f Dockerfile.runner -t amplify-qea:runner .
```

The runner extends the locally built application image. It installs pytest, pytest-playwright, pytest-xdist, allure-pytest, and Chromium into a Linux environment at `/opt/test-venv`. Firefox and WebKit are not installed.

Install your QA framework's additional dependencies in a derived image using `/opt/test-venv/bin/python -m pip install -r ...`. The runner packages are only the common baseline, not a substitute for your framework's requirements. Pin tested dependencies when preparing repeatable releases.

Do not mount a macOS virtual environment and expect it to work in Linux. Set `venv_path` explicitly to `/opt/test-venv` to avoid detecting a mounted host `.venv` or `venv`.

## 4. Prepare Persistent Configuration

Store runtime state outside this repository so it cannot enter the image build context:

```bash
mkdir -p "$HOME/.amplify-qea-docker/insights"
cp config.json "$HOME/.amplify-qea-docker/config.json"
chmod 600 "$HOME/.amplify-qea-docker/config.json"
```

Run the copy only for initial setup; repeating it overwrites your Docker-specific configuration. Protect this directory because the copied configuration may contain credentials.

Edit the copied configuration:

- Set `repo_root` to `/repos/qa`.
- Set `venv_path` to `/opt/test-venv` for the runner image.
- Clear stale `config_override_path` values or replace them with mounted container paths.
- Update pinned repositories, scripts, report paths, and Allure binary paths that refer to macOS locations.
- Check the QA repository's own `config.json`: repository configuration can override tool settings.

Jira Insights standalone workspaces live outside the source package. Local standalone runs use `~/.amplify-qea/jira_insights/`; container runs default to `/app/data/jira_insights_default/`. When an Amplify user is signed in, Insights writes to that user's `/app/data/workspaces/<id>/jira_insights/` directory instead.

## 5. Run Your Deployment Locally

Replace `/absolute/path/to/your-qa-framework` with an existing host repository. Docker Desktop must have access to that directory.

```bash
docker run -d --name amplify-qea \
  --restart unless-stopped --init --shm-size=1g \
  -p 127.0.0.1:8080:8080 \
  -p 127.0.0.1:8501:8501 \
  -e JIRA_INSIGHTS_HOME=/data/insights \
  --mount "type=bind,source=$HOME/.amplify-qea-docker/config.json,target=/app/config.json" \
  --mount "type=bind,source=$HOME/.amplify-qea-docker/insights,target=/data/insights" \
  --mount "type=bind,source=/absolute/path/to/your-qa-framework,target=/repos/qa" \
  amplify-qea:runner
```

Use `amplify-qea:local` instead for UI-only deployment. Open **http://localhost:8080**. Jira Insights starts when requested from its tab, not at container startup.

The config file, Insights workspace, and repository reports persist through bind mounts. Other changes inside the container do not survive replacement. The repository mount is writable because tests and file-management actions may write artifacts; mount only a trusted repository, not your entire home directory. Do not mount the Docker socket.

If host ports are busy, use `127.0.0.1:8081:8080` and `127.0.0.1:8502:8501`, and set `-e INSIGHTS_PUBLIC_URL=http://localhost:8502`. Change only the host side of the mappings: Gunicorn currently binds to container port 8080 regardless of the `PORT` environment variable.

## 6. Test Flask and Jira Insights

```bash
docker ps --filter name=amplify-qea
docker logs --tail 100 amplify-qea
curl --fail http://localhost:8080/ -o /dev/null
curl --fail http://localhost:8080/api/insights/status
curl --fail -X POST http://localhost:8080/api/insights/start
curl --fail --retry 10 --retry-all-errors --retry-delay 1 \
  http://localhost:8501/_stcore/health
docker inspect --format '{{.State.Status}} {{.State.Health.Status}}' amplify-qea
```

Expected: Flask succeeds, Streamlit returns `ok`, and Docker eventually reports `running healthy`. Streamlit starts asynchronously, and Docker's health check runs periodically; initial startup can briefly produce connection errors or a `starting` state. The Docker health check probes Flask only, not Jira connectivity or browsers.

If Insights fails:

```bash
docker exec amplify-qea tail -n 100 /data/insights/streamlit.log
```

For the already-running smoke-test container, substitute its name and use ports 18080 and 18501.

## 7. Test Browser Execution

This independent smoke test does not need Jira or an external website:

```bash
docker run --rm --init --shm-size=1g amplify-qea:runner \
  /opt/test-venv/bin/python -c 'from playwright.sync_api import sync_playwright; manager = sync_playwright().start(); browser = manager.chromium.launch(); page = browser.new_page(); page.set_content("<title>Docker smoke test</title>"); assert page.title() == "Docker smoke test"; browser.close(); manager.stop(); print("Chromium smoke test passed")'
```

Then select one known, harmless test in the Executor UI, choose Chromium, and leave headed mode disabled. Check its streamed logs, result status, and saved artifacts before running a full suite. Native desktop folder pickers are unavailable in containers; use the mounted `/repos/qa` path instead.

`allure-pytest` writes result files but does **not** install the Allure HTML-generation CLI. The application image (`Dockerfile`) includes Node.js, npm and the **Allure 3** CLI (`npm install -g allure`, release pinned by `--build-arg ALLURE_VERSION`, default 3.16.0), so Amplify can generate HTML reports in the container. `ALLURE3_BIN` is set to the npm bin folder (`echo $(npm config get prefix)/bin`, `/usr/local/bin` here); Amplify uses it as the default *Allure 3 binary path* (`allure3_bin`) and makes Allure 3 the default report format. Check it with `docker run --rm amplify-qea allure --version`. Allure 2 is not included: it needs Java, so if your framework uses it, add the compatible CLI and Java to a derived image. Maven and other external commands also need to be installed if your framework invokes them.

The `Dockerfile.runner` image extends the application image, so it has the Allure 3 CLI as well.

Tests accessing an application on your Mac should use `host.docker.internal`, not `localhost`. Inside Docker, `localhost` means the container itself. Headed runs require a display/Xvfb setup not included here.

## 8. Verify Persistence

Save a harmless setting, create a sample Insights report, and produce a small test artifact. Restart:

```bash
docker restart amplify-qea
```

After startup, verify those items remain. Start Insights again: its on-demand subprocess is not automatically restored. Restarting interrupts active test runs. Back up the config, Insights directory, and repository artifacts securely.

## 9. Ship the Tested Image

### Registry

Replace `YOUR_ACCOUNT` with your Docker Hub account, or use your organization's registry and namespace:

```bash
docker login
docker tag amplify-qea:runner YOUR_ACCOUNT/amplify-qea:1.0.0
docker push YOUR_ACCOUNT/amplify-qea:1.0.0
```

Enter registry credentials directly in the terminal. Recheck the build context before publishing. Use versioned tags or image digests rather than relying on `latest`. On the server:

```bash
docker pull YOUR_ACCOUNT/amplify-qea:1.0.0
```

### Offline Transfer

```bash
docker save -o "$HOME/amplify-qea-1.0.0.tar" amplify-qea:runner
```

Transfer securely, then run `docker load -i amplify-qea-1.0.0.tar` on the destination. Images and archives do not contain the mounted runtime configuration, repository, or Insights data; provision or transfer those separately.

### CPU Architecture

Apple Silicon builds usually target ARM64. An AMD64 server needs an AMD64 image. Build both base and runner for the destination architecture and retest using emulation or on that destination:

```bash
docker buildx build --platform linux/amd64 --load -t amplify-qea:local .
docker buildx build --platform linux/amd64 --load \
  -f Dockerfile.runner -t amplify-qea:runner .
```

Use `--platform linux/amd64` for corresponding local runs. These commands replace the local tags with AMD64 images; tag your previous images separately first if you need to keep them. Multi-platform publishing requires a published multi-platform base or a self-contained Dockerfile, not an assumption that the local base image exists on every platform.

## 10. Deploy on the Server

Prepare Docker and the three runtime mounts using server paths. Repeat step 5 using the published image tag or loaded image. Rerun Flask health, Insights health, Chromium smoke, one QA test, and persistence checks on the destination.

For remote access, place Flask and Streamlit behind an authenticated HTTPS reverse proxy or private VPN, keeping their published Docker ports bound to loopback. This app can execute commands and read/write mounted files; do not expose it unauthenticated to the public internet.

Set a stable random `AMPLIFY_SECRET_KEY` through your deployment secret mechanism to replace the development Flask secret. This does not provide user authentication. Set `INSIGHTS_PUBLIC_URL` to the browser-accessible HTTPS address of the proxied Streamlit service. A separate Insights hostname is simplest. The proxy needs WebSocket upgrades for Streamlit and long-lived, unbuffered SSE for execution logs; test authentication inside the iframe too.

Keep one Gunicorn worker and one application instance: subprocess handles and run state live in memory and are not shared between workers or replicas. Use a tested non-root identity and restrict access to credentials and writable mounts before production use.

For upgrades, stop active runs, back up persisted data, and replace the container using the same mounts and a new versioned image. Keep the previous tested image for rollback. Rolling back an image does not roll back data.

## Useful Commands

```bash
docker logs -f amplify-qea
docker exec -it amplify-qea sh
docker stop amplify-qea
docker start amplify-qea
```

Stopping a container does not delete mounted data. Removing containers or volumes is intentionally not part of this guide; preserve the data you need before cleanup.