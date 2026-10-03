"""
Jira Insights — JQL-driven Jira reporting for Amplify QEA.

A Streamlit side-car app (app.py) that pulls Jira issues with JQL or from a
Jira CSV/Excel export and lets users build pivot reports and charts from the
UI. The Flask app starts it on demand (launcher.py) and embeds it in an iframe.

Only launcher.py and settings.py are imported by Flask; everything else needs
the optional reporting dependencies (streamlit, pandas, plotly, pygwalker).
"""
