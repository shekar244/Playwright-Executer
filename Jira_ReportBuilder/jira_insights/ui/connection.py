"""
🔌 Jira / Zephyr config — where the app gets its credentials:

  • Enter here   — typed into the sidebar, saved to the workspace (owner-only file)
  • Config file  — an external config.ini (or Amplify QEA config.json) given by
                   path; re-read on every use, only the path is stored

Test buttons check Jira (REST /myself) and Zephyr (JWT-signed status call)
without saving anything. Secret values are never shown.
"""
from __future__ import annotations

import streamlit as st

from ..jira_client import JiraClient, JiraError
from ..settings import (Connection, JiraSettings, read_credentials_file, save_credentials_file,
                        save_jira_settings, saved_app_settings, with_env)
from ..zephyr import ZephyrClient

INI_EXAMPLE = """\
; Jira / Zephyr credentials for Jira Report Builder.
; Keep this file outside the project folder. Same keys as Amplify QEA → Config → Zephyr.
[zephyr]
jira_url    = https://yourcompany.atlassian.net
username    = you@company.com
api_token   = your-jira-api-token
access_key  = your-zephyr-access-key
secret_key  = your-zephyr-secret-key
account_id  = 5b10a2844c20165700ede21g
project_key = ABC
verify_ssl  = true
"""

_SOURCES = {"app": "Enter here", "file": "Config file"}
_LABELS = {
    "url": "Jira URL", "username": "username", "token": "API token", "verify_ssl": "verify SSL",
    "zephyr_access_key": "Zephyr access key", "zephyr_secret_key": "Zephyr secret key",
    "zephyr_account_id": "account id", "project_key": "project key",
}


def _run_tests(settings: JiraSettings, jira: bool, zephyr: bool) -> None:
    if jira:
        try:
            me = JiraClient(settings).myself()
            st.success(f"Jira: connected as {me.get('displayName') or me.get('name') or 'Jira user'}")
        except JiraError as exc:
            st.error(f"Jira{f' ({exc.status})' if exc.status else ''}: {exc}")
    if zephyr:
        try:
            st.success(ZephyrClient(settings).test())
        except JiraError as exc:
            st.error(f"Zephyr{f' ({exc.status})' if exc.status else ''}: {exc}")


def _app_form() -> None:
    saved = saved_app_settings()
    keep = "•••••• saved — leave blank to keep"
    with st.form("conn_app", border=False):
        url = st.text_input("Jira URL", value=saved.url, placeholder="https://yourcompany.atlassian.net")
        user = st.text_input("Email / username", value=saved.username,
                             help="Jira Cloud: your Atlassian email. Leave empty for a Data Center "
                                  "personal access token (Bearer auth).")
        token = st.text_input("Jira API token", type="password", placeholder=keep if saved.token else "",
                              help="Cloud: id.atlassian.com → Security → API tokens. "
                                   "Data Center: Profile → Personal Access Tokens.")
        verify = st.checkbox("Verify SSL certificates", value=saved.verify_ssl,
                             help="Untick only behind a corporate TLS proxy with a private certificate.")
        st.markdown("**Zephyr Squad** · optional")
        access = st.text_input("Zephyr access key", type="password", placeholder=keep if saved.zephyr_access_key else "")
        secret = st.text_input("Zephyr secret key", type="password", placeholder=keep if saved.zephyr_secret_key else "")
        account = st.text_input("Atlassian account id", value=saved.zephyr_account_id,
                                placeholder="e.g. 5b10a2844c20165700ede21g")
        project = st.text_input("Project key", value=saved.project_key, placeholder="e.g. ABC")
        save = st.form_submit_button("Save", type="primary", width="stretch")
        c1, c2 = st.columns(2)
        test_jira = c1.form_submit_button("Test Jira", width="stretch")
        test_zephyr = c2.form_submit_button("Test Zephyr", width="stretch")
    if not (save or test_jira or test_zephyr):
        return
    candidate = JiraSettings(url, user, token.strip() or saved.token, verify,
                             access.strip() or saved.zephyr_access_key, secret.strip() or saved.zephyr_secret_key,
                             account.strip(), project.strip().upper())
    if save:
        save_jira_settings(candidate)
    _run_tests(with_env(candidate), test_jira, test_zephyr)
    if save and not (test_jira or test_zephyr):
        st.rerun()


def _file_form(conn: Connection) -> None:
    ss = st.session_state
    ss.setdefault("conn_file_path", conn.file_path)
    path = st.text_input("Credentials file path", key="conn_file_path", placeholder="~/secure/jira_config.ini",
                         help="A config.ini / .cfg / .conf with a [zephyr] or [jira] section, or Amplify QEA's "
                              "config.json. It's read each time it's needed — only the path is stored here.")
    found, sections, settings, problem = (), (), None, ""
    if path.strip():
        try:
            settings, found, sections = read_credentials_file(path, ss.get("conn_file_section", ""))
        except (ValueError, OSError) as exc:
            problem = str(exc)
            if "Section [" in problem:                      # chosen section vanished → retry on auto
                ss["conn_file_section"] = ""
                try:
                    settings, found, sections = read_credentials_file(path)
                    problem = ""
                except (ValueError, OSError) as retry:
                    problem = str(retry)

    ss.setdefault("conn_file_section", conn.file_section if conn.file_section in sections else "")
    if ss["conn_file_section"] not in ("", *sections):
        ss["conn_file_section"] = ""
    section = st.selectbox("Section", ["", *sections], key="conn_file_section",
                           format_func=lambda s: f"[{s}]" if s else "Auto — [jira], then [zephyr], then others",
                           disabled=not sections)
    if problem:
        st.warning(problem)
    elif found:
        st.caption("✅ Found " + ", ".join(_LABELS[f] for f in found if f in _LABELS))

    use = st.button("Use this file", type="primary", width="stretch", disabled=settings is None)
    c1, c2 = st.columns(2)
    test_jira = c1.button("Test Jira", width="stretch", disabled=settings is None or not settings.configured)
    test_zephyr = c2.button("Test Zephyr", width="stretch", disabled=settings is None or not settings.zephyr_configured)
    if use:
        save_credentials_file(path, section)
        st.rerun()
    if settings is not None and (test_jira or test_zephyr):
        _run_tests(with_env(settings), test_jira, test_zephyr)
    with st.expander("Example config.ini"):
        st.code(INI_EXAMPLE, language="ini")


def render(conn: Connection) -> None:
    ss = st.session_state
    with st.expander("🔌 Jira / Zephyr config", expanded=not conn.settings.configured or bool(conn.error)):
        if conn.source == "file":
            st.caption(f"In use: **config file** `{conn.file_path}`"
                       + (f" · [{conn.file_section}]" if conn.file_section else ""))
        else:
            st.caption("In use: **values entered here**")
        if conn.error:
            st.error(conn.error)
        if conn.overrides:
            st.caption(f"Environment variables override: {', '.join(conn.overrides)}")
        ss.setdefault("conn_source", conn.source)
        mode = st.radio("Credentials from", list(_SOURCES), key="conn_source", horizontal=True,
                        format_func=_SOURCES.get)
        if mode == "file":
            _file_form(conn)
        else:
            _app_form()
