"""
Zephyr test executions → a flat DataFrame for execution reports.

Accepts both payload shapes:
  Cloud   searchObjectList items: {"execution": {...status, cycleName, executedOn…},
                                   "issueKey", "issueSummary", "component", "versionName", …}
  Server  ZQL executeSearch items: {status, cycleName, issueKey, executedOn, executedBy…}

Columns: Execution ID · Key · Summary · Execution Status · Result · Executed ·
Cycle · Folder · Version · Project · Executed By · Executed On · Assignee ·
Priority · Labels · Components · Defects · Defect Keys · Comment
"""
from __future__ import annotations

import pandas as pd

from .transform import MULTI_SEP, normalise_types

# Zephyr's built-in status ids (Amplify QEA STATUS_MAP) for payloads that only carry an id.
STATUS_NAMES = {1: "Pass", 2: "Fail", 3: "WIP", 4: "Blocked", -1: "Unexecuted"}
NOT_EXECUTED = {"unexecuted", "not executed", "not run", "no run"}
RESULT_ORDER = ("Passed", "Failed", "Blocked", "In progress", "Not run", "Other")
EXECUTION_COLUMNS = ("Execution ID", "Key", "Summary", "Execution Status", "Result", "Executed", "Cycle",
                     "Folder", "Version", "Project", "Executed By", "Executed On", "Assignee", "Priority",
                     "Labels", "Components", "Defects", "Defect Keys", "Comment")
MULTI_COLUMNS = ("Labels", "Components", "Defect Keys")


def _first(*sources_and_keys):
    sources, keys = sources_and_keys[0], sources_and_keys[1:]
    for key in keys:
        for src in sources:
            value = src.get(key) if isinstance(src, dict) else None
            if value not in (None, "", [], {}):
                return value
    return None


def _names(value) -> str | None:
    """List of dicts/strings or a comma string → 'a, b'."""
    if value in (None, "", []):
        return None
    if isinstance(value, str):
        parts = [p.strip() for p in value.split(",")]
    elif isinstance(value, list):
        parts = [str(v.get("name") or v.get("key") or v.get("value") or "") if isinstance(v, dict) else str(v)
                 for v in value]
    elif isinstance(value, dict):
        parts = [str(value.get("name") or value.get("key") or "")]
    else:
        parts = [str(value)]
    parts = [p for p in parts if p]
    return MULTI_SEP.join(parts) if parts else None


def status_label(status) -> str:
    """{"name": "PASS"} / "WIP" / 1 → "Pass" / "WIP" / "Pass" — title case like Amplify, but a
    single short acronym (WIP, N/A) is kept as is."""
    if isinstance(status, dict):
        raw = status.get("name")
        sid = str(status.get("id", ""))
        if not raw and sid.lstrip("-").isdigit():
            raw = STATUS_NAMES.get(int(sid), sid)
    elif isinstance(status, (int, float)) or (isinstance(status, str) and status.lstrip("-").isdigit()):
        raw = STATUS_NAMES.get(int(status), str(status))
    else:
        raw = status
    if not raw:
        return "Unexecuted"
    words = str(raw).replace("_", " ").split()
    if len(words) == 1 and len(words[0]) <= 3 and words[0].isupper():
        return words[0]
    return " ".join(w.capitalize() for w in words)


def result_group(status: str) -> str:
    s = status.lower()
    if s in NOT_EXECUTED or "unexecuted" in s or "not run" in s:
        return "Not run"
    if s.startswith("pass"):
        return "Passed"
    if s.startswith("fail"):
        return "Failed"
    if "block" in s:
        return "Blocked"
    if s in ("wip", "in progress", "in-progress", "retest") or "progress" in s:
        return "In progress"
    return "Other"


def _executed_on(value):
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
        return pd.to_datetime(int(value), unit="ms", utc=True).tz_convert(None)   # epoch ms (Cloud)
    return pd.to_datetime(str(value), format="mixed", utc=True, errors="coerce")


def executions_to_frame(records: list[dict]) -> tuple[pd.DataFrame, list[str]]:
    rows = []
    for item in records:
        if not isinstance(item, dict):
            continue
        ex = item.get("execution") if isinstance(item.get("execution"), dict) else item
        src = (ex, item)
        defects = _first(src, "defects", "executionDefects")
        defect_keys = _names([d.get("key") if isinstance(d, dict) else d for d in defects]) \
            if isinstance(defects, list) else None
        count = _first(src, "totalDefectCount", "executionDefectCount")
        status = status_label(_first(src, "status", "executionStatus", "executionStatusName"))
        cycle = _first(src, "cycleName")
        if cycle is None and str(_first(src, "cycleId")) == "-1":
            cycle = "Ad hoc"
        executed_on = _executed_on(_first(src, "executedOn", "executionDate", "executedOnStr"))
        rows.append({
            "Execution ID": _first(src, "id", "executionId"),
            "Key": _first(src, "issueKey"),
            "Summary": _first(src, "issueSummary", "summary"),
            "Execution Status": status,
            "Result": result_group(status),
            "Executed": "No" if result_group(status) == "Not run" else "Yes",
            "Cycle": cycle,
            "Folder": _first(src, "folderName"),
            "Version": _first(src, "versionName") or ("Unscheduled" if str(_first(src, "versionId")) == "-1" else None),
            "Project": _first(src, "projectKey"),
            "Executed By": _first(src, "executedByDisplay", "executedByUserName", "executedByDisplayName",
                                  "executedBy", "executedByAccountId"),
            "Executed On": executed_on,
            "Assignee": _first(src, "assignedToDisplay", "assigneeUserName", "assigneeDisplayName",
                               "assignedTo", "assigneeAccountId"),
            "Priority": _names(_first(src, "priority")),
            "Labels": _names(_first(src, "labels", "issueLabel")),
            "Components": _names(_first(src, "components", "component")),
            "Defects": int(count) if str(count or "").isdigit() else (len(defects) if isinstance(defects, list) else 0),
            "Defect Keys": defect_keys,
            "Comment": _first(src, "comment"),
        })
    if not rows:
        return pd.DataFrame(columns=list(EXECUTION_COLUMNS)), []
    df = pd.DataFrame(rows, columns=list(EXECUTION_COLUMNS))
    df["Executed On"] = pd.to_datetime(df["Executed On"], utc=True, errors="coerce").dt.tz_convert(None)
    df = df.dropna(axis=1, how="all")
    df = normalise_types(df)
    return df, [c for c in MULTI_COLUMNS if c in df.columns]
