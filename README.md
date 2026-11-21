# Amplify QEA

A multi-tenant, server-deployable platform for running, reporting, and managing **Playwright + pytest** test suites — with user authentication, isolated workspaces, and admin controls.

Deployable on **Docker**, **OpenShift**, **Render.com**, or any container platform. Each user registers, gets their own workspace, and runs tests without interfering with others.

---

## Features

| Tab | What it does |
|---|---|
| **Executor** | Clone repos via Git, create venvs, pip install, run pytest with full control: browser, suite, file, test filter (-k), marker (-m), workers (-n), headed/headless, extra args |
| **Reporting Dashboard** | Every test run in one view — Grid runs and your local Executor runs together. Filter by source, environment, browser, pod and time frame; see consolidated KPIs, charts (distribution, by browser, by environment, daily trend) and a pods table; drill into a run to see which pod executed each chunk, read its logs, and open its HTML reports — for Grid runs the run's Allure 3 report — without leaving the page |
| **Grid** | Run tests on distributed Playwright runner pods (OpenShift): pick tests, environment and browsers, start a run, and watch live pod status and per-test results. The tests run from a snapshot of the Executor's selected repo at its committed `HEAD` (uncommitted changes are not included and the tab says so). Needs a coordinator: an admin sets it in **Admin Panel → Grid Coordinator** (or via `GRID_COORDINATOR_URL`) |
| **Test Management** | Full Zephyr for Jira Cloud — import tests, bulk upload results, view/mark executions, hierarchical metrics |
| **File Manager** | **Admins only.** Browse and edit `.json`, `.yaml`, `.env`, `.csv`, `.xlsx` files with dedicated editors per format. It reaches any path on the server (including the saved Jira / Zephyr / Grid secrets), so its tab is shown to admins only and its endpoints reject everyone else |
| **AI Studio** | AI-powered test assistance |
| **Jira Insights** | Streamlit-based reporting — pull Jira issues with JQL, build pivot reports and charts, or a **Table** report of just the columns you pick (downloadable as CSV and Excel; the **Key** column links to the issue in Jira). **Sprint Reports** include stories and defects carried forward out of each sprint, and KPI tiles can be plain, full colour or gradient (runs on port 8501) |
| **Config** | Repository config, features/scripts, git commands, CLI tools, Allure settings, JFrog Artifactory, field mappings |
| **Admin Panel** | User management, password resets, enable/disable accounts, promote/demote admins, usage metrics, activity log, server secrets, tab and Jira Insights option visibility for all users |

---

## Architecture

```
┌──────────────────────────────────────────────────────┐
│  Amplify QEA Server                                  │
│                                                      │
│  Flask (gunicorn) ──── port 8080                     │
│  Streamlit (Jira Insights) ── port 8501              │
│                                                      │
│  /app/data/ (persistent volume)                      │
│    ├── amplify.db            (SQLite: users, sessions,│
│    │                          activity log)           │
│    ├── server_config.json    (Jira/Zephyr secrets)   │
│    └── workspaces/                                   │
│        ├── 1/  (user 1)                              │
│        │   ├── config/config.json                    │
│        │   ├── history/report_history.json           │
│        │   ├── repos/  (cloned git repos)            │
│        │   └── jira_insights/  (datasets, reports)   │
│        ├── 2/  (user 2, fully isolated)              │
│        └── ...                                       │
└──────────────────────────────────────────────────────┘
```

---

## Quick Start

### Docker (recommended)

```bash
docker build -t amplify-qea .
docker run -p 8080:8080 -p 8501:8501 \
  -e JWT_SECRET_KEY="$(openssl rand -base64 32)" \
  -e CONTAINER=1 \
  -v amplify-data:/app/data \
  amplify-qea
```

Open `http://localhost:8080/register` to create your account. The first registered user is automatically promoted to admin.

### Docker Hub

```bash
docker pull shekar244/amplify-qea:latest
docker run -p 8080:8080 -p 8501:8501 \
  -e JWT_SECRET_KEY="$(openssl rand -base64 32)" \
  -e CONTAINER=1 \
  -v amplify-data:/app/data \
  shekar244/amplify-qea:latest
```

