# Playwright Test Executor

A browser-based local UI for running tests against any **Playwright + pytest** framework — no VS Code, no terminal knowledge, no command memorisation required.

```
GitHub/
├── Your-Git-Project-Folder/     ← your existing framework (unchanged)
└── Playwright-Executer/      ← this project (UI layer only)
```

The executor **never modifies the framework**. All test logic, fixtures, page objects, reporting, and configuration remain exactly as they are.

---

## How it works

A lightweight Python (Flask) web server runs locally and serves a browser UI at:

```
http://playwright-executor:7777
```

The UI lets you pick a repo, choose tests, configure options, hit **RUN**, and watch live output stream in real time — then open the generated report with one click.

---

## Prerequisites

| Requirement | Where to get it |
|---|---|
| **Python 3.11+** | [python.org](https://python.org) — must be on your `PATH` |
| **Flask** | Installed automatically by the launch script |
| **Framework dependencies** | `pip install -r requirements.txt` inside **your framework folder** |
| **Playwright browsers** | `playwright install` inside **your framework folder** |

You do **not** need VS Code, Node.js, or any other tool.

---

## Installation

### Step 1 — Place alongside your framework

```
GitHub/
├── Your-Git-Project-Folder/
└── Playwright-Executer/
```

The executor auto-detects sibling directories that look like Playwright repos (any folder containing `tests/` and a pytest config file).

### Step 2 — Verify `config.json` (optional)

`config.json` ships with empty defaults — no editing required on first run. The repo is selected at runtime from the dropdown.

---

## Launching

### macOS / Linux

```bash
bash launch-ui.sh
```

- Installs Flask automatically if missing
- Adds `playwright-executor` to `/etc/hosts` on first run (prompts for sudo password once)
- Starts the server and opens the browser automatically

### Windows

**Double-click `launch-ui.bat`** or run from a terminal:

```cmd
launch-ui.bat
```

> **First run:** right-click `launch-ui.bat` → **Run as administrator** so it can add `playwright-executor` to the Windows hosts file. Subsequent launches do not need admin.

### Direct (any platform)

```bash
cd Playwright-Executer
python server.py
```

Then open **http://playwright-executor:7777** (or **http://localhost:7777** if the hosts entry was not added).

---

## Using the UI

Once the server is running, open your browser to:

```
http://playwright-executor:7777
```

### Repository

| Control | Description |
|---|---|
| **Select Local Repo** | Dropdown auto-populated by scanning `~/Documents/GitHub`, `~/GitHub`, `~/Projects`, and sibling directories for any Playwright/pytest repo |
| **⟳ button** | Re-scan for repos without refreshing the page |
| **Or enter path manually** | Type or paste a full path; use **Browse…** to get a prompt |

Selecting a repo automatically discovers tests, markers, and updates the browser tab title.

### Test Selection

| Field | Description |
|---|---|
| **Suite / Folder** | A subdirectory under `tests/` |
| **Test File** | A specific `test_*.py` file within the suite |
| **Test Name Filter (-k)** | Select a test file to get a checkbox list of individual test functions. Check one or more to run only those; leave all unchecked to run the whole file. Toggle **manual** to type a custom `-k` expression instead. |

### Execution Options

| Option | Description |
|---|---|
| **Browser** | `chromium`, `firefox`, or `webkit` |
| **Marker (-m)** | Run only tests tagged with this marker (discovered from your repo's conftest) |
| **Workers (-n)** | Parallel workers via pytest-xdist (1 = serial) |
| **Headed** | Run browsers visibly — uncheck for headless / CI-style |
| **Verbose (-v)** | Show full test names in the output log |
| **Auto-open Report** | Open the HTML report automatically when the run finishes |

### Extra Args

Raw pytest arguments appended verbatim to the end of the command (e.g. `--timeout=60000 -x`).

### Run / Stop / Output Log

- **▶ RUN** — builds and executes the pytest command; disabled while running
- **■ STOP** — sends a termination signal to the pytest process
- **Output Log** — live stdout/stderr streamed line-by-line, colour-coded:

| Colour | Meaning |
|---|---|
| Teal | PASSED |
| Red | FAILED / ERROR |
| Yellow | WARNING |
| Purple | Command preview |
| Dark grey | Separator lines |

### Report

After a run the executor scans common report output directories (`allure/reports`, `reports`, `test-results`, etc.) for the latest HTML file. Click **Open Latest Report** to open it in your default browser.

### ? Help

Opens this documentation in a new tab.

---

## Generated commands

The executor calls `python -m pytest` using the framework venv's Python (auto-detected) and builds a safe argument list — never a shell string.

Example commands:

```bash
# All tests, chromium, headed
python -m pytest -c config/pytest.ini --override-ini "addopts=" \
    --alluredir=allure/results --browser chromium --headed

# Specific suite, smoke marker, firefox
python -m pytest tests/api -c config/pytest.ini --override-ini "addopts=" \
    --alluredir=allure/results --browser firefox --headed -m smoke

# Single test function, webkit, verbose
python -m pytest tests/api/test_example.py \
    -c config/pytest.ini --override-ini "addopts=" \
    --alluredir=allure/results --browser webkit --headed -k test_login -v
```

`--override-ini "addopts="` clears defaults in `pytest.ini` so the UI selections take full effect without duplicating options.

---

## Project structure

```
Playwright-Executer/
├── server.py              ← Flask server; REST API + SSE stream; auto-opens browser
├── static/
│   └── index.html         ← single-page browser UI (HTML + CSS + JS, no build step)
├── ui_launcher/
│   ├── config_reader.py   ← loads config.json; auto-detects repos generically
│   ├── test_discovery.py  ← scans tests/ for files and markers (read-only)
│   ├── command_builder.py ← converts UI selections → safe pytest arg list
│   ├── runner.py          ← subprocess execution + live output streaming
│   └── report_resolver.py ← finds the latest HTML report in common output dirs
├── config.json            ← user-editable defaults (all optional)
├── launch-ui.sh           ← macOS / Linux launcher
├── launch-ui.bat          ← Windows launcher
└── requirements.txt       ← pip dependencies (flask)
```

---

## `config.json` reference

All keys are optional — the UI falls back to sensible defaults.

```json
{
  "repo_root":        "",
  "default_browser":  "chromium",
  "browsers":         ["chromium", "firefox", "webkit"],
  "markers":          [],
  "report_paths":     [
    "allure/reports/latest/index.html",
    "allure/reports/html/index.html"
  ],
  "default_workers":  1,
  "auto_open_report": false
}
```

- `repo_root` — leave empty; the repo is selected from the dropdown at runtime
- `markers` — leave empty; markers are discovered live from the repo's `conftest.py`
- `report_paths` — relative paths checked first before the broad scan

---

## Existing CLI users — nothing changes

You can continue running tests exactly as before from the terminal:

```bash
cd Your-Git-Project-Folder
pytest tests/ -c config/pytest.ini --browser chromium --headed
```

The executor is an additional entry point, not a replacement.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| **Browser shows "This site can't be reached"** | The server isn't running — start it with `bash launch-ui.sh` or `python server.py` |
| **`playwright-executor` doesn't resolve** | The hosts entry is missing. Run the launch script (it adds it automatically), or add `127.0.0.1  playwright-executor` to your hosts file manually. Use `http://localhost:7777` as a fallback. |
| **Windows: hosts entry not added** | Right-click `launch-ui.bat` → **Run as administrator** on the first launch |
| **No repos appear in the dropdown** | Enter the path manually or use Browse…; the executor scans `~/Documents/GitHub`, `~/GitHub`, `~/Projects`, and sibling directories |
| **"Flask not installed"** | Run `pip install flask` (the launch script does this automatically) |
| **Tests fail with "unrecognised arguments"** | A pytest option may not be supported by the installed plugin versions — check the Output Log for the exact error |
| **No report found after run** | Verify the framework generates a report; check the Output Log for the report path |
| **Wrong Python / venv used** | The executor prefers `<repo>/venv/bin/python` or `<repo>/.venv/bin/python`. Verify it exists and has all dependencies installed |
