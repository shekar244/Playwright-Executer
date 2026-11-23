# Extending Amplify QEA

This guide explains the project structure and the steps required to add new features, tabs, and backend routes to Amplify QEA.

---

## Project structure

```
Amplify-QEA/
Amplify-QEA/
├── amplify.sh / amplify.bat              ← local launchers
├── src/
│   ├── amplify_server/                   ← Flask application package
│   │   ├── server.py                    ← app factory and blueprint registration
│   │   ├── routes/                      ← authenticated Flask blueprints
│   │   ├── models/                      ← SQLite models
│   │   ├── templates/                  ← Jinja2 HTML
│   │   ├── static/                     ← CSS and browser JavaScript
│   ├── jira_insights/                   ← standalone Streamlit package
│   ├── coordinator/                     ← FastAPI Grid coordinator
│   ├── runner/                          ← Grid runner pod package
│   └── gridtest/                        ← runner-baked pytest/Playwright framework
├── tests/
│   ├── amplify_server/                  ← Flask and Insights tests grouped by feature
│   ├── coordinator/                     ← coordinator tests
│   ├── runner/                          ← runner tests
│   └── sample/                          ← baked Playwright sample suite
├── config/
│   └── amplify_server/config.example.json
├── docs/                                ← architecture, setup and testing docs
├── k8s/                                 ← Kubernetes manifests
└── opns/                                ← OpenShift manifests
```

---

## How to add a new feature module (e.g. "Test Design")

### 1 — Backend: create `src/amplify_server/routes/testdesign.py`

```python
from flask import Blueprint, jsonify, request
from amplify_server.config_reader import ConfigReader

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

### 2 — Register the blueprint in `src/amplify_server/server.py`

```python
from amplify_server.routes.testdesign import bp as testdesign_bp
app.register_blueprint(testdesign_bp)
```

### 3 — Add HTML partial: `src/amplify_server/templates/partials/testdesign.html`

```html
<!-- ── Test Design page ── -->
<div id="page-testdesign">
  <div class="dash-body">
    <h2>Test Design</h2>
    <!-- your UI here -->
  </div>
</div>
```

### 4 — Add JS file: `src/amplify_server/static/js/testdesign.js`

```js
// ── Test Design JS ─────────────────────────────────────────────────────────────

async function loadTestDesign() {
  const data = await fetch('/api/testdesign/items').then(r => r.json()).catch(() => ({ items: [] }));
  // render UI from data.items
}
```

### 5 — Wire up in `src/amplify_server/templates/base.html`

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

Add page-specific styles to `src/amplify_server/static/css/amplify.css` (or a `<style>` block in the new partial). All CSS variables (`--bg`, `--accent`, `--surface`, `--green`, `--red`, etc.) are already available.

---

## Making a new tab controllable by admins

Admins show or hide tabs for **all users** in **Admin Panel → Tab Visibility** (settings are stored server-wide in `server_config.json`, not in any user's `config.json`). The list of tabs lives in one place — `UI_TAB_CATALOG` in `src/amplify_server/server_secrets.py` — and the Admin Panel checkboxes, the `ui_tabs` value returned by `/api/config`, and `applyUiTabs()` in `base.html` all derive from it. Two steps plug a new tab in.

### A — Give the nav button a predictable `id`

```html
<!-- In the <nav class="tab-nav"> block in base.html -->
<button type="button" class="tab-btn" id="navbtn-testdesign"
        onclick="switchTab('testdesign')">
  <span class="tab-icon">🎨</span>Test Design
</button>
```

Config sub-tab buttons use `id="ctab-<name>"` instead (e.g. `ctab-mykey`).

### B — Add the tab to `UI_TAB_CATALOG` (`src/amplify_server/server_secrets.py`)

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
| `src/amplify_server/templates/base.html` | Add `id="navbtn-testdesign"` to the nav button |
| `src/amplify_server/server_secrets.py` | Add the entry to `UI_TAB_CATALOG` (key, label, group, default) |

> **Server-side rules** (`save_ui_tabs`): unknown keys and non-boolean values are rejected, at least one main tab must stay visible, and Config cannot be visible with every Config sub-tab hidden.
> Visibility is a UI control only — it hides the tab, it does not block the underlying `/api/...` routes.

---

## Config resolution chain

`src/amplify_server/config_reader.py` implements a three-level merge:

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
| Import shared state via `from amplify_server.routes import state` | `global` only works within the same module |
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

1. **Backend** — copy the route functions into a new blueprint file in `src/amplify_server/routes/` and register it in `src/amplify_server/server.py`.
2. **HTML** — extract the page `<div>` from `index.html` into a new file in `src/amplify_server/templates/partials/`.
3. **JS** — extract the JavaScript into a new file in `src/amplify_server/static/js/`.
4. Wire up the nav button, `switchTab`, `{% include %}`, and `<script src>` in `base.html`.

No manual search-and-splice into a large monolithic file — each concern lives in its own file.
