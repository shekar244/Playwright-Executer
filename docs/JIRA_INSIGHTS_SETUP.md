# Jira Insights Setup Guide

Jira Insights is a **separate Streamlit application** that runs alongside the main Flask server.

## 🚀 Quick Start

### Option 1: Auto-Start from UI (Recommended)

1. Click the **📈 Jira Insights** tab in the main navigation
2. You should see a **"▶ Start Jira Insights"** button
3. Click the button
4. Wait ~10 seconds for Streamlit to boot
5. The app will embed in the iframe

**Status indicators:**
- 🟢 **Green dot** = Running and healthy
- 🔴 **Red dot** = Not running or failed
- ⚪ **White dot** = Starting...

### Option 2: Manual Command Line

If auto-start fails, you can start Streamlit manually:

```bash
cd /Users/admin/Documents/GitHub/amplify

# Start Streamlit on port 8501
streamlit run amplify/jira_insights/app.py --server.port 8501
```

Then refresh the **📈 Jira Insights** tab and it should connect.

---

## 📋 Prerequisites

### 1. Install Dependencies

All dependencies are in `amplify-requirements.txt` but confirm Streamlit is installed:

```bash
pip install streamlit
```

### 2. Jira Credentials (Optional)

To pull data from Jira directly, configure these in **Config → Zephyr Config**:
- **Jira URL:** `https://yourcompany.atlassian.net`
- **Jira Username:** Your email
- **Jira API Token:** From `id.atlassian.com` → Account Settings → API tokens

If not configured, you can still:
- **Upload** a Jira CSV/Excel export file
- **Build reports** from uploaded data

---

## 🔧 Docker Container

### In a Running Container

The Streamlit app is **NOT started by default** in Docker. You need to:

1. **Option A: Start from UI (if image has terminal access)**
   - Just use the "▶ Start Jira Insights" button in the tab

2. **Option B: Manual startup in container**
   ```bash
   docker exec -it amplify-qea-test \
     streamlit run amplify/jira_insights/app.py --server.port 8501
   ```

3. **Option C: Modify Dockerfile to auto-start** (not recommended)
   - Streamlit needs interactive terminal
   - Flask and Streamlit both want port binding
   - Separate services = cleaner design

### Ports

- **Flask (Main app):** `8080`
- **Streamlit (Jira Insights):** `8501`

Both must be exposed if running in Docker.

---

## ✅ Verify It's Running

### Check via curl:

```bash
curl http://localhost:8501/_stcore/health
# Should respond: ok
```

### Check tab status:

Look at the Jira Insights tab header:
- Shows the port it's running on
- Shows connection status
- Shows error messages if startup failed

### Check logs:

```bash
# Logs are written to workspace dir
tail -f ~/.amplify-qea/workspaces/<user_id>/streamlit.log
```

---

## 🐛 Troubleshooting

### Issue: "Connection refused" on Jira Insights tab

**Cause:** Streamlit server is not running

**Fix:** Click "▶ Start Jira Insights" button and wait 10 seconds

### Issue: "Timed out waiting for Jira Insights to start"

**Cause:** Streamlit is taking >45 seconds to boot, or failed to start

**Fix:**
```bash
# Check if process is running
ps aux | grep streamlit

# Check error logs
cat ~/.amplify-qea/workspaces/1/streamlit.log

# Try manual start to see errors
streamlit run amplify/jira_insights/app.py
```

### Issue: "Streamlit is not installed"

**Cause:** Missing dependency

**Fix:**
```bash
pip install streamlit>=1.45,<1.57
```

### Issue: Port 8501 already in use

**Cause:** Another process is using port 8501

**Fix:** Stop the other process or change the port:

```bash
# Start on a different port
INSIGHTS_PORT=8502 streamlit run amplify/jira_insights/app.py
```

Then in the Flask app environment:
```bash
export INSIGHTS_PORT=8502
```

### Issue: Upload fails or app crashes

**Cause:** Missing optional dependencies

