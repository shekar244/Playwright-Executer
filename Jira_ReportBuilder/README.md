# Jira Report Builder

Pull Jira issues with JQL, **Zephyr test runs** (executions), or a Jira CSV/Excel export, and build reports, pivot tables, gauges and charts from the browser. You don't write any code.

- **📊 Dashboard** shows KPI tiles (issues, open, stories, defects, defects per story, story points) and every saved report. One filter row (date range plus any field) applies to all of them.
- **🧮 Report Builder** lets you pick rows, a series, a measure (count, sum, average…), the date grain and filters. There are 12 chart types: column, stacked column, bar, stacked bar, line, area, donut, heatmap, treemap, **gauge**, **meter** and number. You get a live preview, the pivot table and CSV/Excel download, and you save the result to the dashboard.
- **🎨 Explorer** is a Tableau-style drag-and-drop canvas (PyGWalker). Charts you build there are saved per dataset.
- **🗃️ Data** shows the raw issues with clickable Jira keys, plus downloads.

## Quick start

Requires **Python 3.10+** (3.13 recommended).

| OS | Command |
|---|---|
| macOS / Linux | `./run.sh` |
| Windows | `run.bat` |

The first run creates `venv/` and installs `requirements.txt`, which takes about a minute. The app then opens at **http://localhost:8501**. If something is already using that port (an earlier run, for example), the launcher stops it first. To use a different port: `PORT=8600 ./run.sh` (Windows: `set PORT=8600` then `run.bat`).

