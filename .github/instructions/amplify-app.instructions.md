---
applyTo: "amplify/**"
---

# Amplify app (Flask, Jira Insights, browser UI)

- **Adding a feature follows `docs/EXTENDING.md`**: a blueprint in `amplify/routes/<name>.py`, registered in `amplify/server.py`; an HTML partial in `templates/partials/`; a JS file in `static/js/`; a nav button with `id="navbtn-<key>"`; and an entry in `UI_TAB_CATALOG` (`server_secrets.py`) so admins can show or hide the tab. Its "Key rules" table is binding: shared state via `from amplify.routes import state`, shared JS globals such as `escHtml` stay in `base.html`, and the JS files load after that inline script.
- **Auth**: protect routes with `@require_auth` and admin-only ones with `@require_admin` (`amplify/auth_utils.py`); the caller is `g.user`. Every user has an isolated workspace: never read or write another user's files.
- **File paths from a client are never trusted.** Do not build a filesystem path from request data without proving it stays inside the folder it belongs to. `report_viewer.py` (signed, per-user links), `grid_reports.py` and `grid_snapshot.py` show the patterns; each has hostile-input tests.
- **Request bodies**: call `request_body.json_object()` first in a POST handler, so malformed JSON is a JSON 400 and never an HTML 500.
- **Payloads Amplify does not control** (a pod's test results, the coordinator's answers, history files on disk) are read only through `amplify/payload_coerce.py`: its helpers always return the requested type and never raise. Add new accessors there; do not index into such payloads directly.
- **Escape everything dynamic that reaches HTML.** Use `escHtml` (`base.html`) or `_gEsc` (`grid.js`); put ids in `data-*` attributes and handle clicks with one delegated listener, not inline handlers with interpolated values. A new render function that shows pod, coordinator or user data gets a hostile case in `amplify/tests/js/ui_escaping_check.js` (run by `test_ui_escaping.py`, which needs `node`).
- **Server secrets** (Jira and Zephyr tokens, `GRID_API_TOKEN`, `JWT_SECRET_KEY`) live in `server_secrets.py` or the environment. They never appear in a response, a log line or the UI except masked (`SECRET_MASK`).
- **Jira Insights** is a separate Streamlit process. QEA signs a user in to it with a short-lived, single-use token (`amplify/sso.py`), so both services must share `JWT_SECRET_KEY`.
- **Tests** are in `amplify/tests/` (Insights tests in `amplify/tests/jira_insights/`, shared Grid helpers in `grid_testkit.py`). A new endpoint that reads external data gets a hostile-input case, in the style of `test_hostile_payloads.py`. Run `venv/bin/python -m pytest amplify/tests` (Python 3.13).