### Local Development

```bash
bash amplify.sh
```

On first run the launcher creates `./venv` (Python 3.13 preferred), installs `requirements.txt` into it — including the Jira Insights dependencies — initializes the database and opens http://amplify-qea:7777. Jira Insights starts from its tab using the same venv. To run without the launcher:

```bash
python3 -m venv venv && venv/bin/pip install -r requirements.txt
venv/bin/python -m amplify.server
```

### Render.com

1. Use image: `docker.io/shekar244/amplify-qea:latest`
2. Set env var: `JWT_SECRET_KEY` = (generate with `openssl rand -base64 32`)
3. Set env var: `CONTAINER` = `1`
4. Add disk: mount path `/app/data`
5. Deploy

### OpenShift

See [opns/README.md](opns/README.md) for full deployment guide with JFrog Artifactory integration.

### Kubernetes

See [k8s/README.md](k8s/README.md) — Ingress + Kustomize manifests for any cluster with an NGINX ingress (`kubectl apply -k k8s/`).

---

## Authentication & Multi-Tenancy

### User Registration & Login

- Register at `/register` with username, password, optional email
- Login at `/login` — JWT token issued (24-hour expiration, httpOnly cookie)
- First registered user is auto-promoted to admin
- Disabled accounts are blocked from login
- **Change your password** any time: user menu → **Change password** (asks for your current password). Passwords are 6–72 bytes.
- **After an admin reset** the password is temporary: the user is sent to the Change password page when they sign in and can't use anything else until they choose their own. The requirement applies at once, even to sessions they already had open, and the Admin Panel marks them "Must change password" until they do.

### User Isolation

Every user gets a completely isolated workspace:
- **Config** — separate `config.json` per user
- **History** — separate `report_history.json` per user
- **Repos** — cloned repos stored in user's workspace
- **Jira Insights** — datasets and reports per user
- No cross-user data visibility

### Admin Panel (`/admin`)

Accessible only to admin users (via user dropdown menu):

| Feature | Description |
|---|---|
| **User Management** | List all users with role, status, last login, disk usage |
| **Password Reset** | Set a temporary password for any user; they must choose their own at next sign-in |
| **Enable/Disable** | Toggle account active status |
| **Promote/Demote** | Grant or revoke admin role |
| **Delete User** | Remove user and their entire workspace |
| **Metrics** | Total users, active today/week, events, disk usage |
| **Activity Log** | Who did what, when, from which IP |
| **Server Secrets** | Configure Jira/Zephyr credentials (admin only) |
| **Grid Coordinator** | Set the Grid coordinator URL and API token for every user. Saving checks the connection and reports how many environments and browsers the coordinator offers. The token is write-only — never shown again. Overrides `GRID_COORDINATOR_URL` / `GRID_API_TOKEN`; clear the URL to fall back to them |
| **Tab Visibility** | Choose which main tabs and Config sub-tabs every user sees, and which tab they **land on** after signing in or reloading (default: Executor; if the chosen tab is later hidden, users land on the first visible tab). Server-wide, admin only |
| **Jira Insights Options** | Choose which Insights sidebar options (JQL / Zephyr / Sprint / Upload, Excluded fields, Colour theme) and views (Dashboard, Sprint Reports, Report Builder, Explorer, Data) every user sees. Hidden options are not rendered at all |

### Admin Bootstrap (for platforms without shell access)

Set env var `ADMIN_BOOTSTRAP_TOKEN`, then:

```bash
curl -X POST https://your-app.com/api/admin/bootstrap \
  -H "Content-Type: application/json" \
  -d '{"username": "your-user", "token": "your-bootstrap-token"}'
```

---

## Git Clone Workflow (Server Mode)

Since the server runs in Docker, users clone repos instead of browsing local files:

1. **Config → Repo** — save repo URL, branch, and access token
2. **Executor → Clone Repository** — clone into user workspace
3. **Environment** — create `.venv`, pip install dependencies
4. **Branch** — switch branches from dropdown
5. **Discover Tests** — find suites and test files
6. **Run** — execute pytest as usual

