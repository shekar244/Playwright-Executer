# Playwright Test Executor

A lightweight local desktop launcher for the **p13n-marketing-experiences-qa-automation** Playwright + pytest framework.

Non-technical users can run tests by double-clicking a script — no VS Code, no terminal, no command knowledge required.

---

## What this is

This is a **standalone UI project**. It sits alongside the automation framework and acts as a thin orchestration layer on top of it:

```
GitHub/
├── p13n-marketing-experiences-qa-automation/   ← unchanged framework
└── Playwright-Executer/                        ← this project (UI only)
```

It does **not** modify the framework. All test logic, fixtures, page objects, reporting, and configuration remain exactly as they are.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Python 3.9 or newer | Must be installed and on your PATH |
| Framework dependencies installed | Run `pip install -r requirements.txt` inside the **framework** folder |
| Playwright browsers installed | Run `playwright install` inside the **framework** folder |
| Tkinter | Included with Python on Windows and macOS. Linux users may need to install it separately (see below) |

You do **not** need VS Code, Node.js, or any other tool.

### Linux — install Tkinter if missing

```bash
# Ubuntu / Debian
sudo apt-get install python3-tk

# Fedora / RHEL / Rocky
sudo dnf install python3-tkinter

# Arch
sudo pacman -S tk
```

---

## Installation

### Step 1 — Clone or copy this project

Place the `Playwright-Executer` folder alongside the framework folder:

```
GitHub/
├── p13n-marketing-experiences-qa-automation/
└── Playwright-Executer/
```

### Step 2 — (Optional) Create a virtual environment for the UI

The UI itself has no extra pip dependencies, but if you want an isolated environment:

```bash
cd Playwright-Executer
python3 -m venv venv
```

You can also point the launcher at the **framework's own venv** — the launcher auto-detects it.

### Step 3 — Verify `config.json`

Open `config.json` and confirm `repo_root` points to the framework:

```json
{
  "repo_root": "/path/to/p13n-marketing-experiences-qa-automation"
}
```

The value pre-filled in `config.json` is the path detected at build time. Edit it if your layout differs.

---

## Launching the UI

### Windows

Double-click **`launch-ui.bat`**.

Or from a terminal:
```cmd
launch-ui.bat
```

### macOS / Linux

Double-click **`launch-ui.sh`** (if your file manager supports it), or run:

```bash
bash launch-ui.sh
```

### From any terminal (alternative)

```bash
cd Playwright-Executer
python3 -m ui_launcher
```

---

## Using the UI

### 1. Framework Repository Root

The path to the automation framework. Defaults to the value in `config.json`. Use **Browse…** to change it. The launcher uses this as the working directory (`cwd`) for every pytest invocation.

### 2. Test Selection

| Field | Description |
|---|---|
| **Suite / Folder** | A folder under `tests/` (e.g. `tests/api`, `tests/web/email_layout_content`) |
| **Test File** | A specific `test_*.py` file within the selected suite |
| **Name filter (-k)** | Substring or expression passed to pytest `-k` (e.g. `test_login or test_checkout`) |

Selecting **All Tests** / **All in Suite** runs everything in that scope.

### 3. Execution Options

| Option | Description |
|---|---|
| **Browser** | `chromium`, `firefox`, or `webkit` |
| **Marker (-m)** | Run only tests tagged with this marker (e.g. `smoke`, `api`) |
| **Workers (-n)** | Number of parallel workers via pytest-xdist (1 = serial) |
| **Headed** | Run browsers visibly (uncheck for headless/CI-style) |
| **Verbose (-v)** | Show full test names in the output log |
| **Auto-open Report** | Automatically open the HTML report when the run finishes |

### 4. .env File

The framework reads environment-specific settings (URLs, credentials, etc.) from a `.env` file in the repository root. Use **Browse…** to select which `.env` file to use for this run.

- `.env` → default / QA settings
- `.env.uat`, `.env.perf`, etc. → other environments (if present in the framework)

The launcher does **not** copy or modify the `.env` file. It passes the selected path as `EXECUTOR_ENV_FILE` in the subprocess environment so custom scripts can use it.

### 5. Extra Args

Raw pytest arguments appended verbatim to the command (e.g. `--timeout=30 -x`).

### 6. Run / Stop

- **▶ RUN** — builds the command and executes it. The button is disabled while running.
- **■ STOP** — sends a termination signal to the pytest process.

### 7. Output Log

