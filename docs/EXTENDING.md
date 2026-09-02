# Extending Playwright Executor

## Project structure after modularisation

```
Playwright-Executer/
  server.py                        ← app factory — registers blueprints, serves templates
  routes/
    __init__.py
    state.py                       ← shared run state (runner, is_running, SSE queues)
    history.py                     ← allure result parsing + run-history persistence
    executor.py                    ← Blueprint: run / stop / stream / repos / tests / features / git
    config.py                      ← Blueprint: /api/config endpoints
    git.py                         ← Blueprint: /api/git/commands + /api/git/run
    dashboard.py                   ← Blueprint: /api/dashboard + /api/report
    zephyr.py                      ← Blueprint: all /api/zephyr/* and /api/jira/*
  templates/
    base.html                      ← HTML shell: <head>, CSS, nav, shared JS, {% include %} calls
    partials/
      executor.html                ← Executor page + Config page HTML
      dashboard.html               ← Reports Dashboard page HTML
      testmanagement.html          ← Test Management (Zephyr) page HTML
  static/
    js/
      executor.js                  ← Executor + Config JS
      dashboard.js                 ← Dashboard charts + table JS
      testmanagement.js            ← Zephyr / Test Management JS
    index.html                     ← legacy single-file build (kept for reference)
  docs/
    EXTENDING.md                   ← this file
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
<button type="button" class="tab-btn" onclick="switchTab('testdesign')">
  <span class="tab-icon">🎨</span>Test Design
</button>
```

**b) Add the page to `switchTab`** (in the inline `<script>` in base.html):
```js
// Change:
const pages = ['executor','tools','dashboard','zephyr'];
// To:
const pages = ['executor','tools','dashboard','zephyr','testdesign'];
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

Add any page-specific styles inside the `<style>` block in `templates/base.html`. All existing CSS variables (`--bg`, `--accent`, `--surface`, etc.) are already available.

---

## Merging features from another project (index.html-2)

Given a second project with its own `index.html` and backend routes:

1. **Backend**: copy the route functions into a new blueprint file in `routes/` and register it in `server.py`.
2. **HTML**: extract the page `<div>` from index.html-2 into a new file in `templates/partials/`.
3. **JS**: extract the JavaScript into a new file in `static/js/`.
4. Wire up the nav button, `switchTab`, `{% include %}`, and `<script src>` in `base.html`.

No manual search-and-splice into a 4000-line file — each concern lives in its own file.

---

---

## Making a new tab controllable from the 🎛️ Tabs kill-switch

The kill-switch in **Config → 🎛️ Tabs** lets users show/hide any tab without reloading.
When you add a new feature tab (e.g. Test Design), follow these 4 steps to plug it in.

### A — Add a nav button ID to `templates/base.html`

Give the nav button a predictable `id` so `applyUiTabs` can target it:

```html
<!-- In the <nav class="tab-nav"> block -->
<button type="button" class="tab-btn" id="navbtn-testdesign"
        onclick="switchTab('testdesign')">
  <span class="tab-icon">🎨</span>Test Design
</button>
```

### B — Register the key in `UI_TAB_MAP` (also in `base.html`)

The inline `<script>` in `base.html` contains `UI_TAB_MAP`. Add one entry:

```js
const UI_TAB_MAP = {
  dashboard:   { nav: 'navbtn-dashboard' },
  zephyr:      { nav: 'navbtn-zephyr' },
  // ↓ add this line:
  testdesign:  { nav: 'navbtn-testdesign' },
  cfg_git:     { cfg: 'ctab-git' },
  // ... rest unchanged
};
```

`applyUiTabs()` will now automatically show/hide `navbtn-testdesign` based on the saved config.

### C — Add a checkbox to the 🎛️ Tabs panel (`templates/partials/executor.html`)

Find the `cfgUiTabs` div and add a checkbox inside the **Main Navigation** section:

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
  'testdesign',          // ← add this
  'cfg_git', 'cfg_tools', 'cfg_mapping', 'cfg_zephyr'
];
```

`loadUiTabs()` reads this array to populate checkboxes; `saveUiTabs()` reads it to persist state.

### That's all — summary of touch-points

| File | Change |
|---|---|
| `templates/base.html` | Add `id="navbtn-testdesign"` to the nav button |
| `templates/base.html` | Add `testdesign: { nav: 'navbtn-testdesign' }` to `UI_TAB_MAP` |
| `templates/partials/executor.html` | Add `<input id="uitab_testdesign">` checkbox in the Tabs panel |
| `static/js/executor.js` | Add `'testdesign'` to `_UI_TAB_KEYS` |

> **Config sub-tabs** (tabs inside Config, not main nav) use `{ cfg: 'ctab-mykey' }` in `UI_TAB_MAP`
> instead of `{ nav: '...' }`. The checkbox id is `uitab_cfg_mykey` and the key in `_UI_TAB_KEYS`
> is `cfg_mykey`. See the existing Git / CLI Tools entries as a reference.

---

## Key rules

| Rule | Reason |
|---|---|
| Import shared state via `from routes import state` | `global` only works within the same module |
| Modify state as `state._is_running = True` | Attribute assignment crosses module boundaries |
| `HISTORY_FILE` path uses `Path(__file__).parent.parent` | history.py is one level inside `routes/` |
| Keep `escHtml` in `base.html` | It's called from HTML `onclick` attributes in all partials |
| Keep `_fmtDur`, `appendLine`, `setRunning`, `switchTab` in `base.html` | Called across all JS files |
| JS files load after `base.html` inline script | Execution order matters — shared globals are defined first |
| Give every nav button an `id="navbtn-<key>"` | Required for `applyUiTabs()` to target it |
| Add key to both `UI_TAB_MAP` and `_UI_TAB_KEYS` | Map controls visibility; array controls checkbox load/save |
