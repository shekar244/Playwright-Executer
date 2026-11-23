---
applyTo: "src/amplify_server/**,src/jira_insights/**,tests/amplify_server/**"
---

# Amplify Server repository changes

- Flask source lives in `src/amplify_server/`; Jira Insights source lives in the separate `src/jira_insights/` package.
- Server tests live in `tests/amplify_server/`, grouped by feature: `admin`, `auth`, `dashboard`, `grid`, `insights`, `jira_insights`, `repository`, `security`, and `testdata`.
- Runtime application config examples live in `config/amplify_server/`; `config/amplify_server/config.json` is local gitignored state and must never contain secrets.
- Every behavior change gets a focused test under the matching feature folder.
- Run `venv/bin/python -m pytest tests/amplify_server/<feature>` for focused tests and `venv/bin/python -m pytest tests/amplify_server` for the complete server suite.
- Document new behavior in `docs/`; keep deployment-specific READMEs beside `k8s/`, `opns/`, or the repo root.
- Server imports use `amplify_server.*`; Jira Insights imports use `jira_insights.*`.
