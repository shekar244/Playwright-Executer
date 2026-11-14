# Local Docker Testing Guide

Complete step-by-step guide to build and test Amplify QEA locally with Docker.

## Prerequisites

```bash
# Verify Docker is installed
docker --version
# Docker version 20.10+ recommended
```

## Step 1: Build the Docker Image

```bash
cd /Users/admin/Documents/GitHub/amplify

# Build the image
docker build -t amplify-qea:latest .

# Verify build succeeded
docker images | grep amplify-qea
```

**Expected output:**
```
REPOSITORY      TAG       IMAGE ID      CREATED       SIZE
amplify-qea     latest    abc123def456  10 seconds ago  500MB
```

## Step 2: Run the Container Locally

### Option A: Simple Run (No Persistence)

```bash
docker run -p 8080:8080 \
  -e JWT_SECRET_KEY="local-test-key-change-me" \
  -e CONTAINER=1 \
  amplify-qea:latest
```

**Expected output:**
```
Amplify QEA — container mode (multi-tenant)
Listening on 0.0.0.0:8080
```

Open browser: **http://localhost:8080/login**

### Option B: With Volume Persistence (Recommended for Testing)

This persists database and user workspaces across container restarts.

```bash
# Create a local directory for persistent data
mkdir -p ~/amplify-test-data

# Run with volume mount
docker run -p 8080:8080 \
  -e JWT_SECRET_KEY="local-test-key-change-me" \
  -e CONTAINER=1 \
  -v ~/amplify-test-data:/app/data \
  amplify-qea:latest
```

**Verify volume mount:**
```bash
# In another terminal, check the volume contents
ls -la ~/amplify-test-data/
# Should see: amplify.db, workspaces/
```

## Step 3: Test User Registration

### Via Browser

1. Navigate to **http://localhost:8080/register**
2. Fill in form:
   - **Username:** `testuser1`
   - **Password:** `MyTestPass123!`
   - **Email:** `test@example.com`
3. Click "Create Account"
4. Should redirect to main app dashboard

### Via cURL

```bash
curl -X POST http://localhost:8080/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "username": "testuser1",
    "password": "MyTestPass123!",
    "email": "test@example.com"
  }' | jq .

# Expected response:
# {
#   "ok": true,
#   "user": {
#     "id": 1,
#     "username": "testuser1",
#     "email": "test@example.com"
#   },
#   "access_token": "eyJ0eXAiOiJKV1QiLCJhbGc..."
# }
```

## Step 4: Test Login

### Via Browser

1. Navigate to **http://localhost:8080/login**
2. Fill in:
   - **Username:** `testuser1`
   - **Password:** `MyTestPass123!`
3. Click "Sign In"
4. Should see main dashboard

### Via cURL

```bash
curl -X POST http://localhost:8080/api/auth/login \
  -H "Content-Type: application/json" \
  -c cookies.txt \
  -d '{
    "username": "testuser1",
    "password": "MyTestPass123!"
  }' | jq .

# Save the access_token for testing
TOKEN="eyJ0eXAiOiJKV1QiLCJhbGc..."
```

## Step 5: Test Protected Routes

### Get Current User Info

```bash
TOKEN="your-access-token-here"

curl -X GET http://localhost:8080/api/auth/me \
  -H "Authorization: Bearer $TOKEN" | jq .

# Expected:
# {
#   "id": 1,
#   "username": "testuser1",
#   "email": "test@example.com"
# }
```

### Get User's Config (Empty Initially)

```bash
curl -X GET http://localhost:8080/api/config \
  -H "Authorization: Bearer $TOKEN" | jq .

# Expected: default config object for user 1
```

## Step 6: Test User Isolation

Create a second user and verify data isolation:

```bash
# Register user 2
curl -X POST http://localhost:8080/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "username": "testuser2",
    "password": "DifferentPass456!"
  }' | jq .

# Login as user 2
TOKEN2=$(curl -X POST http://localhost:8080/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "username": "testuser2",
    "password": "DifferentPass456!"
  }' | jq -r .access_token)

# Get user 2's config
curl -X GET http://localhost:8080/api/config \
  -H "Authorization: Bearer $TOKEN2" | jq .

# Verify user 2's config is different from user 1's
```

## Step 7: Verify Persistent Volume

Stop the container and check data persists:

```bash
# List running containers
docker ps

# Stop the container (use container ID)
docker stop <container-id>

# Check files persisted
ls -la ~/amplify-test-data/
ls -la ~/amplify-test-data/workspaces/

# Expected structure:
# ~/amplify-test-data/
#   ├── amplify.db              (SQLite database with users)
#   ├── workspaces/
#   │   ├── 1/                  (user 1 workspace)
#   │   │   ├── config/
#   │   │   │   └── config.json
#   │   │   └── history/
#   │   │       └── report_history.json
#   │   ├── 2/                  (user 2 workspace)
#   │   │   ├── config/
#   │   │   │   └── config.json
#   │   │   └── history/
#   │   │       └── report_history.json
```

## Step 8: Test Persistence

Restart the container and verify data is still there:

```bash
# Run container again with same volume
docker run -p 8080:8080 \
  -e JWT_SECRET_KEY="local-test-key-change-me" \
  -e CONTAINER=1 \
  -v ~/amplify-test-data:/app/data \
  amplify-qea:latest

# Wait for startup (~5 seconds)

# Login as user 1 again
curl -X POST http://localhost:8080/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "username": "testuser1",
    "password": "MyTestPass123!"
  }' | jq .

# Should succeed — user data persisted!
```

## Step 9: Test Logout

```bash
TOKEN="user-token-here"

curl -X POST http://localhost:8080/api/auth/logout \
  -H "Authorization: Bearer $TOKEN" | jq .

# Expected:
# { "ok": true }

# Try to use token after logout (should fail)
curl -X GET http://localhost:8080/api/auth/me \
  -H "Authorization: Bearer $TOKEN"

# Expected: 401 Unauthorized
```

## Step 10: Test Token Refresh

```bash
TOKEN="user-token-here"

curl -X POST http://localhost:8080/api/auth/refresh \
  -H "Authorization: Bearer $TOKEN" | jq .

# Expected:
# {
#   "ok": true,
#   "access_token": "new-token-here"
# }
```

## Testing Checklist

Use this checklist to verify all functionality:

```bash
# Create a test script: test-local-docker.sh

#!/bin/bash
set -e

echo "🧪 Testing Amplify QEA Local Docker Deployment"
echo ""

# Test 1: User Registration
echo "✓ Test 1: User Registration"
curl -X POST http://localhost:8080/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username":"alice","password":"alice123","email":"alice@test.com"}' \
  -s | jq .ok

# Test 2: User Login
echo "✓ Test 2: User Login"
TOKEN=$(curl -X POST http://localhost:8080/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"alice","password":"alice123"}' \
  -s | jq -r .access_token)
echo "Got token: ${TOKEN:0:20}..."

# Test 3: Get User Info
echo "✓ Test 3: Get Current User"
curl -X GET http://localhost:8080/api/auth/me \
  -H "Authorization: Bearer $TOKEN" \
  -s | jq .username

# Test 4: Get User Config
echo "✓ Test 4: Get User Config"
curl -X GET http://localhost:8080/api/config \
  -H "Authorization: Bearer $TOKEN" \
  -s | jq .repo_root

# Test 5: Second User Registration (Isolation Test)
echo "✓ Test 5: Register Second User"
curl -X POST http://localhost:8080/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username":"bob","password":"bob456"}' \
  -s | jq .ok

# Test 6: Logout
echo "✓ Test 6: Logout"
curl -X POST http://localhost:8080/api/auth/logout \
  -H "Authorization: Bearer $TOKEN" \
  -s | jq .ok

echo ""
echo "✅ All tests passed!"
```

Run the test script:
```bash
chmod +x test-local-docker.sh
./test-local-docker.sh
```

## Troubleshooting

### Container won't start

```bash
# Check logs
docker logs <container-id>

# Common issues:
# - Port 8080 already in use: use different port
#   docker run -p 8081:8080 ...
# - Volume permission issue: use chmod
#   chmod 777 ~/amplify-test-data
```

### Database errors

```bash
# Check if database file exists
ls -la ~/amplify-test-data/amplify.db

# Check database integrity
sqlite3 ~/amplify-test-data/amplify.db ".tables"

# Should show:
# user_sessions  users
```

### Token not working

```bash
# Verify token is valid
TOKEN="your-token"
curl -X GET http://localhost:8080/api/auth/me \
  -H "Authorization: Bearer $TOKEN" -v

# Check response headers for errors
```

### User workspace not isolated

```bash
# Check workspace directories
ls -la ~/amplify-test-data/workspaces/

# Should have separate directories:
# 1/, 2/, 3/, etc.

# Check configs are different
diff ~/amplify-test-data/workspaces/1/config/config.json \
     ~/amplify-test-data/workspaces/2/config/config.json

# Should show differences (different user IDs in history, etc.)
```

## Cleanup

```bash
# Stop all containers
docker stop $(docker ps -a -q)

# Remove the image
docker rmi amplify-qea:latest

# Remove test data
rm -rf ~/amplify-test-data

# Clean up dangling images
docker image prune -f
```

## Next: Deploy to OpenShift

Once local testing passes, you can deploy to OpenShift:

```bash
# Tag for registry
docker tag amplify-qea:latest <your-registry>/amplify-qea:latest

# Push to registry
docker push <your-registry>/amplify-qea:latest

# Create OpenShift deployment (see ARCHITECTURE.md)
```

---

## Summary

| Test | Command | Expected |
|------|---------|----------|
| Register | POST /api/auth/register | 201 Created |
| Login | POST /api/auth/login | 200 OK + token |
| Get User | GET /api/auth/me | 200 OK + user info |
| Get Config | GET /api/config | 200 OK + config |
| Logout | POST /api/auth/logout | 200 OK |
| Token Refresh | POST /api/auth/refresh | 200 OK + new token |
| Invalid Token | GET /api/config (no token) | 401 Unauthorized |
| User Isolation | User 1 config ≠ User 2 config | Different files |
| Persistence | Restart container + login | Same users exist |