Supports GitHub, GitLab, Bitbucket with PAT authentication. JFrog Artifactory pip.ini auto-generated if configured.

---

## Server Secrets (Jira / Zephyr)

Credentials are managed at the server level — users never see or configure them:

**Priority:** Environment variables → `server_config.json` → not configured

| Environment Variable | Description |
|---|---|
| `JIRA_URL` | Jira instance URL |
| `JIRA_USERNAME` | Atlassian email |
| `JIRA_API_TOKEN` | Atlassian API token |
| `ZEPHYR_ACCESS_KEY` | Zephyr for Jira Cloud access key |
| `ZEPHYR_SECRET_KEY` | Zephyr for Jira Cloud secret key |
| `ZEPHYR_ACCOUNT_ID` | Atlassian account ID |

Or configure from Admin Panel → Server Secrets section.

Non-admin users see only that Jira is configured and the project key — no credentials, not even masked.

---

## Themes

8 built-in color themes, selectable from the header dropdown:

| Theme | Type | Description |
|---|---|---|
| Light | Light | Default white theme |
| Dark | Dark | Apple-style dark |
| Midnight | Dark | Deep navy blue |
| Nord | Dark | Cool blue-gray |
| Solarized | Light | Warm cream |
| Rosé | Dark | Pink/mauve |
| Forest | Dark | Emerald green |
| Ocean Blue | Light | Soft blue |

Theme persists across pages and sessions via localStorage.

---

## Mobile Responsive

The UI adapts to phone and tablet screens:
- Executor panels stack vertically
- Dashboard cards reflow (4 → 2 → 1 columns)
- Tables scroll horizontally
- Compact header and nav
- 16px inputs prevent iOS zoom

---

## Requirements

| Dependency | Version | Notes |
|---|---|---|
| Python | 3.10+ | 3.13 recommended |
| Flask | 3.0+ | Web server |
| bcrypt | 4.1+ | Password hashing |
| PyJWT | 2.8+ | JWT authentication |
| gunicorn | 21+ | Production WSGI server |
| openpyxl | 3.1+ | Excel file editing |
| pyyaml | 6.0+ | YAML file editing |

Jira Insights additionally requires: `streamlit`, `pygwalker`, `plotly`, `pandas`

```bash
pip install -r requirements.txt
```

---

## Project Layout

```
amplify-qea/
├── amplify.sh / amplify.bat         # Local launchers
├── requirements.txt
├── Dockerfile
├── opns/                            # OpenShift manifests (Routes, OpenShift registry)
│   ├── amplify-deployment.yaml / amplify-deployment-insights.yaml
│   ├── amplify-service.yaml / amplify-route.yaml / amplify-route-insights.yaml
│   ├── amplify-pvc.yaml / amplify-secrets.yaml
│   ├── amplify-jfrog-deploy.sh      # Build + push + deploy script
│   └── grid-01-postgres.yaml … grid-04-keda-scaledjob.yaml   # Grid backend: PostgreSQL, coordinator, runner job, KEDA
├── k8s/                             # Plain Kubernetes manifests (Ingress, Kustomize)
│   ├── kustomization.yaml           # kubectl apply -k k8s/  (Amplify)
│   ├── amplify-deployment.yaml / amplify-deployment-insights.yaml / amplify-service.yaml
│   ├── amplify-ingress.yaml / amplify-pvc.yaml / amplify-namespace.yaml
│   ├── amplify-secrets.example.yaml # template — create the real secret with kubectl
│   ├── grid-secrets.example.yaml    # template — Grid database URL and API tokens
│   └── grid-coordinator/ grid-postgres/ grid-runners/   # Grid backend, one kustomize base each
└── amplify/                         # Application package
    ├── server.py                    # Flask app — registers blueprints, auth middleware
    ├── auth_utils.py                # JWT, @require_auth, @require_admin
    ├── server_secrets.py            # Server-level Jira/Zephyr credential resolution
    ├── storage.py                   # User workspace path management
    ├── user_config_reader.py        # User-scoped config reader
    ├── config_reader.py             # Base config reader
    ├── command_builder.py           # pytest command builder
    ├── runner.py                    # Subprocess + SSE streaming
    ├── test_discovery.py            # Suite and test file discovery
    ├── models/
    │   ├── db.py                    # SQLite schema, migrations
    │   └── user.py                  # User model, ActivityLog
    ├── config/
    │   └── settings.py              # App settings (DB path, JWT, workspaces)
    ├── routes/
    │   ├── auth.py                  # Register, login, logout, refresh
    │   ├── admin.py                 # User management, metrics, server config
    │   ├── admin_bootstrap.py       # One-time admin promotion endpoint
    │   ├── repo.py                  # Git clone, pull, branch, venv, pip
    │   ├── executor.py              # Test runs, discovery, features, venv
    │   ├── config.py                # Config read/write
    │   ├── dashboard.py             # Results, history, reports
    │   ├── zephyr.py                # Zephyr/Jira integration
    │   ├── filemanager.py           # File browse/read/write
    │   ├── git.py                   # Git commands
    │   ├── studio.py                # AI Studio
    │   ├── insights.py              # Jira Insights launcher
    │   ├── history.py               # Run history persistence
    │   └── state.py                 # Shared run state
    ├── templates/
    │   ├── base.html                # Main app shell
    │   ├── login.html               # Login page
    │   ├── register.html            # Registration page
    │   ├── admin.html               # Admin panel
    │   └── partials/                # Tab partials
    ├── static/
    │   ├── css/amplify.css          # Design system (8 themes, responsive)
    │   └── js/                      # Per-tab JavaScript
    └── jira_insights/               # Streamlit app (port 8501)
        ├── app.py
        ├── launcher.py
        ├── settings.py
        └── ...
```

