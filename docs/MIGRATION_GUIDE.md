# Multi-Tenant Migration Guide

This guide explains how to update the remaining route files to work with the multi-tenant architecture.

## Pattern for Route Updates

### 1. Import Required Modules

Add to the top of your route file:
```python
from flask import g
from amplify_server.auth_utils import require_auth
from amplify_server.user_config_reader import UserConfigReader
```

### 2. Protect Endpoints with @require_auth

Before:
```python
@bp.route("/api/dashboard")
def get_dashboard():
    cfg = ConfigReader().load()
    ...
```

After:
```python
@bp.route("/api/dashboard")
@require_auth
def get_dashboard():
    cfg = UserConfigReader(g.user.id).load()
    ...
```

### 3. Replace ConfigReader with UserConfigReader

All places that read/write config:
```python
# Before
reader = ConfigReader()
cfg = reader.load()

# After
reader = UserConfigReader(g.user.id)
cfg = reader.load()
```

### 4. Use User-Scoped Paths for History and Files

History reading/writing:
```python
# Before
history_path = Path(repo_root) / "report_history.json"

# After
from amplify_server.storage import get_user_history_path
history_path = get_user_history_path(g.user.id)
```

---

## Routes to Update

### 1. src/amplify_server/routes/executor.py

**Changes:**
- Add `@require_auth` to all endpoints
- Replace `ConfigReader()` with `UserConfigReader(g.user.id)` in:
  - `run_tests()` — config loading for test execution
  - `get_tests()` — test discovery config
  - `browse_folder()` — any config reads
  - `venv_status()` — venv_path from config
  - `venv_install()` — venv config
- Update history path resolution:
  - Import `from amplify_server.storage import get_user_history_path`
  - Replace local repo-scoped history with user-scoped: `get_user_history_path(g.user.id)`

**Key functions:**
```python
@bp.route("/api/run", methods=["POST"])
@require_auth
def run_tests():
    cfg = UserConfigReader(g.user.id).load()
    repo_root = cfg.get("repo_root", "")
    # ... rest of logic uses repo_root ...
```

### 2. src/amplify_server/routes/dashboard.py

**Changes:**
- Add `@require_auth` to all endpoints
- Use `UserConfigReader(g.user.id)` for config
- Use `get_user_history_path(g.user.id)` for history loading/saving
- History JSON reading:
  ```python
  @bp.route("/api/dashboard")
  @require_auth
  def get_dashboard():
      history_path = get_user_history_path(g.user.id)
      history_data = json.loads(history_path.read_text()) if history_path.exists() else []
      # ... build dashboard response ...
  ```

### 3. src/amplify_server/routes/zephyr.py

**Changes:**
- Add `@require_auth` to all endpoints
- Use `UserConfigReader(g.user.id)` for:
  - Zephyr credentials from config
  - Field mapping config
  - Results mapping config
- Example:
  ```python
  @bp.route("/api/zephyr/config")
  @require_auth
  def get_zephyr_config():
      cfg = UserConfigReader(g.user.id).load()
      zephyr_creds = cfg.get("zephyr", {})
      return jsonify(zephyr_creds)
  ```

### 4. src/amplify_server/routes/filemanager.py

**Changes:**
- Add `@require_auth` to all endpoints
- Scope file access to user's workspace and repos
- File browse should still work from user-selected repos
- Example:
  ```python
  @bp.route("/api/fm/browse", methods=["GET"])
  @require_auth
  def browse():
      path = request.args.get("path", "")
      # Validate path is within user's configured repos
      cfg = UserConfigReader(g.user.id).load()
      repo_root = cfg.get("repo_root", "")
      # ... rest of logic ...
  ```

### 5. src/amplify_server/routes/git.py

**Changes:**
- Add `@require_auth` to all endpoints
- Use `UserConfigReader(g.user.id)` for git commands config
- Git operations are already scoped to repo_root, which is user-configured

### 6. src/amplify_server/routes/studio.py

**Changes:**
- Add `@require_auth` to all endpoints
- Use `UserConfigReader(g.user.id)` for any config reads

