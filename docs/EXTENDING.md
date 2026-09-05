# Extending Amplyf QEA

This guide explains the project structure and the steps required to add new features, tabs, and backend routes to Amplyf QEA.

---

## Project structure

```
Amplyf-QEA/
├── server.py                        ← app factory: registers blueprints, serves templates
├── routes/
│   ├── __init__.py
│   ├── state.py                     ← shared run state (runner, is_running, SSE queues)
│   ├── history.py                   ← allure result parsing + per-repo run-history persistence
│   ├── executor.py                  ← Blueprint: run/stop/stream/repos/tests/features/git/venv
│   ├── config.py                    ← Blueprint: /api/config/* endpoints
│   ├── dashboard.py                 ← Blueprint: /api/dashboard + /api/report
│   ├── zephyr.py                    ← Blueprint: all /api/zephyr/* and /api/jira/*
│   └── filemanager.py               ← Blueprint: /api/fm/browse, read, save
├── templates/
│   ├── base.html                    ← HTML shell: <head>, CSS, nav, shared JS, {% include %} calls
│   └── partials/
│       ├── executor.html            ← Executor page + Config page HTML
│       ├── dashboard.html           ← Reports Dashboard page HTML
│       ├── testmanagement.html      ← Test Management (Zephyr) page HTML
│       └── filemanager.html         ← File Manager page HTML + inline JS
├── static/
│   └── js/
│       ├── executor.js              ← Executor + Config JS
│       ├── dashboard.js             ← Dashboard charts + history table JS
│       └── testmanagement.js        ← Zephyr / Test Management JS
├── ui_launcher/
│   ├── command_builder.py           ← builds pytest command; venv Python resolution
│   ├── config_reader.py             ← three-level config resolution chain
│   ├── report_resolver.py           ← finds the latest Allure HTML report
│   ├── runner.py                    ← subprocess + SSE stream
│   └── test_discovery.py            ← discovers suites and test files
└── docs/
    └── EXTENDING.md                 ← this file
```

---

## How to add a new feature module (e.g. "Test Design")

### 1 — Backend: create `routes/testdesign.py`

```python
from flask import Blueprint, jsonify, request
from ui_launcher.config_reader import ConfigReader

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

### 2 — Register the blueprint in `server.py`

```python
from routes.testdesign import bp as testdesign_bp
app.register_blueprint(testdesign_bp)
```

### 3 — Add HTML partial: `templates/partials/testdesign.html`

```html
<!-- ── Test Design page ── -->
<div id="page-testdesign">
  <div class="dash-body">
    <h2>Test Design</h2>
    <!-- your UI here -->
  </div>
</div>
```

### 4 — Add JS file: `static/js/testdesign.js`

```js
// ── Test Design JS ─────────────────────────────────────────────────────────────

async function loadTestDesign() {
  const data = await fetch('/api/testdesign/items').then(r => r.json()).catch(() => ({ items: [] }));
  // render UI from data.items
}
```

### 5 — Wire up in `templates/base.html`

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

Add page-specific styles inside the `<style>` block in `templates/base.html`. All CSS variables (`--bg`, `--accent`, `--surface`, `--green`, `--red`, etc.) are already available.

---

## Making a new tab controllable from the 🎛️ Tabs kill-switch

The kill-switch in **Config → 🎛️ Tabs** lets users show/hide any tab without reloading. Follow these four steps to plug a new tab in.

### A — Give the nav button a predictable `id`

```html
<!-- In the <nav class="tab-nav"> block in base.html -->
<button type="button" class="tab-btn" id="navbtn-testdesign"
        onclick="switchTab('testdesign')">
  <span class="tab-icon">🎨</span>Test Design