---

## API Reference

### Authentication

| Method | Route | Purpose |
|---|---|---|
| POST | `/api/auth/register` | Create new user |
| POST | `/api/auth/login` | Authenticate, issue JWT |
| POST | `/api/auth/logout` | Clear session |
| POST | `/api/auth/refresh` | Refresh JWT token |
| POST | `/api/auth/change-password` | Change your own password (`current_password`, `new_password`) |
| GET | `/api/auth/me` | Current user info (including `must_change_password`) |
| POST | `/api/admin/bootstrap` | One-time admin promotion |

### Admin (requires admin role)

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/admin/users` | List all users + disk usage |
| GET | `/api/admin/users/<id>` | User detail + activity |
| POST | `/api/admin/users/<id>/reset` | Set a temporary password (the user must change it) |
| POST | `/api/admin/users/<id>/toggle` | Enable/disable account |
| POST | `/api/admin/users/<id>/promote` | Grant/revoke admin |
| DELETE | `/api/admin/users/<id>` | Delete user + workspace |
| GET | `/api/admin/metrics` | Usage stats |
| GET | `/api/admin/activity` | Activity log |
| GET/POST | `/api/admin/server-config` | Server secrets |

### Repository

| Method | Route | Purpose |
|---|---|---|
| POST | `/api/repo/clone` | Clone or pull a git repo |
| POST | `/api/repo/pull` | Pull latest |
| GET | `/api/repo/branches` | List branches |
| POST | `/api/repo/checkout` | Switch branch |
| GET | `/api/repo/status` | Repo + venv status |
| POST | `/api/repo/venv/create` | Create .venv |
| POST | `/api/repo/venv/install` | pip install (SSE stream) |
| GET/POST/DELETE | `/api/repo/configs` | Saved repo presets |

### Executor

| Method | Route | Purpose |
|---|---|---|
| POST | `/api/run` | Start pytest run (SSE stream) |
| POST | `/api/stop` | Cancel running process |
| GET | `/api/stream` | SSE event stream |
| GET | `/api/tests` | Discover suites + test files |
| GET | `/api/tests/names` | List test functions |
| POST | `/api/features/run` | Run a feature script |
| GET | `/api/venv/status` | Venv detection |
| POST | `/api/venv/install` | pip install (SSE stream) |

### Grid

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/grid/status` · `/config` · `/pods` | Coordinator reachable? · environments and browsers · runner pods |
| GET | `/api/grid/runs` · `/api/grid/runs/<id>` · `/api/grid/chunks/<id>` | Runs, one run's chunks, a chunk's pytest output |
| POST | `/api/grid/runs` | Start a run `{tests, environment, browsers, chunk_size[, repo_path]}`. With `repo_path` (the Executor's repo, its top-level folder) the answer also has `repo`, `sha`, `dirty` and `code_source`, and `warning` when the coordinator did not confirm them. A body or field of the wrong type is a JSON `400` (`tests`: list of text, `chunk_size`: whole number 1–10) |

**How a Grid run gets its code.** With `repo_path`, Amplify finds the repo's git top-level (it must be a git work tree, and not inside Amplify's data directory — except your own clones in your workspace), takes `HEAD`'s `sha`, builds `git archive --format=tar.gz <sha>` (at most 100 MiB and 100,000 entries — the runners' limits; committed symlinks are refused) and delivers it: unpacked into `GRID_CODE_DIR/<repo>/<sha>/` when that shared volume is set (`code_source: "volume"`), otherwise sent to the coordinator (`"coordinator"`, or `"cached"` when it already had that commit). The run carries `(repo, sha)`; each pod runs its tests from that immutable snapshot. Uncommitted and untracked files are never included (`dirty: true` warns). Without `repo_path` the pods run the tests baked into their image, as before.

