# Playwright Test Executor

A browser-based local UI for running, reporting, and managing **Playwright + pytest** test suites — no VS Code, no terminal, no command memorisation required.

```
GitHub/
├── Your-QA-Framework/       ← your existing framework (unchanged)
└── Playwright-Executer/     ← this project (UI layer only)
```

The executor **never modifies the framework**. All test logic, fixtures, page objects, and configuration remain exactly as they are.

---

## Features

| Tab | What it does |
|---|---|
| **Executor** | Run pytest with full control: browser, suite, file, test filter (-k), marker (-m), workers (-n), headed/headless, extra args |
| **Features** | Run any Python / Node.js / shell script directly from the UI |
| **Tools** | Manage custom CLI flag controls shown in the Executor tab |
| **Dashboard** | Per-test results table, Last-N-Days stacked bar chart, run-history trend from Allure + smart-reporter data |
| **Zephyr** | Full Zephyr for Jira Cloud integration — browse projects/versions/cycles/folders, import test cases, bulk upload results, attach reports |

---

## Quick Start

### macOS / Linux

```bash
bash launch-ui.sh
```

### Windows

```bat
launch-ui.bat    # run as Administrator on first launch
```

The launcher:
1. Detects or creates a virtualenv
2. Installs Flask if missing
3. Adds `127.0.0.1 playwright-executor` to `/etc/hosts`
4. Opens `http://playwright-executor:7777` in the default browser

---

## Requirements

| Dependency | Version | Notes |
|---|---|---|
| Python | 3.10+ | 3.13 recommended |
| Flask | 3.0+ | only pip dependency |
| tkinter | stdlib | used by native folder picker |

All other modules (`hashlib`, `hmac`, `base64`, `csv`, `urllib`, etc.) are Python standard library — nothing extra to install.

Install the single pip dependency:

```bash
pip install -r requirements.txt
# or just:
pip install flask
```

Optional — needed only if you run tests locally (already in your framework):

```
pytest
pytest-playwright
allure-pytest
pytest-xdist
```

---

## Project Layout

```
Playwright-Executer/
├── server.py               # Flask app — all API routes
├── static/
│   └── index.html          # Single-file SPA (HTML + CSS + JS)
├── ui_launcher/
│   ├── command_builder.py  # Builds the pytest command
│   ├── config_reader.py    # Loads / saves config.json
│   ├── report_resolver.py  # Finds the latest Allure HTML report
│   ├── runner.py           # Spawns pytest subprocess + SSE stream
│   └── test_discovery.py   # Walks tests/ and discovers suites/files
├── config.json             # User settings (persisted)
├── report_history.json     # Dashboard run history (auto-generated)
├── requirements.txt
├── launch-ui.sh
└── launch-ui.bat
```

---

## Executor Tab

- **Select Local Repo** — auto-scans `~/Documents/GitHub`, `~/GitHub`, `~/Projects`; or click **Browse…** to open the native Finder (macOS) / Explorer (Windows) folder picker
- **Suite / Folder** — discovered from `tests/` subdirectories
- **Test File** — all `.py` files in the selected suite
- **Test Name Filter** — tick individual test functions or type a `-k` expression
- **Browser** — chromium / firefox / webkit
- **Workers (-n)** — parallel execution via pytest-xdist
- **Custom Options** — any extra CLI flags defined in the Tools tab

After each run the **Reports** panel auto-refreshes and shows the latest Allure HTML file.

---

## Dashboard Tab

Parses results from two sources (whichever is present):

| Source | Location | Contents |
|---|---|---|
| Allure results | `allure/results/*-result.json` | Per-test name, suite, status, timestamps |
| Smart Reporter | `allure/reports/.smart-reporter-data.json` | Current run results |
| Smart Reporter history | `allure/reports/smart-reporter-history.json` | Up to 20 historical run summaries |
| Allure trend | `allure/reports/*/history-trend.json` | Build-over-build pass/fail counts |

