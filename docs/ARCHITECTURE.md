# Amplify QEA — Multi-Tenant Architecture

## Overview

Amplify QEA has been transformed into a **multi-tenant application** deployable on OpenShift with user authentication and isolated workspaces. Each user registers with a username/password, receives a JWT token, and gets their own isolated workspace that doesn't interfere with other users.

---

## Architecture Changes

### 1. Authentication & Authorization

**New Files:**
- `src/amplify_server/routes/auth.py` — Authentication endpoints (register, login, logout, refresh)
- `src/amplify_server/auth_utils.py` — JWT token generation, verification, and `@require_auth` decorator
- `src/amplify_server/models/user.py` — User model with bcrypt password hashing
- `src/amplify_server/models/db.py` — SQLite database setup and initialization

**Endpoints:**
```
POST   /api/auth/register        — Create new user
POST   /api/auth/login           — Authenticate and issue JWT
POST   /api/auth/logout          — Clear session
POST   /api/auth/refresh         — Refresh JWT token
GET    /api/auth/me              — Get current user info
```

**Flow:**
1. User visits `/login` or `/register`
2. Credentials submitted to `/api/auth/login` or `/api/auth/register`
3. Backend validates credentials and hashes password with bcrypt (12 rounds)
4. JWT token issued (24-hour expiration)
5. Token stored in httpOnly cookie `access_token`
6. All API requests check for valid JWT via `@require_auth` decorator

---

### 2. User Workspaces

**New Files:**
- `src/amplify_server/storage.py` — User workspace path resolution and initialization

**Structure:**
```
~/.amplify-qea/
  amplify.db                    # SQLite database (local dev)
  workspaces/
    1/                          # User ID 1
      config/
        config.json             # User's executor config
      history/
        report_history.json     # User's run history
    2/                          # User ID 2
      config/
        config.json
      history/
        report_history.json
```

**On Container:**
```
/app/
  data/
    amplify.db
    workspaces/
      {user_id}/
        config/config.json
        history/report_history.json
```

---

### 3. Multi-Tenant Config Storage

**New Files:**
- `src/amplify_server/user_config_reader.py` — User-scoped version of ConfigReader

**Changes:**
All routes that read/write config now use `UserConfigReader(g.user.id)` instead of `ConfigReader()`. This ensures:
- Each user's config is stored in their workspace
- No cross-user interference
- Config loading/saving is automatic from user context

**Modified Routes:**
- `src/amplify_server/routes/config.py` — All endpoints updated with `@require_auth` and `UserConfigReader`
- `src/amplify_server/routes/executor.py` — Needs `@require_auth` and `UserConfigReader` updates
- `src/amplify_server/routes/dashboard.py` — Needs `@require_auth` and user-scoped history
- Other route files similarly updated

---

### 4. Frontend Authentication

**New Pages:**
- `src/amplify_server/templates/login.html` — Login form with email/password
- `src/amplify_server/templates/register.html` — Registration form with validation

**Updated Pages:**
- `src/amplify_server/templates/base.html` — Added user menu in header with logout button

**Session Management:**
- JWT tokens in httpOnly cookies (secure, inaccessible to JavaScript)
- Auto-refresh before expiration
- Logout clears cookie and redirects to login page

---

### 5. Database Schema

**SQLite (MVP) or PostgreSQL (production):**

```sql
CREATE TABLE users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE NOT NULL,
  email TEXT,
  password_hash TEXT NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE user_sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  token_hash TEXT NOT NULL,
  expires_at TIMESTAMP NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
```

---

### 6. Deployment

**Local Development:**
```bash
pip install -r amplify-requirements.txt
python3 -m amplify_server.models.db    # Initialize database
python3 src/amplify_server/server.py        # Run server
```

Database stored in `~/.amplify-qea/amplify.db`, workspaces in `~/.amplify-qea/workspaces/`

**Docker / OpenShift:**
```dockerfile
FROM python:3.13-slim
WORKDIR /app
RUN mkdir -p /app/data /app/workspaces && chmod 777 /app/data /app/workspaces
# ... copy files ...
CMD ["sh", "-c", "python -m amplify_server.models.db && gunicorn ..."]
```

**Environment Variables:**
```bash
DATABASE_URL="sqlite:////app/data/amplify.db"  # or "postgresql://..."
JWT_SECRET_KEY="<secure-random-key>"            # Change in production!
PORT=8080
CONTAINER=1
```

**Volume Mounts (OpenShift):**
```yaml
volumes:
  - name: amplify-data
    persistentVolumeClaim:
      claimName: amplify-data-pvc
volumeMounts:
  - name: amplify-data
    mountPath: /app/data
```

---

## Implementation Status

### ✅ Completed
- [x] Database schema and SQLite setup
- [x] User model with bcrypt hashing
- [x] JWT token generation and verification
- [x] Auth routes (register, login, logout, refresh)
- [x] `@require_auth` middleware decorator
- [x] User workspace initialization
- [x] User-scoped config storage
- [x] Login/register HTML templates
- [x] User menu in header with logout
- [x] Docker configuration for multi-tenant deployment
- [x] Requirements.txt updated (bcrypt, PyJWT)