To run it by hand:

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
venv/bin/streamlit run app.py
```

## Connect to Jira (and Zephyr)

Open **🔌 Jira / Zephyr config** in the sidebar and pick where the credentials come from. It uses the same fields as Amplify QEA's Config → Zephyr.

### Option 1 — Enter here

Fill in the form, click **Test Jira** / **Test Zephyr**, then **Save**. The values are saved to `workspace/jira_connection.json`, which only your user can read.

| Field | Jira Cloud | Jira Server / Data Center |
|---|---|---|
| Jira URL | `https://yourcompany.atlassian.net` | `https://jira.yourcompany.com` |
| Email / username | your Atlassian email | leave **empty** (Bearer token) |
| Jira API token | [id.atlassian.com → Security → API tokens](https://id.atlassian.com/manage-profile/security/api-tokens) | Profile → Personal Access Tokens |
| Auth type | Auto (Basic) | **Auto**, or **Bearer** for a personal access token |
| Zephyr type | Cloud (needs the keys below) | **Server / DC** (uses your Jira login, no keys) |
| Zephyr access key / secret key | Zephyr Squad → API Keys | — |
| Atlassian account id | your account id | — |

**Auth type.** *Auto* uses Basic auth (username + token) when a username is set, and Bearer otherwise. On a non-Cloud Jira, a Basic login that's rejected is retried once as Bearer, which covers a Data Center PAT entered together with a username. Pick *Basic* or *Bearer* to force one.

**Zephyr type.**
- *Cloud* calls Zephyr Squad Cloud's API with JWT-signed requests (access key, secret key and account id), exactly as Amplify QEA does.
- *Server / DC* calls ZAPI on your Jira host (`/rest/zapi/latest`) with your Jira login.
- *Auto* picks Cloud when the keys are filled in, and Server / DC otherwise.

### Option 2 — Config file (credentials kept outside the app)

Choose **Config file**, enter the path to your file (`~` and `$VARS` work), check the **✅ Found …** line, then click **Use this file**.

- The file is re-read every time it's needed, so edits and token rotations apply immediately.
- Only the **path** is stored. The secrets never leave your file.
- If the file has several sections (e.g. `[jira-prod]` and `[jira-dc]`), pick one under **Section**. *Auto* reads `[jira]`, then `[zephyr]`, then the others.
- Optional keys: `auth_type = auto | basic | bearer` and `zephyr_type = auto | cloud | server`.
- If `verify_ssl` is missing from the file, it defaults to **false**, the same default as Amplify QEA.

```ini
; Keep this file outside the project folder.
[zephyr]
jira_url    = https://yourcompany.atlassian.net
username    = you@company.com
api_token   = your-jira-api-token
access_key  = your-zephyr-access-key
secret_key  = your-zephyr-secret-key
account_id  = 5b10a2844c20165700ede21g
project_key = ABC
verify_ssl  = true
```

Accepted formats are `.ini`, `.cfg` and `.conf`. You can also point it at **Amplify QEA's `config.json`**, and it will read that file's `"zephyr"` section. The keys `url`, `token`, `email` and `pat` are accepted as aliases.

### Environment variables

These override both options, which is useful for Docker:

| Variable | Meaning |
|---|---|
| `JIRA_URL`, `JIRA_USERNAME`, `JIRA_API_TOKEN` | Jira connection |
| `ZEPHYR_ACCESS_KEY`, `ZEPHYR_SECRET_KEY`, `ZEPHYR_ACCOUNT_ID` | Zephyr Squad connection |
| `JIRA_VERIFY_SSL` | `false` only behind a corporate TLS proxy |
| `JIRA_INSIGHTS_HOME` | where data is stored (default `./workspace`) |

JQL pulls match Amplify QEA's `_jira_search_jql`: the same headers, and `GET /rest/api/latest/search/jql` first, falling back to `GET /rest/api/2/search` on 404/405/410 or a response without `issues`. This app adds paging and rate-limit retries. Zephyr Cloud uses Amplify's JWT signing and endpoints unchanged.

You don't need a Jira connection to use **⬆️ Upload Jira export**. In Jira, open *Filters → Export → CSV (all fields)*, then upload the file.

## Zephyr test runs (execution reports)

Open **🧪 New dataset from Zephyr (test runs)** in the sidebar.

- **Project / version / cycle.** This is the same flow as Amplify's Test Management.
  1. Enter the project key and click **Load**.
  2. Pick a version, then one or more cycles. Leave cycles empty for all of them.
  3. Click **Fetch test runs**.
- **ZQL query.** Use any Zephyr Query Language search, e.g. `project = "ABC" AND fixVersion = "Release 3.2" AND executionStatus != UNEXECUTED`.

Every test run becomes a row with these columns: *Key, Summary, Execution Status, Result (Passed / Failed / Blocked / In progress / Not run), Executed (Yes/No), Cycle, Folder, Version, Executed By, Executed On, Assignee, Priority, Labels, Components, Defects, Defect Keys, Comment*. **↻ Refresh** re-runs the same cycles or query.

The first test-run dataset adds ready-made execution reports and KPI tiles:
- **Reports:** Pass rate meter, Pass rate by cycle (one gauge per cycle), Execution results donut, Results by cycle, Test runs per day, Failures by component, Test runs by tester.
- **KPI tiles:** Executions, Passed, Failed, Blocked, Not run, Pass rate.

Results use the status colours (Passed green, Failed red, Blocked orange, In progress amber, Not run grey). Issue reports and tiles hide on test-run datasets, and the execution ones hide on issue datasets.

## Using it

1. **Create a dataset.** In the sidebar, choose *New dataset from JQL*, give it a name, enter the JQL (e.g. `project = ABC AND issuetype in (Bug, Story) AND created >= -90d`) and click **Fetch issues**. **↻ Refresh** re-runs the JQL later.
2. **Dashboard.** Six starter reports are included. On each card, **⛶** opens that report full screen (large chart, full table, downloads) and **✎** edits it. Hover a chart for Plotly's own fullscreen and image export.
3. **KPI tiles.** Turn on **✎ Edit tiles** to configure the headline numbers:
   - drag tiles to reorder them;
   - set each tile's label, icon and colour, and the data behind it: issue count or sum/average of a field, filters (values, *contains* text such as `bug|defect`, or last N days), an optional **divide by** metric for ratios, and a caption;
   - add, delete or reset tiles. A live preview shows the tile while you edit.
4. **Arrange the dashboard.** Turn on **✥ Arrange** and drag reports within a row or between rows. Each row splits its width evenly: one report is full width, two are halves, four are quarter tiles. Drop a report on the last group to start a new row. Cards in a row share one height so they line up. The layout saves automatically, and **↺ Reset layout** restores the default.
5. **Report Builder.** Configure a chart, then click **Save** to pin it to the dashboard. Reports work with any dataset that has the columns they use.
   - Every report gets a permanent number (**R-001**, R-002, …). It shows on its card, in the builder's report list, in Arrange and in download file names.
   - Numbers are never reused, and a copy gets a new one.
   - Clicking ✎ on a card, or picking a report in the list, opens the saved report with all its settings (name, chart, fields, filters, dial bands).
   - Unsaved edits are kept if you switch views and come back.
   - **Gauge** (a filled arc) and **Meter** (a needle over fixed colour bands) draw one dial for the slice, or one per category under *… per* (up to 8).
   - **Counts as complete / success when…** (e.g. `Status = Done` or `Result = Passed`) turns the dial into completion, 0–100%.
   - Without it, the dial shows the measure on a **minimum…maximum** range, e.g. average rating on a 1–5 scale.
   - **Colour bands:** choose *Red · Amber · Green* or *Red · Amber · Light green · Green*, and set where each band ends as a % of the dial (e.g. red to 25%, amber to 50%, light green to 75%).
   - The gauge arc takes the colour of the band its value falls in, and the meter's needle points into the bands. Choose **Lower** when smaller is better (e.g. open defects); that flips the colours.
6. **🎨 Colour theme** (bottom of the sidebar) switches between *Aurora* (vivid) and *Classic*. Both palettes are checked for colour-blind safety.

Every dataset gets these derived columns: **Open/Closed**, **Age (days)**, **Resolution Time (days)**. For the best results, include `Created`, `Resolved`, `Status Category`, `Issue Type`, `Status`, `Priority` and `Story Points` in your exports.

## Where data lives

Everything is stored in `workspace/` (it is git-ignored):

```
workspace/
  datasets/<name>.parquet + .json   pulled / uploaded issues + metadata
  specs/<name>.json                 Explorer charts
  reports.json                      saved dashboard reports
  kpis.json                         KPI tiles
  settings.json                     UI preferences (colour theme, dashboard layout)
  jira_connection.json              connection: typed values (incl. token) or the credentials-file path
```

To move the tool to another machine, copy this folder without `venv/` and `workspace/`. Copy `workspace/` as well if you want to keep your data and reports.

## Docker

```bash
docker build -t jira-report-builder .
docker run -p 8501:8501 -v jira-rb-data:/app/workspace \
  -e JIRA_URL=https://yourcompany.atlassian.net \
  -e JIRA_USERNAME=you@company.com -e JIRA_API_TOKEN=xxxx \
  jira-report-builder
```

The image works on OpenShift, which runs containers under a random user ID: `/app` is group-writable for group 0. It also includes a health check on `/_stcore/health`.

## Project layout

```
app.py                  Streamlit entry point
jira_insights/
  jira_client.py        JQL search: Cloud /search/jql (token paging) + Server/DC /search
  zephyr.py             Zephyr Squad Cloud (JWT, ported from Amplify QEA) + Server/DC (ZAPI) clients
  executions.py         Zephyr test runs → flat table (status, result, cycle, tester…)
  kpi.py                configurable KPI tiles
  transform.py          REST issues / CSV export → flat table + derived columns
  pivot.py              ReportSpec + pivot engine (pure pandas)
  layout.py             dashboard rows (drag-and-drop arrangement)
  charts.py             Plotly charts + colour themes
  store.py              workspace persistence
  settings.py           Jira/Zephyr connection (app values, config.ini/config.json, env) + workspace
  ui/                   sidebar, connection, Zephyr source, dashboard, tile editor, builder, explorer, data, styling
.streamlit/config.toml  theme + server settings
tests/                  unit tests
```

## Tests

```bash
venv/bin/pip install pytest
venv/bin/python -m pytest
```

## Notes

- **Streamlit is pinned below 1.57.** PyGWalker's Streamlit integration needs Streamlit's Tornado server, which 1.57 replaced. If you upgrade anyway, the Explorer falls back to a read-only view.
- PyGWalker telemetry is switched off, so no usage data leaves your machine.
