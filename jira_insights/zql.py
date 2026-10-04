"""
ZQL helpers — the 🧪 panel's project → version → cycle(s) selection is turned into
one Zephyr Query Language search just before fetching, and the test runs come from
the paged ZQL endpoint. That replaces one executions call per cycle, which tripped
Zephyr's rate limits on large versions.

  project = "ABC" AND fixVersion = "Release 3.2" AND cycleName IN ("Sprint 41", "Sprint 42")
"""
from __future__ import annotations


def quote(value: str) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def drilldown_zql(project_key: str, version_name: str = "", cycle_names=()) -> str:
    """No cycles → every cycle in the version; no version → the whole project."""
    parts = [f"project = {quote(project_key)}"]
    if version_name:
        parts.append(f"fixVersion = {quote(version_name)}")
    cycles = [c for c in dict.fromkeys(cycle_names) if c]
    if len(cycles) == 1:
        parts.append(f"cycleName = {quote(cycles[0])}")
    elif cycles:
        parts.append("cycleName IN (" + ", ".join(quote(c) for c in cycles) + ")")
    return " AND ".join(parts)
