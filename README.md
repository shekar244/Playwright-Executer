# Amplyfy QEA

A browser-based local UI for running, reporting, and managing **Playwright + pytest** test suites — no VS Code, no terminal, no command memorisation required.

```
GitHub/
├── Your-QA-Framework/    ← your existing framework (unchanged)
└── Amplyfy-QEA/  ← Amplyfy QEA (UI layer only)
```

Amplyfy QEA **never modifies the framework**. All test logic, fixtures, page objects, and configuration remain exactly as they are.

---

## Features

| Tab | What it does |
|---|---|
| **Executor** | Run pytest with full control: browser, suite, file, test filter (-k), marker (-m), workers (-n), headed/headless, extra args; pinned repo quick-switching |
| **Config** | Manage features/scripts, git commands, custom CLI flags, field mappings, Zephyr credentials, tab visibility |
| **Dashboard** | Per-test results, run history table, 4 charts, suite + per-test report links; history scoped per-repo |
| **Test Management** | Full Zephyr for Jira Cloud — import tests, bulk upload results, view/mark executions, hierarchical metrics with dynamic statuses |
| **File Manager** | Browse and edit `.json`, `.yaml`, `.env`, `.csv`, `.xlsx` files with dedicated editors per format |

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
3. Adds `127.0.0.1 amplyfy-qea` to `/etc/hosts`
4. Opens `http://amplyfy-qea:7777` in the default browser

---

## Requirements

| Dependency | Version | Notes |
|---|---|---|
| Python | 3.10+ | 3.13 recommended |
| Flask | 3.0+ | only required pip dependency |
| tkinter | stdlib | used by native folder picker |
| openpyxl | optional | required for `.xlsx` editing in File Manager |

All other modules (`hashlib`, `hmac`, `base64`, `csv`, `urllib`, etc.) are Python standard library.

```bash
pip install -r requirements.txt
# or:
pip install flask openpyxl
```

Optional — needed only in the test framework repo itself:

```
pytest
pytest-playwright
allure-pytest
pytest-xdist
```

---

## Project Layout

```
Amplyfy-QEA/
├── server.py                    # Flask app factory — registers blueprints
├── routes/
│   ├── __init__.py
│   ├── state.py                 # Shared run state + SSE broadcast
│   ├── executor.py              # run / stop / stream / repos / tests / features / git / venv
│   ├── config.py                # /api/config/* endpoints
│   ├── dashboard.py             # /api/dashboard + /api/report
│   ├── history.py               # Allure result parsing + per-repo run history
│   ├── zephyr.py                # All /api/zephyr/* and /api/jira/* endpoints
│   └── filemanager.py           # /api/fm/browse, read, save
├── templates/
│   ├── base.html                # HTML shell: head, CSS, nav, shared JS, includes
│   └── partials/
│       ├── executor.html        # Executor + Config page HTML
│       ├── dashboard.html       # Reports Dashboard page HTML
│       ├── testmanagement.html  # Test Management (Zephyr) page HTML
│       └── filemanager.html     # File Manager page HTML + JS
├── static/
│   └── js/
│       ├── executor.js          # Executor + Config JS
│       ├── dashboard.js         # Dashboard charts + history table JS
│       └── testmanagement.js    # Zephyr / Test Management JS
├── ui_launcher/
│   ├── command_builder.py       # Builds the pytest command; venv Python resolution
│   ├── config_reader.py         # Config resolution chain (tool → override → repo)
│   ├── report_resolver.py       # Finds the latest Allure HTML report
│   ├── runner.py                # Spawns subprocess + SSE stream
│   └── test_discovery.py        # Discovers suites and test files
├── config.json                  # Tool-level bootstrap config (repo_root, overrides)
├── docs/
│   └── EXTENDING.md
├── requirements.txt
├── launch-ui.sh
└── launch-ui.bat
```

---

## Executor Tab

### Repository