- **Select the repo's top-level folder.** The pods unpack the whole repo and run from its top-level, so a sub-folder is refused with a `400` that names the top-level folder to select (the Grid tab shows the message).
- **The coordinator must be current.** Before packaging, Amplify asks the coordinator's `GET /api/config` for `features` and needs `"code-delivery"` in it, else `502` *"The Grid coordinator is too old for code delivery — update it"*. After `POST /api/runs` the coordinator must echo the same `repo` and `sha`; if not, the answer carries a `warning` and the tab shows it (the pods may then run their image's tests).

**Shared volume (`GRID_CODE_DIR`).** Back it with a **ReadWriteMany** volume (an RWX PVC) mounted read-write in Amplify at `GRID_CODE_DIR` and **read-only** in every runner at its `GRID_CODE_ROOT` (the same folder). Amplify's uid needs write access to it (set the pod's `fsGroup`/`runAsUser` accordingly); the runners may run as any uid — Amplify creates `<repo>/` and each snapshot folder `0755` (files `0644`, or `0755` if executable) regardless of its umask, so they can read everything. A snapshot is up to 1 GiB unpacked and one is kept per distinct commit, so size the volume for the commits you expect inside the retention window. **Retention:** each run that uses a snapshot touches its folder, and after every delivery Amplify removes snapshot folders nobody has used for `GRID_CODE_RETENTION_DAYS` (default 30; `0` keeps them for ever) and half-extracted `.<sha>-*.tmp` folders older than a day. The pruning is bounded (at most 10 folders and 5 seconds per delivery, so a backlog clears over several runs), never touches the snapshot just used, never follows a symlink, and never leaves `GRID_CODE_DIR`.

