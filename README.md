# Amplify QEA

A multi-tenant, server-deployable platform for running, reporting, and managing **Playwright + pytest** test suites — with user authentication, isolated workspaces, and admin controls.

Deployable on **Docker**, **OpenShift**, **Render.com**, or any container platform. Each user registers, gets their own workspace, and runs tests without interfering with others.

---

## Features

| Tab | What it does |
|---|---|
| **Executor** | Clone repos via Git, create venvs, pip install, run pytest with full control: browser, suite, file, test filter (-k), marker (-m), workers (-n), headed/headless, extra args |
| **Dashboard** | Per-test results, run history table, 4 charts (donut, bar, daily trend, history trend), suite + per-test report links |
| **Test Management** | Full Zephyr for Jira Cloud — import tests, bulk upload results, view/mark executions, hierarchical metrics |
| **File Manager** | Browse and edit `.json`, `.yaml`, `.env`, `.csv`, `.xlsx` files with dedicated editors per format |
| **AI Studio** | AI-powered test assistance |
| **Jira Insights** | Streamlit-based reporting — pull Jira issues with JQL, build pivot reports and charts (runs on port 8501) |
| **Config** | Repository config, features/scripts, git commands, CLI tools, Allure settings, JFrog Artifactory, field mappings, tab visibility |
| **Admin Panel** | User management, password resets, enable/disable accounts, promote/demote admins, usage metrics, activity log, server secrets |

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
pip install -r requirements.txt
python3 -m amplify.models.db    # initialize database
bash amplify.sh                 # or: python3 amplify/server.py
```

### Render.com

1. Use image: `docker.io/shekar244/amplify-qea:latest`
2. Set env var: `JWT_SECRET_KEY` = (generate with `openssl rand -base64 32`)
3. Set env var: `CONTAINER` = `1`
4. Add disk: mount path `/app/data`
5. Deploy

### OpenShift

See [openshift/README.md](openshift/README.md) for full deployment guide with JFrog Artifactory integration.

---

## Authentication & Multi-Tenancy

### User Registration & Login

- Register at `/register` with username, password, optional email
- Login at `/login` — JWT token issued (24-hour expiration, httpOnly cookie)
- First registered user is auto-promoted to admin
- Disabled accounts are blocked from login

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
| **Password Reset** | Reset any user's password |
| **Enable/Disable** | Toggle account active status |
| **Promote/Demote** | Grant or revoke admin role |
| **Delete User** | Remove user and their entire workspace |
| **Metrics** | Total users, active today/week, events, disk usage |
| **Activity Log** | Who did what, when, from which IP |
| **Server Secrets** | Configure Jira/Zephyr credentials (admin only) |

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
├── openshift/                       # OpenShift deployment manifests
│   ├── deployment.yaml
│   ├── service.yaml
│   ├── route.yaml
│   ├── pvc.yaml
│   ├── secrets.yaml
│   └── jfrog-deploy.sh             # Build + push + deploy script
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
| GET | `/api/auth/me` | Current user info |
| POST | `/api/admin/bootstrap` | One-time admin promotion |

### Admin (requires admin role)

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/admin/users` | List all users + disk usage |
| GET | `/api/admin/users/<id>` | User detail + activity |
| POST | `/api/admin/users/<id>/reset` | Reset password |
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

### Dashboard

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/dashboard` | Results, summaries, history |
| GET | `/api/report` | Report paths |
| GET | `/api/report/history` | History records |
| POST | `/api/report/history/clear` | Clear history |

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

### File Manager

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

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `JWT_SECRET_KEY` | **Yes** | — | Token signing key |
| `CONTAINER` | Yes | `1` | Set by Dockerfile |
| `PORT` | No | `8080` | Flask listen port |
| `JIRA_URL` | No | — | Jira instance URL |
| `JIRA_USERNAME` | No | — | Atlassian email |
| `JIRA_API_TOKEN` | No | — | Atlassian API token |
| `ZEPHYR_ACCESS_KEY` | No | — | Zephyr access key |
| `ZEPHYR_SECRET_KEY` | No | — | Zephyr secret key |
| `ZEPHYR_ACCOUNT_ID` | No | — | Atlassian account ID |
| `ADMIN_BOOTSTRAP_TOKEN` | No | — | One-time admin promotion token |
| `INSIGHTS_PORT` | No | `8501` | Streamlit port |

---

## Security

- **Passwords**: bcrypt with 12 rounds
- **Tokens**: JWT (HS256, 24-hour expiration, httpOnly cookies)
- **User isolation**: filesystem-level workspace separation
- **Credentials**: server-level only, never in user config, masked in API responses
- **Disabled accounts**: rejected at auth middleware level
- **Admin-only**: credential management, user management, activity log
- **Activity logging**: all logins, registrations, and admin actions recorded with IP

---

## Deployment Guides

- [OpenShift with JFrog Artifactory](openshift/README.md)
- [Docker guide](amplify/docs/DOCKER.md)

---

## Licence

MIT
