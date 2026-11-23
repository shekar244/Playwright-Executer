# Testing

Source and tests use a standard `src/` layout.

## Test layout

```text
tests/amplify_server/
  admin/
  auth/
  dashboard/
  grid/
  insights/
  jira_insights/
  repository/
  security/
  testdata/
tests/coordinator/
tests/runner/
tests/sample/
src/amplify_server/
src/jira_insights/
src/coordinator/
src/runner/
src/gridtest/
```

## Running tests

```bash
venv/bin/python -m pytest tests/amplify_server
venv/bin/python -m pytest tests/amplify_server/grid
venv/bin/python -m pytest tests/amplify_server/jira_insights
pytest tests/coordinator
pytest tests/runner
venv/bin/python -m pytest tests/sample/test_sample.py
```

Every behavior change should add or update a focused test under the matching feature folder. Test names should describe the behavior in full sentences.
