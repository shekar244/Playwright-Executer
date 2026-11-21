# Extending Amplify QEA

This guide explains the project structure and the steps required to add new features, tabs, and backend routes to Amplify QEA.

---

## Project structure

```
Amplify-QEA/
├── amplify.sh / amplify.bat         ← launchers (python -m amplify.server)
└── amplify/
    ├── server.py                        ← app factory: registers blueprints, serves templates
    ├── consolidated.py                  ← Reporting Dashboard logic: Grid + local runs in one model, filters, roll-ups (pure, unit-tested)
    ├── payload_coerce.py                ← reads numbers/text from payloads we don't control (pod results, history files): never raises
    ├── request_body.py                  ← json_object(): a malformed JSON body is a JSON 400, never an HTML 500
    ├── grid_data.py                     ← reads Grid runs from the coordinator for the dashboard (cached; a finished run is kept for good only once its Allure results are in)
    ├── grid_snapshot.py                 ← Grid code delivery: git snapshot of a repo's HEAD → shared volume (GRID_CODE_DIR, with retention pruning) or coordinator (safe tar extraction)
    ├── grid_reports.py                  ← a Grid run's Allure 3 report, fetched from the coordinator and cached per run + ETag
    ├── report_viewer.py                 ← signed, per-user links for viewing HTML reports inside the dashboard
    ├── routes/
    │   ├── __init__.py
    │   ├── state.py                     ← shared run state (runner, is_running, SSE queues)
    │   ├── history.py                   ← allure result parsing + per-repo run-history persistence
    │   ├── executor.py                  ← Blueprint: run/stop/stream/repos/tests/features/git/venv
    │   ├── config.py                    ← Blueprint: /api/config/* endpoints
    │   ├── grid.py                      ← Blueprint: /api/grid/* — proxy to the Grid coordinator; POST /api/grid/runs starts a run (optionally from a repo snapshot, see grid_snapshot.py)
    │   ├── testdata.py                  ← Blueprint: /api/testdata/* — list, upload and delete files in the active cloned repo
    │   ├── dashboard.py                 ← Blueprint: /api/dashboard (+ /overview, /run, /report-link), /rv viewer, /api/report
    │   ├── zephyr.py                    ← Blueprint: all /api/zephyr/* and /api/jira/*
    │   ├── filemanager.py               ← Blueprint: /api/fm/browse, read, save
    │   └── insights.py                  ← Blueprint: /api/insights/* (start/stop the Streamlit side-car)
    ├── jira_insights/                   ← Jira Insights Streamlit app (embedded via <iframe>)
    │   ├── app.py                       ← Streamlit entry: sidebar data source + view switcher
    │   ├── launcher.py                  ← side-car process start/stop/health (used by routes/insights.py)
    │   ├── jira_client.py               ← JQL search (Cloud /search/jql + Server/DC /search)
    │   ├── transform.py                 ← issues / CSV export → flat DataFrame + derived columns
    │   ├── pivot.py                     ← ReportSpec + pivot engine (pure pandas; Gauge/Meter %, filters)
    │   ├── charts.py                    ← Plotly figure factory (bars, lines, donut, heatmap, treemap)
    │   ├── chart_theme.py               ← colours, validated themes, Plotly template
    │   ├── dials.py                     ← Gauge (arc) + Meter (needle) with configurable colour bands
    │   ├── layout.py                    ← dashboard rows for drag-and-drop arranging
    │   ├── kpi.py                       ← configurable KPI tiles
    │   ├── executions.py                ← Zephyr test runs → flat table
    │   ├── zephyr.py                    ← test-run fetch via routes/zephyr.py (_z_call / _jira_call)
    │   ├── store.py                     ← workspace persistence (datasets, reports, tiles, settings)
    │   └── ui/                          ← Streamlit views (dashboard, tile editor, builder, explorer, data, Zephyr source)
    ├── .streamlit/config.toml           ← Streamlit server + theme settings for Jira Insights
    ├── templates/
    │   ├── base.html                    ← HTML shell: <head>, CSS, nav, shared JS, {% include %} calls
    │   └── partials/
    │       ├── executor.html            ← Executor page + Config page HTML
    │       ├── dashboard.html           ← Reporting Dashboard page (filters, KPIs, runs, drill-down, viewers)
    │       ├── grid.html                ← Grid page (new run form, runner pods, runs, run detail)
    │       ├── testmanagement.html      ← Test Management (Zephyr) page HTML
    │       ├── insights.html            ← Jira Insights page (iframe host)
    │       └── filemanager.html         ← File Manager page HTML + inline JS
    ├── static/
    │   ├── css/amplify.css              ← Apple-style design system (light + dark)
    │   ├── css/amplify.css              ← Apple-style design system (light + dark)
    │   └── js/
    │       ├── executor.js              ← Executor + Config JS
    │       ├── dashboard.js             ← Reporting Dashboard JS (consolidated Grid + local runs)
    │       ├── grid.js                  ← Grid tab JS: renders pods/runs/results (all data escaped; ids via data-* attributes + one delegated click handler)
    │       ├── testmanagement.js        ← Zephyr / Test Management JS
    │       └── insights.js              ← Jira Insights start/embed JS
    ├── command_builder.py               ← builds pytest command; venv Python resolution
    ├── config_reader.py                 ← three-level config resolution chain
    ├── report_resolver.py               ← finds the latest Allure HTML report
    ├── runner.py                        ← subprocess + SSE stream
    ├── test_discovery.py                ← discovers suites and test files
    └── tests/                           ← unit tests
```