</button>
```

### B — Register the key in `UI_TAB_MAP` (in base.html inline script)

```js
const UI_TAB_MAP = {
  dashboard:   { nav: 'navbtn-dashboard' },
  zephyr:      { nav: 'navbtn-zephyr' },
  testdesign:  { nav: 'navbtn-testdesign' },   // ← add this
  cfg_git:     { cfg: 'ctab-git' },
  // ... rest unchanged
};
```

`applyUiTabs()` will automatically show/hide `navbtn-testdesign` based on the saved config.

### C — Add a checkbox to the 🎛️ Tabs panel (`templates/partials/executor.html`)

Find the `cfgUiTabs` div and add a checkbox in the **Main Navigation** section:

```html
<label class="checkbox-label" style="gap:10px;cursor:pointer;">
  <input type="checkbox" id="uitab_testdesign" onchange="saveUiTabs()" />
  <span>🎨 Test Design</span>
</label>
```

### D — Register the key in `_UI_TAB_KEYS` (`static/js/executor.js`)

```js
const _UI_TAB_KEYS = [
  'dashboard', 'zephyr',
  'testdesign',   // ← add this
  'cfg_git', 'cfg_tools', 'cfg_mapping', 'cfg_zephyr'
];
```

`loadUiTabs()` reads this array to populate checkboxes; `saveUiTabs()` reads it to persist state.

### Summary of touch-points

| File | Change |
|---|---|
| `templates/base.html` | Add `id="navbtn-testdesign"` to the nav button |
| `templates/base.html` | Add `testdesign: { nav: 'navbtn-testdesign' }` to `UI_TAB_MAP` |
| `templates/partials/executor.html` | Add `<input id="uitab_testdesign">` checkbox in the Tabs panel |
| `static/js/executor.js` | Add `'testdesign'` to `_UI_TAB_KEYS` |

> **Config sub-tabs** (tabs inside the Config page, not main nav) use `{ cfg: 'ctab-mykey' }` in
> `UI_TAB_MAP`. The checkbox id is `uitab_cfg_mykey` and the key in `_UI_TAB_KEYS` is `cfg_mykey`.
> See the existing Git / CLI Tools entries as reference.

---

## Config resolution chain

`ui_launcher/config_reader.py` implements a three-level merge:

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
| Import shared state via `from routes import state` | `global` only works within the same module |
| Modify state as `state._is_running = True` | Attribute assignment crosses module boundaries |
| Use `_history_file(repo)` — not a module-level constant | History is per-repo; the file lives at `{repo}/report_history.json` |
| `ConfigReader().save(cfg)` writes to `_active_config_path` | Respects the resolution chain; never hard-codes a path |
| `ConfigReader().save_tool_config({...})` for `repo_root` / `config_override_path` | These must always be in the bootstrap so the chain can bootstrap itself |
| Keep `escHtml` in `base.html` inline script | Called from HTML `onclick` attributes in all partials |
| Keep `_fmtDur`, `appendLine`, `setRunning`, `switchTab` in `base.html` | Shared globals — must be defined before the external JS files load |
| JS files load after `base.html` inline script | Execution order matters; never use a shared function before it is declared |
| Give every nav button `id="navbtn-<key>"` | Required for `applyUiTabs()` to show/hide it |
| Add key to both `UI_TAB_MAP` and `_UI_TAB_KEYS` | Map controls visibility; array controls checkbox load/save |
| Dynamic Zephyr statuses live in `_ZS` (`testmanagement.js`) | Call `_rebuildStatusDropdowns()` after any mutation to keep all UI in sync |
| Venv Python resolved via `resolve_python(repo, venv_path)` from `command_builder.py` | Use this helper in any new route that needs to run Python in the repo's environment |

---

## Merging features from another project

Given a second project with its own `index.html` and backend routes:

1. **Backend** — copy the route functions into a new blueprint file in `routes/` and register it in `server.py`.
2. **HTML** — extract the page `<div>` from `index.html` into a new file in `templates/partials/`.
3. **JS** — extract the JavaScript into a new file in `static/js/`.
4. Wire up the nav button, `switchTab`, `{% include %}`, and `<script src>` in `base.html`.

No manual search-and-splice into a large monolithic file — each concern lives in its own file.