**Charts:**
- **Tests Run by Day** — stacked bar auto-scaled to cover all available data (7–60 days)
- **Status Distribution** — donut of current run
- **Run History Trend** — line chart of last 20 runs

**Test Case Table** — live search, status filter, sortable columns, per-test history dots (coloured circles = last 10 runs).

---

## Zephyr Tab (Zephyr for Jira Cloud)

### Authentication

Credentials stored in `config.json` under the `zephyr` key:

| Field | Where to find it |
|---|---|
| **Jira Base URL** | `https://yourcompany.atlassian.net` |
| **Jira Username** | Your Atlassian email |
| **Jira API Token** | `id.atlassian.com` → Security → API tokens |
| **Zephyr Access Key** | Jira → Zephyr app → API Keys |
| **Zephyr Secret Key** | Jira → Zephyr app → API Keys |
| **Atlassian Account ID** | Profile → `accountId` in URL |

JWT tokens are generated per-request using HS256 + the Access/Secret key pair (no external JWT library required).

### Workflow

1. **Configure** — fill in credentials, click **Test** (green dot = connected)
2. **Project** → **Version/Release** → **Test Cycle** (create new inline) → **Folder** (create new inline)
3. **Import Tests tab** — paste Jira issue keys or upload a CSV with an `Issue Key` column → **Add Tests to Cycle**
4. **Upload Results tab** — upload a results CSV (`Issue Key`, `Status`, `Comment`) + optional HTML/PDF attachment → **Upload Results**
5. **Executions tab** — view all executions in the selected cycle/folder, tick rows, bulk-mark pass/fail/wip/blocked, attach a file to each

### Results CSV format

```csv
Issue Key,Status,Comment
PROJ-1,pass,Verified on chromium
PROJ-2,fail,Assertion error on step 3
PROJ-3,skip,
```

Accepted status values: `pass`, `fail`, `wip`, `blocked`, `unexecuted` (case-insensitive).

### API endpoints added

| Method | Route | Purpose |
|---|---|---|
| GET/POST | `/api/zephyr/config` | Load / save credentials |
| GET | `/api/zephyr/projects` | List Jira projects |
| GET | `/api/zephyr/versions?projectKey=` | List project versions |
| GET | `/api/zephyr/cycles?projectId=&versionId=` | List test cycles |
| POST | `/api/zephyr/cycle` | Create a test cycle |
| GET | `/api/zephyr/folders?projectId=&versionId=&cycleId=` | List folders |
| POST | `/api/zephyr/folder` | Create a folder |
| POST | `/api/zephyr/executions/add` | Add tests to cycle/folder |
| GET | `/api/zephyr/executions?cycleId=` | List executions |
| PUT | `/api/zephyr/execution/<id>` | Update single execution |
| POST | `/api/zephyr/executions/bulk` | Bulk status update |
| GET | `/api/zephyr/stepresults?executionId=` | Get step results |
| PUT | `/api/zephyr/stepresult/<id>` | Update step result |
| POST | `/api/zephyr/attach` | Upload attachment to execution |
| POST | `/api/zephyr/bulk-results` | Parse CSV + bulk update + attach |

---

## Native Folder Picker

Clicking **Browse…** on the Executor tab calls `/api/browse-folder`, which opens:
- **macOS** — native Finder folder chooser via `osascript`
- **Windows / Linux** — `tkinter.filedialog.askdirectory()` (part of Python stdlib)

The selected path is immediately applied and tests are re-discovered.

---

## Configuration (`config.json`)

```json
{
  "repo_root": "/path/to/your-qa-repo",
  "browsers": ["chromium", "firefox", "webkit"],
  "default_browser": "chromium",
  "default_workers": 1,
  "auto_open_report": false,
  "allure_results_dir": "allure/results",
  "report_individual_dir": "allure/reports",
  "report_consolidated_dir": "allure/reports/history",
  "extra_options": [],
  "features": [],
  "zephyr": {
    "jira_url": "",
    "username": "",
    "api_token": "",
    "access_key": "",
    "secret_key": "",
    "account_id": ""
  }
}
```

---

## Licence

MIT