**Fix:**
```bash
pip install openpyxl pandas
```

---

## 📊 What You Can Do in Jira Insights

### 📊 Dashboard
- Pin reports and view them on one page
- Global filter bar applies to all reports
- Switch between light/dark mode

### 🧮 Report Builder
- Drag-and-drop pivot table builder
- Add charts without code
- Save reports to dashboard

### 🎨 Explorer
- PyGWalker drag-and-drop canvas
- Explore data interactively
- Create custom visualizations

### 🗃️ Data
- View all pulled/uploaded issues
- Links to Jira
- Download as CSV

---

## 🔗 Data Sources

### 1. Pull from Jira (JQL)
- Requires Jira credentials
- Queries Jira API directly
- Real-time updates

### 2. Upload CSV/Excel
- Export from Jira issue navigator
- Upload file in UI
- No Jira API credentials needed

### 3. Local CSV/Excel Files
- Create your own issue data
- Upload for analysis
- Format: any CSV or Excel with issue columns

---

## 📝 Configuration Files

Streamlit config: `amplify/.streamlit/config.toml`

Workspace data: `~/.amplify-qea/workspaces/{user_id}/`
- `streamlit.log` — Error logs
- `jira_insights_state/` — Dashboard/report definitions (auto-created)

---

## 🚀 Production (OpenShift)

### As its own pod at `/jira-insights`

The manifests in `opns/` and `k8s/` run Insights in its own pod, served at `/jira-insights` on QEA's host, with the same users, workspaces and sign-in as QEA. See *Jira Insights (its own pod, at `/jira-insights`)* in `opns/README.md` for the settings. The example below is the older layout with Insights on its own host.

### As Sidecar Service

In Kubernetes/OpenShift, run Jira Insights as a **separate service**:

```yaml
apiVersion: v1
kind: Service
metadata:
  name: amplify-insights
spec:
  ports:
    - port: 8501
      targetPort: 8501

---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: amplify-insights
spec:
  template:
    spec:
      containers:
        - name: insights
          image: amplify-qea:latest
          command: ["streamlit", "run", "amplify/jira_insights/app.py", "--server.port", "8501"]
          ports:
            - containerPort: 8501
```

Then in main Flask deployment, set:
```bash
INSIGHTS_PUBLIC_URL=https://insights.example.com
```

---

## 🔗 User-Scoped Data

Each user has their own Jira Insights workspace:

```
~/.amplify-qea/workspaces/{user_id}/
├── streamlit.log              # Startup/error logs
└── jira_insights_state/       # Dashboard definitions (auto-created)
    ├── datasets/              # Uploaded/pulled issues
    ├── reports.json           # Saved reports
    └── kpis.json              # Dashboard KPIs
```

Data is **completely isolated** per user — no cross-user data leakage.

---

## 📞 If It Still Doesn't Work

1. **Run with verbose logging:**
   ```bash
   streamlit run amplify/jira_insights/app.py --logger.level=debug
   ```

2. **Check Python version:**
   ```bash
   python --version
   # Should be 3.10+
   ```

3. **Check dependencies:**
   ```bash
   pip list | grep -i streamlit
   # Should show streamlit 1.45-1.57
   ```

4. **Clear Streamlit cache:**
   ```bash
   rm -rf ~/.streamlit/
   rm -rf ~/.cache/streamlit/
   streamlit run amplify/jira_insights/app.py
   ```

5. **Check if port is actually listening:**
   ```bash
   lsof -i :8501
   # Should show streamlit process
   ```

---

## Summary

| Scenario | Action |
|----------|--------|
| Fresh install | Click "▶ Start Jira Insights" button on the tab |
| Container restart | Auto-starts from UI button or manual CLI |
| Port conflict | Set `INSIGHTS_PORT=8502` |
| Slow boot | Wait 15 seconds, then refresh |
| Connection refused | Check `~/.amplify-qea/workspaces/1/streamlit.log` |
| Jira credentials | Configure in Config → Zephyr Config (optional) |