### 7. src/amplify_server/routes/insights.py

**Changes:**
- Add `@require_auth` if exposing API endpoints
- Studio/insights don't need user scoping if they run independently

---

## Testing the Migration

After updating each route file:

```bash
# 1. Test registration and login
curl -X POST http://localhost:7777/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username":"testuser","password":"testpass123"}'

# 2. Capture token from response
TOKEN="eyJ0eXAiOiJKV1QiLCJhbGc..."

# 3. Test protected endpoint
curl -X GET http://localhost:7777/api/config \
  -H "Authorization: Bearer $TOKEN"

# 4. Verify config is user-scoped
ls ~/.amplify-qea-grid/workspaces/1/config/config.json  # should exist

# 5. Register another user and verify isolation
curl -X POST http://localhost:7777/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username":"user2","password":"pass456789"}'

# 6. Verify user 2's config is separate
ls ~/.amplify-qea-grid/workspaces/2/config/config.json  # should be different
```

---

## Common Patterns

### Pattern 1: Config Read and Modify

```python
@bp.route("/api/config/save-setting", methods=["POST"])
@require_auth
def save_setting():
    reader = UserConfigReader(g.user.id)
    cfg = reader.load()
    
    # Modify
    cfg["some_setting"] = request.json.get("value")
    
    # Save
    reader.save(cfg)
    
    return jsonify({"ok": True})
```

### Pattern 2: History File Read

```python
@bp.route("/api/report/history")
@require_auth
def get_history():
    from amplify_server.storage import get_user_history_path
    history_path = get_user_history_path(g.user.id)
    
    if not history_path.exists():
        return jsonify([])
    
    history = json.loads(history_path.read_text(encoding="utf-8"))
    return jsonify(history)
```

### Pattern 3: History File Write (Append)

```python
def append_to_history(user_id, run_record):
    from amplify_server.storage import get_user_history_path
    history_path = get_user_history_path(user_id)
    
    history = []
    if history_path.exists():
        history = json.loads(history_path.read_text(encoding="utf-8"))
    
    history.insert(0, run_record)  # Prepend
    history = history[:200]  # Keep last 200
    
    history_path.write_text(json.dumps(history, indent=2), encoding="utf-8")
```

---

## Database Changes (If Needed Later)

For PostgreSQL migration:

1. Update `DATABASE_URL` environment variable
2. Install `psycopg2`:
   ```bash
   pip install psycopg2-binary
   ```
3. Run migrations (add `src/amplify_server/migrations/` later if needed)
4. Both SQLite and PostgreSQL use same query interface via `sqlite3` for now

For production, consider:
- Adding SQLAlchemy ORM (instead of raw sqlite3)
- Adding alembic for migrations
- Adding connection pooling

---

## Checklist for Completion

- [ ] Update all route files with `@require_auth`
- [ ] Update all `ConfigReader()` → `UserConfigReader(g.user.id)`
- [ ] Update all history path reads to use `get_user_history_path(g.user.id)`
- [ ] Test registration → config save → history append → login as different user
- [ ] Test that user 1's history is isolated from user 2's
- [ ] Test executor test discovery with user-scoped config
- [ ] Test Zephyr credentials isolation (each user has their own)
- [ ] Build Docker image and test
- [ ] Deploy to OpenShift with PVC for `/app/data`
- [ ] Load test with multiple concurrent users

---

## Quick Reference: Key Functions

```python
# Auth
from amplify_server.auth_utils import require_auth, generate_jwt, verify_jwt
from flask import g

@require_auth
def protected_route():
    user_id = g.user.id
    username = g.user.username

# Config
from amplify_server.user_config_reader import UserConfigReader

reader = UserConfigReader(g.user.id)
cfg = reader.load()
cfg["key"] = value
reader.save(cfg)

# Storage
from amplify_server.storage import (
    get_user_workspace_dir,
    get_user_config_path,
    get_user_history_path,
    initialize_user_workspace
)

ws_dir = get_user_workspace_dir(g.user.id)
config_path = get_user_config_path(g.user.id)
history_path = get_user_history_path(g.user.id)
```