### Dashboard

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/dashboard` | Results, summaries, history (single repo) |
| GET | `/api/dashboard/overview` | Grid + local runs consolidated: KPIs, charts, pods, runs. Filters: `source`, `env`, `browser`, `pod`, `days` or `from`/`to`, `tz` |
| GET | `/api/dashboard/run/<source>/<id>` | One run's chunks, pods and per-test results (`source` = `grid` or `local`) |
| POST | `/api/dashboard/report-link` | Signed link to view a run's HTML report in the dashboard: `{source: "local" (default) or "grid", run_id, uid}` — a local run's (or test's) report, or a Grid run's Allure report |
| GET | `/rv/<token>/<file>` | Serves that report (signed, per-user, expires after an hour) |
| GET | `/api/report` | Report paths |
| GET | `/api/report/history` | History records |
| POST | `/api/report/history/clear` | Clear history |

**How the consolidated dashboard works.** Grid runs come from the coordinator configured in Admin Panel → Grid Coordinator (the same data as the Grid tab); local runs come from your Executor history. Filters apply to *chunks*, so choosing a browser or a pod counts only what that browser or pod executed, and a run stays in the view while any of its chunks matches. Finished Grid runs are cached; runs in flight refresh every few seconds. If the coordinator is unreachable the dashboard still shows your local runs and says why Grid data is missing. Logs are what each pod's pytest printed. HTML reports exist for local runs (Allure or single-file) and for Grid runs once any chunk has uploaded its Allure results: the coordinator builds one single-file Allure 3 report per run on first request (the first click can take a while — the button says *Generating report…*); it has one Allure *environment* per browser, so an **Environment** dropdown filters it and each test's **Environments** tab compares it across browsers. Amplify keeps what it downloaded in `<data dir>/grid_reports/<run>/<etag>/report.html`, asks again with the cached ETag so an unchanged report is not downloaded twice, and deletes entries unused for 7 days. Reports open in an in-page viewer through short-lived signed links — the browser never sends a file path, and a Grid link reaches only that one report file.

### Zephyr / Jira

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/zephyr/config` | View config status (server-managed) |
| GET | `/api/zephyr/statuses` | Dynamic status list |
| GET | `/api/zephyr/projects` | List Jira projects |
| GET | `/api/zephyr/versions` | List versions |
| GET | `/api/zephyr/cycles` | List cycles |
| POST | `/api/zephyr/cycle` | Create cycle |
| GET | `/api/zephyr/folders` | List folders |
| POST | `/api/zephyr/folder` | Create folder |
| POST | `/api/zephyr/executions/add` | Add tests to cycle |
| GET | `/api/zephyr/executions` | List executions |
| POST | `/api/zephyr/bulk-results` | Bulk status update |

### File Manager (admins only — 403 for everyone else)

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/fm/browse` | List directory |
| GET | `/api/fm/read` | Read file |
| POST | `/api/fm/save` | Write file |

### Jira Insights

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/insights/status` | Streamlit process status |
| POST | `/api/insights/start` | Start Streamlit |
| POST | `/api/insights/stop` | Stop Streamlit |
| GET | `/api/insights/open` | Sign the current user in to Insights and redirect there (embedded tab and **↗ New tab**) |
| GET | `/api/insights/options` | Which Insights options/views the admin enabled (read-only, for a separately deployed Insights) |

**Key links, carry-forward and card colours.**
- **Key links.** In a Table report (builder preview, dashboard, full screen) and in the Data view, the **Key** column is a link to the issue: the dataset's Jira URL, a `/` and the key (`/browse` is added unless the URL already ends with it). It is an ordinary link in your own browser: it opens Jira in a new tab on your machine and never goes back to the Insights server. Nothing is linked when the dataset has no Jira URL (an uploaded export), when it isn't a plain `http(s)` address, or when a "Key" column holds anything but issue keys. CSV and Excel keep the plain keys.
- **Carry-forward by sprint.** An issue lists every sprint it has been in, oldest first; it was carried forward out of each of them except the last. Insights derives a multi-value column **Carried Forward From** from that (also for datasets pulled earlier, without a re-pull), so counting by it puts an issue under every sprint it left. **Sprint Reports** gets two reports from it, *Stories carried forward, by sprint* and *Defects carried forward, by sprint* (bugs and defects), added once to each workspace; in the Report Builder the column works like any other, e.g. with *Issue Type* as the series. A dataset on a custom board picks them up through **🧩 Reports**. An issue's last sprint is never counted (nothing follows it yet), and sprints are taken in the order Jira lists them.
- **Card colours.** Under **✎ Edit tiles** each KPI tile has a **Colour** and a **Card style**: *Plain* (the neutral card, the colour only tints the icon; every existing tile), *Full colour* or *Gradient* (the card is filled with the colour). On a filled card the figure, label and icon take white or near-black, whichever reads better on that fill, so the number is never the colour of its box.

