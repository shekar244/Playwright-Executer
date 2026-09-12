"""
Blueprint: Atlassian OAuth 2.0 (3LO) + API proxy
  GET  /auth/atlassian              — start OAuth flow (redirect to Atlassian)
  GET  /auth/atlassian/callback     — OAuth callback, exchange code → token
  POST /auth/atlassian/disconnect   — clear stored token
  GET  /api/atlassian/status        — auth status + user identity
  GET  /api/atlassian/sites         — accessible Atlassian sites (cloudId list)
  GET  /api/atlassian/jira/search   — JQL search (?jql=&cloudId=)
  GET  /api/atlassian/jira/issue/<key> — single issue (?cloudId=)
  GET  /api/atlassian/rovo/probe    — probe for Rovo API availability
"""
from __future__ import annotations

import json
import secrets
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from flask import Blueprint, jsonify, redirect, request, session, url_for

from ui_launcher.config_reader import ConfigReader, TOOL_CONFIG_FILE

bp = Blueprint("atlassian", __name__)

# ── Constants ──────────────────────────────────────────────────────────────────

AUTH_URL    = "https://auth.atlassian.com/authorize"
TOKEN_URL   = "https://auth.atlassian.com/oauth/token"
API_BASE    = "https://api.atlassian.com"
JIRA_SCOPES = "read:me read:jira-user read:jira-work offline_access"
TOKEN_FILE  = TOOL_CONFIG_FILE.parent / "atlassian_token.json"


# ── Token helpers ──────────────────────────────────────────────────────────────

def _load_token() -> dict:
    if TOKEN_FILE.exists():
        try:
            return json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_token(data: dict) -> None:
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _clear_token() -> None:
    if TOKEN_FILE.exists():
        TOKEN_FILE.unlink()


def _cfg() -> dict:
    return ConfigReader().load()


def _credentials() -> tuple[str, str]:
    cfg = _cfg()
    return cfg.get("atlassian_client_id", "").strip(), cfg.get("atlassian_client_secret", "").strip()


# ── HTTP helpers ───────────────────────────────────────────────────────────────

def _http_get(url: str, token: str, params: dict | None = None) -> Any:
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _http_post(url: str, data: dict, token: str | None = None) -> Any:
    body = json.dumps(data).encode("utf-8")
    req  = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _refresh_token(tok: dict) -> dict | None:
    """Use the refresh_token to get a new access_token. Returns updated token dict or None."""
    client_id, client_secret = _credentials()
    if not client_id or not client_secret or not tok.get("refresh_token"):
        return None
    try:
        result = _http_post(TOKEN_URL, {
            "grant_type":    "refresh_token",
            "client_id":     client_id,
            "client_secret": client_secret,
            "refresh_token": tok["refresh_token"],
        })
        updated = {**tok, **result}
        _save_token(updated)
        return updated
    except Exception:
        return None


def _get_valid_token() -> tuple[str | None, str | None]:
    """Return (access_token, error_message). Refreshes if needed."""
    tok = _load_token()
    if not tok:
        return None, "not_connected"
    access = tok.get("access_token")
    if not access:
        return None, "no_access_token"
    # Try a cheap probe; refresh if 401
    try:
        _http_get(f"{API_BASE}/me", access)
        return access, None
    except urllib.error.HTTPError as e:
        if e.code == 401:
            refreshed = _refresh_token(tok)
            if refreshed:
                return refreshed.get("access_token"), None
            return None, "token_expired"
        return None, f"http_{e.code}"
    except Exception as exc:
        return None, str(exc)


# ── OAuth routes ───────────────────────────────────────────────────────────────

@bp.route("/auth/atlassian")
def atlassian_auth():
    client_id, _ = _credentials()
    if not client_id:
        return ("Atlassian Client ID not configured. "
                "Go to Config → 🔗 Atlassian and enter your OAuth app credentials.", 400)

    state = secrets.token_urlsafe(16)
    session["atlassian_oauth_state"] = state

    callback_url = url_for("atlassian.atlassian_callback", _external=True)

    params = urllib.parse.urlencode({
        "audience":      "api.atlassian.com",
        "client_id":     client_id,
        "scope":         JIRA_SCOPES,
        "redirect_uri":  callback_url,
        "state":         state,
        "response_type": "code",
        "prompt":        "consent",
    })
    return redirect(f"{AUTH_URL}?{params}")