---

## How to add a new feature module (e.g. "Test Design")

### 1 — Backend: create `amplify/routes/testdesign.py`

```python
from flask import Blueprint, jsonify, request
from amplify.config_reader import ConfigReader

bp = Blueprint("testdesign", __name__)

@bp.route("/api/testdesign/items")
def list_items():
    cfg = ConfigReader().load()
    return jsonify({"items": cfg.get("testdesign_items", [])})

@bp.route("/api/testdesign/items", methods=["POST"])
def save_items():
    reader = ConfigReader()
    cfg    = reader.load()
    cfg["testdesign_items"] = request.json or []
    reader.save(cfg)
    return jsonify({"ok": True})
```

### 2 — Register the blueprint in `amplify/server.py`

```python
from amplify.routes.testdesign import bp as testdesign_bp
app.register_blueprint(testdesign_bp)
```

### 3 — Add HTML partial: `amplify/templates/partials/testdesign.html`

```html
<!-- ── Test Design page ── -->
<div id="page-testdesign">
  <div class="dash-body">
    <h2>Test Design</h2>
    <!-- your UI here -->
  </div>
</div>
```

### 4 — Add JS file: `amplify/static/js/testdesign.js`

```js
// ── Test Design JS ─────────────────────────────────────────────────────────────

async function loadTestDesign() {
  const data = await fetch('/api/testdesign/items').then(r => r.json()).catch(() => ({ items: [] }));
  // render UI from data.items
}
```

### 5 — Wire up in `amplify/templates/base.html`

**a) Add a nav button** (in the `<nav class="tab-nav">` block):
```html
<button type="button" class="tab-btn" id="navbtn-testdesign"
        onclick="switchTab('testdesign')">
  <span class="tab-icon">🎨</span>Test Design
</button>
```

**b) Add the page to `switchTab`** (in the inline `<script>` in base.html):
```js
// In the pages array:
const pages = ['executor','tools','dashboard','zephyr','filemanager','testdesign'];
// Also add the load trigger:
if (tab === 'testdesign') loadTestDesign();
```

**c) Include the partial** (before the closing `</body>`):
```html
{% include 'partials/testdesign.html' %}
```

**d) Load the JS** (before the closing `</body>`):
```html
<script src="/static/js/testdesign.js"></script>
```

### 6 — Add CSS (if needed)

Add page-specific styles to `amplify/static/css/amplify.css` (or a `<style>` block in the new partial). All CSS variables (`--bg`, `--accent`, `--surface`, `--green`, `--red`, etc.) are already available.

---

## Making a new tab controllable by admins