### ⏳ To Complete (per existing routes)
1. **Executor routes** (`src/amplify_server/routes/executor.py`):
   - Add `@require_auth` to all endpoints
   - Use `UserConfigReader(g.user.id)` for config
   - Update test discovery to user workspace

2. **Dashboard routes** (`src/amplify_server/routes/dashboard.py`):
   - Add `@require_auth` to all endpoints
   - Use user-scoped history path

3. **Zephyr routes** (`src/amplify_server/routes/zephyr.py`):
   - Add `@require_auth` to all endpoints
   - Store credentials in user's workspace config

4. **File Manager routes** (`src/amplify_server/routes/filemanager.py`):
   - Add `@require_auth` to all endpoints
   - Scope file access to user's workspace and repo

5. **Other routes** (git, studio, insights):
   - Add `@require_auth` decorator
   - Update any config/state reads to user-scoped versions

6. **Testing:**
   - Test user registration and login flow
   - Test config isolation between users
   - Test history isolation between users
   - Test workspace creation on registration

---

## Key Design Decisions

### SQLite for MVP
- Simple file-based storage
- No separate database service needed
- Easy to migrate to PostgreSQL later (just change DATABASE_URL and add migrations)
- Good enough for single OpenShift pod deployment

### JWT with httpOnly Cookies
- **Stateless** — server doesn't need to store token state
- **Secure** — httpOnly prevents JavaScript access
- **Scalable** — each pod can verify tokens independently
- **Standard** — mature libraries (PyJWT, Flask)

### User Workspace Isolation
- **Filesystem-based** — each user has their own directory
- **No shared state** — config, history, temp files are isolated
- **Simple permissions** — OpenShift can mount single PVC, app handles per-user subdirs

### No Additional Service Dependencies
- SQLite for user/auth data
- Flask already in use
- Minimal new dependencies: bcrypt, PyJWT (both lightweight, mature)

---

## Migration Path

To complete the multi-tenant implementation:

1. **Update all route files** to use `@require_auth` and `UserConfigReader`
2. **Test end-to-end** registration → login → executor → config save → logout → login again
3. **Add integration tests** for user isolation (one user shouldn't see another's history)
4. **Deploy to OpenShift** with persistent volume for `/app/data`
5. **Monitor** auth failures, workspace isolation, token expiration

---

## Security Considerations

- ✅ **Password**: bcrypt with 12 rounds (slow, resistant to brute force)
- ✅ **Token**: JWT signed with HS256, 24-hour expiration
- ✅ **Cookie**: httpOnly, Lax SameSite, no Secure flag for local dev (enable in production)
- ✅ **Isolation**: Each user's workspace is separate directory, enforced by app logic
- ⚠️ **Production**:
  - Set `JWT_SECRET_KEY` to a strong random value
  - Enable `Secure` flag on cookies (HTTPS only)
  - Use PostgreSQL for better concurrency
  - Add rate limiting to `/api/auth/` endpoints
  - Add audit logging to monitor access patterns

---

## API Reference (New)

### POST /api/auth/register
Register a new user.

**Request:**
```json
{
  "username": "alice",
  "password": "password123",
  "email": "alice@example.com"  // optional
}
```

**Response (201):**
```json
{
  "ok": true,
  "user": { "id": 1, "username": "alice", "email": "alice@example.com" },
  "access_token": "eyJ0eXAiOiJKV1QiLCJhbGc..."
}
```

### POST /api/auth/login
Authenticate user and issue JWT.

**Request:**
```json
{
  "username": "alice",
  "password": "password123"
}
```

**Response (200):**
```json
{
  "ok": true,
  "user": { "id": 1, "username": "alice", "email": null },
  "access_token": "eyJ0eXAiOiJKV1QiLCJhbGc..."
}
```

### POST /api/auth/logout
Clear session (sets secure cookie to expire).

**Response (200):**
```json
{ "ok": true }
```

### GET /api/auth/me
Get current authenticated user.

**Response (200):**
```json
{
  "id": 1,
  "username": "alice",
  "email": null
}
```

### POST /api/auth/refresh
Refresh JWT token before expiration.

**Response (200):**
```json
{
  "ok": true,
  "access_token": "eyJ0eXAiOiJKV1QiLCJhbGc..."
}
```

---

## Example Deployment (OpenShift)

```bash
# Create PVC for persistent data
oc create -f - <<EOF
kind: PersistentVolumeClaim
apiVersion: v1
metadata:
  name: amplify-data-pvc
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 10Gi
  storageClassName: standard
EOF

# Build and deploy
oc new-build --binary --name amplify-qea --strategy=docker
oc start-build amplify-qea --from-dir=. --follow
oc new-app amplify-qea \
  -e DATABASE_URL="sqlite:////app/data/amplify.db" \
  -e JWT_SECRET_KEY="$(openssl rand -base64 32)" \
  -e CONTAINER=1 \
  -p PORT=8080
oc set volume deployment/amplify-qea \
  --add --type=pvc --claim-name=amplify-data-pvc -m /app/data
oc expose service/amplify-qea
```

---

## Next Steps

1. **Complete remaining route updates** to use `@require_auth` and `UserConfigReader`
2. **Run integration tests** to verify user isolation
3. **Build and test Docker image** locally
4. **Deploy to OpenShift** with persistent volume
5. **Monitor** for auth/isolation issues
6. **Document** user-facing registration and login flow