@bp.route("/auth/atlassian/callback")
def atlassian_callback():
    error = request.args.get("error")
    if error:
        desc = request.args.get("error_description", error)
        return f"<h3>Atlassian OAuth error</h3><p>{desc}</p><a href='/'>Back</a>", 400

    code  = request.args.get("code", "")
    state = request.args.get("state", "")

    if state != session.pop("atlassian_oauth_state", None):
        return "<h3>Invalid state — possible CSRF</h3><a href='/'>Back</a>", 400

    client_id, client_secret = _credentials()
    callback_url = url_for("atlassian.atlassian_callback", _external=True)

    try:
        tok = _http_post(TOKEN_URL, {
            "grant_type":    "authorization_code",
            "client_id":     client_id,
            "client_secret": client_secret,
            "code":          code,
            "redirect_uri":  callback_url,
        })
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return f"<h3>Token exchange failed ({e.code})</h3><pre>{body}</pre><a href='/'>Back</a>", 400
    except Exception as exc:
        return f"<h3>Token exchange error</h3><p>{exc}</p><a href='/'>Back</a>", 500

    _save_token(tok)
    # Redirect back to app with studio tab open
    return redirect("/?tab=tools&cfg=atlassian")


@bp.route("/auth/atlassian/disconnect", methods=["POST"])
def atlassian_disconnect():
    _clear_token()
    return jsonify({"ok": True})


# ── Status / identity ──────────────────────────────────────────────────────────

@bp.route("/api/atlassian/status")
def atlassian_status():
    tok = _load_token()
    if not tok:
        return jsonify({"connected": False})

    access, err = _get_valid_token()
    if not access:
        return jsonify({"connected": False, "error": err})

    try:
        me = _http_get(f"{API_BASE}/me", access)
        return jsonify({
            "connected":    True,
            "account_id":   me.get("account_id"),
            "display_name": me.get("name") or me.get("display_name"),
            "email":        me.get("email"),
            "picture":      me.get("picture"),
        })
    except Exception as exc:
        return jsonify({"connected": True, "identity_error": str(exc)})


# ── Accessible sites (cloudId) ─────────────────────────────────────────────────

@bp.route("/api/atlassian/sites")
def atlassian_sites():
    access, err = _get_valid_token()
    if not access:
        return jsonify({"error": err}), 401
    try:
        sites = _http_get(f"{API_BASE}/oauth/token/accessible-resources", access)
        return jsonify({"sites": sites})
    except urllib.error.HTTPError as e:
        return jsonify({"error": f"HTTP {e.code}"}), e.code
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── Jira search ────────────────────────────────────────────────────────────────

@bp.route("/api/atlassian/jira/search")
def jira_search():
    access, err = _get_valid_token()
    if not access:
        return jsonify({"error": err}), 401

    cloud_id = request.args.get("cloudId", "").strip()
    jql      = request.args.get("jql", "").strip()
    max_res  = request.args.get("maxResults", "10")

    if not cloud_id:
        return jsonify({"error": "cloudId required"}), 400

    try:
        result = _http_get(
            f"{API_BASE}/ex/jira/{cloud_id}/rest/api/3/issue/search",
            access,
            params={"jql": jql, "maxResults": max_res,
                    "fields": "summary,status,priority,assignee,description"},
        )
        return jsonify(result)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return jsonify({"error": f"HTTP {e.code}", "detail": body}), e.code
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── Jira single issue ──────────────────────────────────────────────────────────

@bp.route("/api/atlassian/jira/issue/<key>")
def jira_issue(key: str):
    access, err = _get_valid_token()
    if not access:
        return jsonify({"error": err}), 401

    cloud_id = request.args.get("cloudId", "").strip()
    if not cloud_id:
        return jsonify({"error": "cloudId required"}), 400

    try:
        issue = _http_get(
            f"{API_BASE}/ex/jira/{cloud_id}/rest/api/3/issue/{key}",
            access,
            params={"fields": "summary,status,priority,assignee,description,comment"},
        )
        return jsonify(issue)
    except urllib.error.HTTPError as e:
        return jsonify({"error": f"HTTP {e.code}"}), e.code
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── Rovo probe ─────────────────────────────────────────────────────────────────

@bp.route("/api/atlassian/rovo/probe")
def rovo_probe():
    """
    Probe known Atlassian AI / Rovo endpoints to discover what's available
    on this account. Returns a list of probed URLs and their HTTP status.
    """
    access, err = _get_valid_token()
    if not access:
        return jsonify({"error": err}), 401

    probes = [
        f"{API_BASE}/rovo/v1/agents",
        f"{API_BASE}/rovo/v1/conversations",
        f"{API_BASE}/intelligence/v1/agents",
        f"{API_BASE}/ai/v1/agents",
    ]
    results = []
    for url in probes:
        try:
            data = _http_get(url, access)
            results.append({"url": url, "status": 200, "data": data})
        except urllib.error.HTTPError as e:
            results.append({"url": url, "status": e.code})
        except Exception as exc:
            results.append({"url": url, "status": "error", "detail": str(exc)})

    available = [r for r in results if r["status"] == 200]
    return jsonify({
        "probed":    results,
        "available": available,
        "rovo_api_found": bool(available),
    })