Live stdout/stderr from pytest streamed in real time. Color-coded:

| Color | Meaning |
|---|---|
| Teal | PASSED |
| Red | FAILED / ERROR |
| Yellow | WARNING |
| Purple | Command preview |
| Grey | Separator lines |

### 8. Report

After a run the launcher scans for the latest HTML report in `allure/reports/`. Click **Open Latest Report** to open it in your default browser.

Supported report locations (in priority order):
1. `allure/reports/P13n-Marketing-Experiences-QA-Automation-Report.html`
2. `allure/reports/latest/index.html`
3. `allure/reports/html/index.html`
4. Any `*.html` file found recursively under `allure/reports/`

---

## How commands are constructed

The launcher calls `python -m pytest` with the framework's venv Python (auto-detected) and builds a safe argument list — never a shell string, so there is no shell-injection risk.

Example commands the UI might produce:

```bash
# All tests, chromium, headed
python -m pytest -c config/pytest.ini --override-ini "addopts=" \
    --alluredir=allure/results --browser chromium --headed

# API suite, firefox, smoke marker
python -m pytest tests/api -c config/pytest.ini --override-ini "addopts=" \
    --alluredir=allure/results --browser firefox --headed -m smoke

# Specific file, webkit, 4 parallel workers, verbose
python -m pytest tests/web/email_layout_content/test_email_content.py \
    -c config/pytest.ini --override-ini "addopts=" \
    --alluredir=allure/results --browser webkit --headed -n 4 -v
```

The `--override-ini "addopts="` flag clears the defaults from `config/pytest.ini` so the UI selections take full effect without duplicating options.

---

## Repo root and path behaviour

- The launcher always sets subprocess `cwd` to the framework repo root.
- All relative paths (`allure/results`, `config/pytest.ini`, test files) resolve from there.
- The `repo_root` in `config.json` is the single source of truth. Edit it once; all paths follow.

---

## Existing CLI users — nothing changes

Automation engineers can continue to run tests exactly as before:

```bash
cd p13n-marketing-experiences-qa-automation
pytest tests/api -c config/pytest.ini --browser chromium --headed
```

The UI is an additional entry point, not a replacement.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| **"Repo root not found"** | Use Browse… to point to `p13n-marketing-experiences-qa-automation` |
| **"No module named tkinter"** | Install python3-tk (Linux) or use the system Python that ships with Tkinter |
| **Tests fail with "unrecognized arguments"** | A test option may not be supported by the installed plugin versions. Check the Output Log for the exact error. |
| **No report found after run** | The framework may need `allure` CLI installed to generate the HTML dashboard. The shareable `.html` file is generated automatically by the framework's `conftest.py`. |
| **Wrong Python / venv used** | The launcher prefers `<framework-repo>/venv/bin/python`. Check it exists and has all dependencies. |
| **UI won't open on macOS** | Run `bash launch-ui.sh` from a terminal once to see any error messages. |

---

## Project structure

```
Playwright-Executer/
├── config.json           ← user-editable settings (repo root, browsers, markers, …)
├── launch-ui.bat         ← Windows double-click launcher
├── launch-ui.sh          ← macOS / Linux launcher
├── requirements.txt      ← intentionally empty (tkinter is stdlib)
├── README.md             ← this file
└── ui_launcher/
    ├── __init__.py
    ├── __main__.py        ← enables "python -m ui_launcher"
    ├── app.py             ← main Tkinter window and event wiring
    ├── config_reader.py   ← loads / saves config.json
    ├── test_discovery.py  ← scans tests/ for test files and markers
    ├── command_builder.py ← converts UI selections → pytest arg list
    ├── runner.py          ← subprocess execution + live log streaming
    └── report_resolver.py ← finds the latest HTML report artefact
```

---

## Customising `config.json`

All settings are optional — the UI falls back to sensible defaults.

```json
{
  "repo_root":       "/path/to/framework",   // absolute path to the framework
  "default_browser": "chromium",              // pre-selected browser on startup
  "browsers":        ["chromium", "firefox", "webkit"],
  "markers":         ["smoke", "slow", "nbo", "nbc", "api", "web"],
  "report_paths":    ["allure/reports/P13n-Marketing-Experiences-QA-Automation-Report.html"],
  "default_workers": 1,                       // parallel workers spinbox default
  "auto_open_report": false,                  // auto-open report checkbox default
  "window_geometry": "1250x820"               // initial window size
}
```