- Click **📂 Browse for Repo…** to pick a folder using the native OS picker (Finder on macOS, Explorer on Windows/Linux)
- The **active repo banner** shows the repo name and full path once selected
- Click **📌** to pin the current repo to the **Pinned Repos** quick-access list
- Click any pinned repo card to switch instantly; click **✕** on a card to unpin it
- Pinned repos are stored in `config.json` as `pinned_repos`

### Test Selection

- **Suite / Folder** — discovered from `tests/` subdirectories
- **Test File** — all `.py` files in the selected suite
- **Test Name Filter** — tick individual test functions (auto-discovered) or toggle to manual `-k` expression mode

### Execution Options

| Control | Purpose |
|---|---|
| Browser | chromium / firefox / webkit |
| Marker (-m) | Filter by pytest marker |
| Workers (-n) | Parallel execution via pytest-xdist |
| Headed | Run browser in headed mode |
| Verbose (-v) | Add pytest verbosity |
| Auto-open Report | Open the Allure report automatically after each run |
| Extra Args | Free-text additional pytest flags |

### Custom Options (🎛️)

User-defined CLI controls (checkbox, dropdown, text input) defined in **Config → CLI Tools**. They appear as an extra panel on the Executor tab and their values are appended to the pytest command.

### Config File (collapsed)

- Shows active config path with full word-wrap and a source badge (`tool` / `repo` / `override`)
- **📂 Load Different Config** — browse for any `.json` file to use as a config override
- **✕ Clear Override** — return to normal config resolution
- **⟳ Refresh** — re-fetches config and re-applies all tool options without a page reload

### Python Venv (collapsed)

- Enter a venv path (relative to repo or absolute), or leave blank for auto-detect
- Auto-detect order: `.venv`, `venv`, `env`, `.env` inside the repo root
- Status badge: **venv active** (green) or **system python** (yellow)
- Detects `requirements.txt` and offers a **pip install** button that streams output to the log panel
- The resolved Python is used for both pytest runs and feature script runs

### Reports Panel

- **Individual Run** — path to the latest Allure HTML report for the current run
- **Consolidated** — path to the consolidated/history report
- Click **Open Latest** to open in the default browser

---

## Reports Dashboard Tab

### Current Run

- **Results** card — Total / Passed / Failed counts
- **Performance** card — Broken / Skipped / Avg Duration / Pass Rate
- **Distribution** donut — hover a segment to see percentage; centre shows total count
- **Count by Status** horizontal bar chart
- **View Report** button — opens the suite-level Allure report (`record.report_path`)

### Test Cases Table

- Live search by name/suite
- Status filter (All / Passed / Failed / Broken / Skipped)
- Sortable columns: File, Test Method, Duration
- Per-test **📊 View** button — opens the report for that run (`test.report_path`); falls back to suite report
- Click a historical run row to load its tests into this table; a banner shows which run is active with a **← Back to Live Run** button

### Run History Table

Columns: Date · Repo · Status · Passed · Failed · Broken · Skipped · Total · Pass% · Report

- Paginated (10 per page) with smart pagination controls
- Click any row to inspect that run's individual test results
- **Clear History** — clears history for the currently active repo only

### History Storage

`report_history.json` is stored **per-repo** at `{repo_root}/report_history.json`. It is created and maintained entirely by Amplyfy QEA — no pytest fixtures or plugins required. Each run prepends a new record; the file is capped at 200 records.

---

## Test Management Tab (Zephyr for Jira Cloud)

### Authentication

Credentials are stored in `config.json` under the `zephyr` key and managed via **Config → 🔷 Zephyr Config**.

| Field | Where to find it |
|---|---|
| **Jira Base URL** | `https://yourcompany.atlassian.net` |
| **Jira Username** | Your Atlassian email |
| **Jira API Token** | `id.atlassian.com` → Security → API tokens |
| **Zephyr Access Key** | Jira → Zephyr app → API Keys |
| **Zephyr Secret Key** | Jira → Zephyr app → API Keys |
| **Atlassian Account ID** | Jira profile URL → `accountId` |

JWT tokens are generated per-request using HS256 — no external JWT library required.

### Workflow

1. **Configure** credentials in Config → Zephyr Config, click **⚡ Test Connection**
2. Select **Project → Version → Cycle → Folder** in the left panel
3. Use one of the four sub-tabs:

### Import Tests tab

- **By Issue Keys** — paste Jira keys (one per line) or upload a CSV with an `Issue Key` column
- **Create from CSV** — upload a test case CSV; rows grouped by `Story ID` column create sub-folders automatically, test cases are linked to the story
- **From Jira Filter** — enter a Saved Filter ID or JQL to fetch and preview matching issues, then add selected ones to the cycle

### Upload Results tab

- **Bulk CSV Upload** — upload a results CSV; columns mapped via Config → Field Mapping
- **Individual Test** — update a single test by issue key with status, comment, and optional attachment
- **Bulk status override** — override all rows in the CSV to a single status

Results CSV format:
```csv
Issue Key,Status,Comment,Attachment Path
PROJ-1,pass,Verified on chromium,
PROJ-2,fail,Assertion error on step 3,/path/to/screenshot.png
PROJ-3,wip,,
```

### Executions tab

- Load all executions for the selected cycle/folder
- Filter by status using a dynamic dropdown (all Zephyr statuses)
- Paginated table with select-all / individual checkboxes
- **Mark Selected** — bulk-update status with optional comment and step-update toggle
- **Quick Mark** — mark a single execution inline

### 📊 Metrics tab

- Fetches all folders for the cycle depth-first, accumulates execution counts per status
- Hierarchical table: parent rows show aggregated counts including sub-folders
- Stacked horizontal bar chart per folder
- **Dynamic Zephyr statuses**: on tab open, statuses are fetched from `/api/zephyr/statuses`; all table columns, dropdowns, and chart datasets rebuild automatically
- Hardcoded fallback if Zephyr is not configured: Pass · Fail · WIP · Blocked · Descoped · To-Do · Unexecuted

---

## File Manager Tab

Browse the file system and edit files in place.

| Format | Editor |
|---|---|
| `.json` | Collapsible interactive tree + raw edit mode with JSON validation badge |
| `.yaml` / `.yml` | Raw text editor |
| `.env` | Key=Value table editor + raw mode; add/delete rows |
| `.csv` | Editable spreadsheet table; + Row, + Col, − Row buttons |
| `.xlsx` | Multi-sheet editable table; + Row, + Col, − Row, − Col; click a row number to select the row, click a column letter to select the column; delete buttons activate on selection |

- **Cmd/Ctrl+S** saves the current file
- All changes marked with an `● unsaved` badge; `✓ saved` confirmation on save
- Status bar shows file info (row/col counts, filled cells, etc.)

---

## Config File Resolution

Amplyfy QEA uses a three-level config resolution chain:

1. **Tool bootstrap** — `{tool_dir}/config.json` — always read first; stores `repo_root` and `config_override_path`
2. **Override** — if `config_override_path` is set and the file exists, it overrides everything
3. **Repo config** — if `{repo_root}/config.json` exists, it is merged on top of the tool config

The active source is shown in the **Config File** panel as a colour-coded badge:

| Badge | Meaning |
|---|---|
| `tool` (blue) | Using `{tool_dir}/config.json` |
| `repo` (green) | Using `{repo_root}/config.json` |
| `override` (yellow) | Using a custom override file |

When you save `repo_root`, it is written to both the active config and the tool bootstrap so the chain survives restarts.

---

## Python Venv

The tool resolves a Python executable for every run using this priority order:

1. **Configured `venv_path`** — value from config, relative to repo root or absolute
2. **Auto-detect** — checks `.venv`, `venv`, `env`, `.env` inside the repo root
3. **System Python** — the Python running the Amplyfy QEA server itself

This resolved Python is used for:
- **pytest** test runs
- **Feature scripts** (Python runtime)
- **pip install** from the Venv panel

---

## Configuration (`config.json`)

Full example with all supported keys:

```json
{
  "repo_root": "/path/to/your-qa-repo",
  "pinned_repos": [
    "/path/to/repo-a",
    "/path/to/repo-b"
  ],
  "config_override_path": "",
  "venv_path": ".venv",
  "browsers": ["chromium", "firefox", "webkit"],
  "default_browser": "chromium",
  "default_workers": 1,
  "auto_open_report": false,
  "allure_results_dir": "allure/results",
  "report_individual_dir": "allure/reports",
  "report_consolidated_dir": "allure/reports/history",
  "extra_options": [],
  "features": [],
  "git_commands": [],
  "markers": [],
  "ui_tabs": {
    "dashboard": true,
    "zephyr": true,
    "cfg_git": true,
    "cfg_tools": true,
    "cfg_mapping": true,
    "cfg_zephyr": true
  },
  "zephyr": {
    "jira_url": "https://yourcompany.atlassian.net",
    "username": "you@company.com",
    "api_token": "",
    "access_key": "",
    "secret_key": "",
    "account_id": "",
    "project_key": "PROJ",
    "project_name": "My QA Project",
    "verify_ssl": false
  },
  "zephyr_tc_mapping": {
    "story_id": "Story ID",
    "summary": "Test Name",
    "description": "Description",
    "steps_format": "columns"
  },
  "zephyr_results_mapping": {
    "issue_key": "Issue Key",
    "status": "Status",
    "comment": "Comment",
    "attachment_path": "Attachment Path"
  }
}
```

---

## API Reference

### Config

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/config` | Full merged config |
| GET | `/api/config/info` | Active config path + source badge |
| POST | `/api/config/repo-root` | Save repo_root |
| POST | `/api/config/pinned-repos` | Save pinned repos list |
| POST | `/api/config/override` | Set / clear config_override_path |
| POST | `/api/config/venv-path` | Save venv_path |
| POST | `/api/config/features` | Save features list |
| POST | `/api/config/tools` | Save extra_options (CLI tools) |
| POST | `/api/config/ui-tabs` | Save tab visibility settings |

### Executor

| Method | Route | Purpose |
|---|---|---|
| POST | `/api/run` | Start a pytest run (streams via SSE) |
| POST | `/api/stop` | Cancel the running process |
| GET | `/api/stream` | SSE event stream (cmd / line / status / done) |
| GET | `/api/repos` | Auto-discover Playwright repos |
| GET | `/api/tests?repo=` | Discover suites + test files |
| GET | `/api/tests/names?repo=&file=` | List test function names in a file |
| POST | `/api/features/run` | Run a feature script |
| POST | `/api/git/run` | Run a configured git command |
| POST | `/api/browse-folder` | Open native OS folder picker |
| GET | `/api/venv/status?repo=` | Venv detection + requirements files |
| POST | `/api/venv/install` | pip install -r requirements.txt (streams) |

### Dashboard

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/dashboard?repo=` | Tests, summaries, trend, run history, report_path |
| GET | `/api/report?repo=` | Individual + consolidated report paths |
| POST | `/api/report/open` | Open a report file in the browser |
| GET | `/api/report/history?repo=` | Load history records for a repo |
| POST | `/api/report/history/clear` | Clear history for a repo |

### Zephyr

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/zephyr/statuses` | Dynamic status list (Zephyr API or fallback) |
| GET/POST | `/api/zephyr/config` | Load / save credentials |
| GET | `/api/zephyr/projects` | List Jira projects |
| GET | `/api/zephyr/versions?projectKey=` | List versions |
| GET | `/api/zephyr/cycles?projectId=&versionId=` | List cycles |
| POST | `/api/zephyr/cycle` | Create a cycle |
| GET | `/api/zephyr/folders?projectId=&versionId=&cycleId=` | List folders |
| POST | `/api/zephyr/folder` | Create a folder |
| POST | `/api/zephyr/executions/add` | Add tests to cycle/folder |
| GET | `/api/zephyr/executions?cycleId=` | List executions |
| POST | `/api/zephyr/bulk-results` | Parse CSV + bulk status update + attach |

### File Manager

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/fm/browse?path=` | List directory entries |
| GET | `/api/fm/read?path=` | Read a file (json/yaml/env/csv/xlsx) |
| POST | `/api/fm/save` | Write a file |

---

## Licence

MIT
