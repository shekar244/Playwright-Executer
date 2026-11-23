---
name: amplify-repository-change
description: Safely implement features and fixes in the Amplify QEA, Jira Insights, and Grid src-layout repository.
---

# Amplify repository changes

## Layout

- `src/amplify_server/` — Flask server package.
- `src/jira_insights/` — standalone Streamlit package.
- `src/coordinator/` — FastAPI Grid coordinator.
- `src/runner/` — Grid runner pod package.
- `src/gridtest/` — pytest-Playwright framework shared with runner pods.
- `tests/amplify_server/` — Flask and Insights tests grouped by feature.
- `tests/coordinator/` — coordinator tests.
- `tests/runner/` — runner tests.
- `tests/sample/` — Playwright sample suite baked into runner images.
- `config/amplify_server/` — application configuration examples.
- `docs/` — architecture, setup, extension and testing documentation.

## Change workflow

1. Read repository instructions and the relevant `docs/` file.
2. Keep server imports under `amplify_server.*`, Jira imports under `jira_insights.*`, coordinator imports under `coordinator.*`, and runner imports under `runner.*`.
3. Put Flask behavior in `src/amplify_server/`, Grid behavior in `src/coordinator/` or `src/runner/`, and sample framework behavior in `src/gridtest/`.
4. Put tests in the matching `tests/<area>/<feature>/` location.
5. Add or update documentation in `docs/` and the relevant deployment README.
6. Run the smallest feature test set, then broad tests for cross-cutting changes.
7. Never commit secrets or machine-specific paths.