**How sign-in to Insights works.** Insights never trusts its URL for identity. Opening it — embedded or in a new tab — goes through `/api/insights/open`: QEA checks your session and redirects to Insights with a signed token (`?sso=…`) that is valid for 60 seconds, usable once, and removed from the address bar on arrival. Two things follow: QEA and Insights must share `JWT_SECRET_KEY` (see below), and **refreshing a New tab returns to Insights' own sign-in form**, because the link it opened with was single-use — reopen it from QEA, or sign in there.

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `JWT_SECRET_KEY` | **Yes** (in containers) | generated | Signing key for sessions and for the Jira Insights sign-in link. Set it explicitly in any real deployment. If unset, a random key is generated once and stored in the data directory, so QEA and Insights on the same host or volume agree automatically. **Insights deployed separately must be given the same value as QEA**, otherwise sign-in from QEA is refused |
| `INSIGHTS_PUBLIC_URL` | When Insights has its own address | `http(s)://<host>:8501` | Browser-facing URL of Jira Insights (e.g. `https://insights.example.com`) — where the Insights tab and **↗ New tab** send the user |
| `CONTAINER` | Yes | `1` | Set by Dockerfile |
| `PORT` | No | `8080` | Flask listen port |
| `ALLURE3_BIN` | No | `/usr/local/bin` (image) | Where the Allure 3 CLI is: the folder `npm install -g allure` installs into (what `echo $(npm config get prefix)/bin` prints) or the executable. The image ships Node, npm and the CLI and sets this. It is the default for each user's **Allure 3 binary path** (`allure3_bin`, Config → Allure) when that is empty; a path saved there wins. Where it is set, Allure 3 is also the default report format for new users (Allure 2 needs Java, which the image does not include). |
| `JIRA_URL` | No | — | Jira instance URL |
| `JIRA_USERNAME` | No | — | Atlassian email |
| `JIRA_API_TOKEN` | No | — | Atlassian API token |
| `ZEPHYR_ACCESS_KEY` | No | — | Zephyr access key |
| `ZEPHYR_SECRET_KEY` | No | — | Zephyr secret key |
| `ZEPHYR_ACCOUNT_ID` | No | — | Atlassian account ID |
| `ADMIN_BOOTSTRAP_TOKEN` | No | — | One-time admin promotion token |
| `INSIGHTS_PORT` | No | `8501` | Streamlit port |
| `QEA_API_URL` | No | — | Set on a separately deployed Insights service (own disk, e.g. Render) to the QEA base URL. Insights then signs users in through QEA and reads the admin's Insights option settings from it. Not needed when both share a data volume |
| `GRID_COORDINATOR_URL` | No | — | Default for the distributed grid coordinator, e.g. `http://central-coordinator-svc:8000`; enables the Grid tab. A URL saved in Admin Panel → Grid Coordinator overrides it |
| `GRID_API_TOKEN` | No | — | Default shared secret sent to the coordinator as `X-API-Token`; a token saved in the Admin Panel overrides it |
| `GRID_CODE_DIR` | No | — | The shared tests-store volume, mounted read-write in Amplify and read-only on the Grid runners (their `GRID_CODE_ROOT`). Snapshots of a repo's committed state are unpacked into `<dir>/<repo>/<sha>/`. If unset, Amplify uploads snapshots to the coordinator instead |
| `GRID_CODE_RETENTION_DAYS` | No | `30` | Snapshots in `GRID_CODE_DIR` that no run has used for this many days are deleted (opportunistically, after each delivery). `0` keeps them for ever. See *Shared volume* under Grid |

---

## Security

- **Passwords**: bcrypt with 12 rounds; 6–72 bytes. Changing one needs the current password. Existing sign-in sessions are *not* ended by a change (tokens are stateless and last 24 hours) — except that a reset immediately restricts them to changing the password
- **Tokens**: JWT (HS256, 24-hour expiration, httpOnly cookies)
- **User isolation**: filesystem-level workspace separation
- **Credentials**: server-level only, never in user config, masked in API responses
- **Disabled accounts**: rejected at auth middleware level
- **Admin-only**: credential management, user management, activity log
- **Activity logging**: all logins, registrations, and admin actions recorded with IP

---

## Deployment Guides

- [OpenShift with JFrog Artifactory](opns/README.md)
- [Kubernetes](k8s/README.md)
- [Docker guide](docs/DOCKER.md)

---

## Licence

MIT