Admins show or hide tabs for **all users** in **Admin Panel → Tab Visibility** (settings are stored server-wide in `server_config.json`, not in any user's `config.json`). The list of tabs lives in one place — `UI_TAB_CATALOG` in `amplify/server_secrets.py` — and the Admin Panel checkboxes, the `ui_tabs` value returned by `/api/config`, and `applyUiTabs()` in `base.html` all derive from it. Two steps plug a new tab in.

### A — Give the nav button a predictable `id`

```html
<!-- In the <nav class="tab-nav"> block in base.html -->
<button type="button" class="tab-btn" id="navbtn-testdesign"
        onclick="switchTab('testdesign')">
  <span class="tab-icon">🎨</span>Test Design
</button>
```

Config sub-tab buttons use `id="ctab-<name>"` instead (e.g. `ctab-mykey`).

### B — Add the tab to `UI_TAB_CATALOG` (`amplify/server_secrets.py`)

```python
UI_TAB_CATALOG = [
    # ...
    {"key": "testdesign", "label": "Test Design", "group": "main",   "default": True},
    # Config sub-tab: key "cfg_mykey" controls button #ctab-mykey
    {"key": "cfg_mykey",  "label": "My Key",      "group": "config", "default": True},
]
```

That is all. The button id is derived from the key: `"<key>"` → `#navbtn-<key>`, `"cfg_<name>"` → `#ctab-<name>`.

### Summary of touch-points

| File | Change |
|---|---|
| `amplify/templates/base.html` | Add `id="navbtn-testdesign"` to the nav button |
| `amplify/server_secrets.py` | Add the entry to `UI_TAB_CATALOG` (key, label, group, default) |

> **Server-side rules** (`save_ui_tabs`): unknown keys and non-boolean values are rejected, at least one main tab must stay visible, and Config cannot be visible with every Config sub-tab hidden.
> Visibility is a UI control only — it hides the tab, it does not block the underlying `/api/...` routes.

---

## Config resolution chain

`amplify/config_reader.py` implements a three-level merge:

```
1. {tool_dir}/config.json          ← always read first; provides repo_root + config_override_path
2. config_override_path (if set)   ← overrides everything; return immediately after merging
3. {repo_root}/config.json         ← repo-level config; merges on top of tool config
```

`ConfigReader.load()` returns the merged dict with two internal keys:

| Key | Value |
|---|---|
| `_active_config_path` | Absolute path of the file that was ultimately used |
| `_active_config_source` | `"tool"` / `"repo"` / `"override"` |

`ConfigReader.save(cfg)` writes to `cfg["_active_config_path"]` (strips `_` keys before writing).

`ConfigReader.save_tool_config(updates)` always writes specific keys directly to the tool bootstrap file — used to persist `repo_root` and `config_override_path` regardless of which config is active.

When adding a new config key, add its default to `_DEFAULTS` in `config_reader.py` so it is always present in `load()` output.

---

## Key rules

| Rule | Reason |
|---|---|
| Import shared state via `from amplify.routes import state` | `global` only works within the same module |
| Modify state as `state._is_running = True` | Attribute assignment crosses module boundaries |
| Use `_history_file(repo)` — not a module-level constant | History is per-repo; the file lives at `{repo}/report_history.json` |
| `ConfigReader().save(cfg)` writes to `_active_config_path` | Respects the resolution chain; never hard-codes a path |
| `ConfigReader().save_tool_config({...})` for `repo_root` / `config_override_path` | These must always be in the bootstrap so the chain can bootstrap itself |
| Keep `escHtml` in `base.html` inline script | Called from HTML `onclick` attributes in all partials |
| Keep `_fmtDur`, `appendLine`, `setRunning`, `switchTab` in `base.html` | Shared globals — must be defined before the external JS files load |
| JS files load after `base.html` inline script | Execution order matters; never use a shared function before it is declared |
| Give every nav button `id="navbtn-<key>"` | Required for `applyUiTabs()` to show/hide it |
| Add every new tab to `UI_TAB_CATALOG` in `server_secrets.py` | Single source for defaults, the Admin Panel checkboxes, and the `ui_tabs` value from `/api/config` |
| Dynamic Zephyr statuses live in `_ZS` (`testmanagement.js`) | Call `_rebuildStatusDropdowns()` after any mutation to keep all UI in sync |
| Venv Python resolved via `resolve_python(repo, venv_path)` from `command_builder.py` | Use this helper in any new route that needs to run Python in the repo's environment |

---

## Merging features from another project

Given a second project with its own `index.html` and backend routes:

1. **Backend** — copy the route functions into a new blueprint file in `amplify/routes/` and register it in `amplify/server.py`.
2. **HTML** — extract the page `<div>` from `index.html` into a new file in `amplify/templates/partials/`.
3. **JS** — extract the JavaScript into a new file in `amplify/static/js/`.
4. Wire up the nav button, `switchTab`, `{% include %}`, and `<script src>` in `base.html`.

No manual search-and-splice into a large monolithic file — each concern lives in its own file.
