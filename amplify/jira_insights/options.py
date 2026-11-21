"""Admin-controlled visibility of Jira Insights options.

An Amplify QEA admin chooses which sidebar options and views Insights shows (Admin
Panel → Jira Insights Options). This module resolves that setting:

  * QEA_API_URL set  → fetched from QEA over HTTP. Needed when Insights runs on its own
                       disk (e.g. Render) and cannot read QEA's server_config.json.
  * otherwise        → read from server_config.json (local run, shared volume / PVC).

Results are cached for a few seconds, and if QEA is briefly unreachable the last good
value is kept, so a network blip never flips hidden options back on.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request

from amplify.server_secrets import INSIGHTS_OPTION_CATALOG, load_insights_options

CACHE_SECONDS = 15   # an Admin Panel change reaches users within about this long

VIEW_KEYS = {
    "Dashboard": "view_dashboard",
    "Sprint Reports": "view_sprint",
    "Report Builder": "view_builder",
    "Explorer": "view_explorer",
    "Data": "view_data",
}

_DEFAULTS = {e["key"]: e["default"] for e in INSIGHTS_OPTION_CATALOG}
_cache: dict = {"at": 0.0, "url": None, "value": None}


def _fetch_remote(base_url: str) -> dict | None:
    try:
        with urllib.request.urlopen(f"{base_url}/api/insights/options", timeout=3) as resp:
            data = json.loads(resp.read())
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return {k: data[k] if isinstance(data.get(k), bool) else d for k, d in _DEFAULTS.items()}


def get_options() -> dict:
    """Full {option_key: bool} map for the current moment."""
    url = os.environ.get("QEA_API_URL", "").rstrip("/")
    now = time.monotonic()
    fresh = _cache["value"] is not None and _cache["url"] == url and now - _cache["at"] < CACHE_SECONDS
    if fresh:
        return _cache["value"]

    value = _fetch_remote(url) if url else None
    if value is None and url and _cache["url"] == url:
        value = _cache["value"]                 # QEA unreachable: keep the last good answer
    if value is None:
        value = load_insights_options()         # local file, or defaults
    _cache.update(at=now, url=url, value=value)
    return value


def allowed_views(views: dict, opts: dict) -> list[str]:
    """View names an admin has left enabled, in their original order (never empty)."""
    shown = [v for v in views if opts.get(VIEW_KEYS.get(v, ""), True)]
    return shown or list(views)


def pick_view(requested: str | None, current: str | None, allowed: list[str]) -> str:
    """Navigate to `requested` if allowed, else stay on `current`, else the first allowed view."""
    for candidate in (requested, current):
        if candidate in allowed:
            return candidate
    return allowed[0]
