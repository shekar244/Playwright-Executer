"""
Flatten Jira issues into a pandas DataFrame ready for pivoting.

Two sources produce the same shape:
  issues_to_frame()    — raw issues from the REST API (JiraClient.search)
  frame_from_export()  — a Jira "Export → CSV / Excel" file

Every frame gets the same derived columns so saved reports work regardless
of the source:  Status Category · Open/Closed · Age (days) · Resolution Time (days)

Multi-valued fields (labels, components, sprints, versions …) are joined with
MULTI_SEP and reported back as `multi_cols` so the pivot engine can explode
them when grouping.
"""
from __future__ import annotations

import io
import re
from typing import IO

import pandas as pd

MULTI_SEP = ", "

# System fields get fixed names so starter reports match every Jira flavour.
SYSTEM_NAMES = {
    "summary": "Summary", "issuetype": "Issue Type", "status": "Status",
    "priority": "Priority", "assignee": "Assignee", "reporter": "Reporter",
    "creator": "Creator", "created": "Created", "updated": "Updated",
    "resolutiondate": "Resolved", "resolution": "Resolution", "labels": "Labels",
    "components": "Components", "fixVersions": "Fix Version/s",
    "versions": "Affects Version/s", "project": "Project", "duedate": "Due Date",
    "parent": "Parent", "statuscategorychangedate": "Status Category Changed",
}

# Rich-text / bulky fields that would bloat the frame without helping pivots.
# Default fields to skip — grouped by reason so the config UI can explain each.
SKIP_FIELD_GROUPS = {
    "Rich text": {"description", "environment"},
    "Comments & logs": {"comment", "worklog"},
    "Attachments": {"attachment", "thumbnail"},
    "Social": {"watches", "votes"},
    "Internal tracking": {"timetracking", "progress", "aggregateprogress"},
    "Metadata noise": {"lastViewed"},
}

# Always handled specially (extracted into structured columns, never togglable).
_ALWAYS_SPECIAL = {"issuelinks", "subtasks"}

# The default skip set — union of all groups + always-special.
DEFAULT_SKIP_FIELDS = _ALWAYS_SPECIAL | {f for group in SKIP_FIELD_GROUPS.values() for f in group}

# Module-level alias for backwards compatibility.
_SKIP_FIELDS = DEFAULT_SKIP_FIELDS

_LEADING_COLUMNS = [
    "Key", "Summary", "Issue Type", "Status", "Status Category", "Open/Closed",
    "Priority", "Assignee", "Reporter", "Epic Name", "Epic Key",
    "Created", "Updated", "Resolved",
    "Age (days)", "Resolution Time (days)",
]

# Known custom field names for the Epic Link field across Jira instances.
_EPIC_LINK_NAMES = {"epic link", "epic", "epic name"}
_EPIC_LINK_IDS = {"customfield_10008", "customfield_10014"}

# Used for Open/Closed when an export has neither Status Category nor Resolved.
_DONE_STATUSES = {"done", "closed", "resolved", "released", "complete", "completed",
                  "cancelled", "canceled", "won't do", "won't fix", "rejected"}

_GH_SPRINT = re.compile(r"name=([^,\]]+)")                     # Server/DC sprint strings
_DATE_LIKE = re.compile(r"^\s*(\d{4}-\d{2}-\d{2}|\d{1,2}/[A-Za-z]{3}/\d{2,4})")
_CUSTOM_FIELD = re.compile(r"^Custom field \((.+)\)$")


# ── Epic extraction ───────────────────────────────────────────────────────────

def _extract_epic(fields: dict, field_names: dict[str, str] | None = None) -> tuple[str | None, str | None]:
    """Extract (epic_name, epic_key) from an issue's fields.

    Sources checked in order:
      1. parent field (next-gen / team-managed projects — epic is the parent)
      2. Epic Link custom field (classic projects — customfield_10008 or similar)
      3. Any custom field whose display name matches 'Epic Link' or 'Epic Name'
    """
    # 1. Parent field: {"key": "PROJ-10", "fields": {"summary": "Epic title", "issuetype": {"name": "Epic"}}}
    parent = fields.get("parent")
    if isinstance(parent, dict):
        parent_fields = parent.get("fields") or {}
        parent_type = (parent_fields.get("issuetype") or {}).get("name", "")
        if parent_type.lower() == "epic":
            return parent_fields.get("summary"), parent.get("key")
        # Even if parent isn't an epic, return it as the parent context
        if parent.get("key"):
            return parent_fields.get("summary"), parent.get("key")

    # 2. Well-known Epic Link custom fields
    for fid in _EPIC_LINK_IDS:
        raw = fields.get(fid)
        if raw is not None:
            if isinstance(raw, str) and raw.strip():
                return raw, raw  # Cloud: epic key as a string
            if isinstance(raw, dict):
                return (raw.get("fields", {}).get("summary") or raw.get("name") or raw.get("key"),
                        raw.get("key"))

    # 3. Search by display name
    names = field_names or {}
    for fid, display in names.items():
        if display.lower() in _EPIC_LINK_NAMES and fid.startswith("customfield_"):
            raw = fields.get(fid)
            if raw is not None:
                if isinstance(raw, str) and raw.strip():
                    return raw, raw
                if isinstance(raw, dict):
                    return (raw.get("fields", {}).get("summary") or raw.get("name") or raw.get("key"),
                            raw.get("key"))

    return None, None


# ── Issue link extraction ────────────────────────────────────────────────────

def _extract_links(fields: dict) -> tuple[str | None, str | None, int]:
    """Extract (link_types, linked_keys, link_count) from issuelinks.

    link_types: comma-separated link relationship names (e.g. "blocks, is blocked by")
    linked_keys: comma-separated keys of linked issues (e.g. "PROJ-1, PROJ-2")
    """
    links = fields.get("issuelinks")
    if not isinstance(links, list) or not links:
        return None, None, 0

    types: list[str] = []
    keys: list[str] = []
    for link in links:
        link_type = link.get("type") or {}
        if "outwardIssue" in link:
            rel = link_type.get("outward", link_type.get("name", "links to"))
            key = (link["outwardIssue"] or {}).get("key", "")
        elif "inwardIssue" in link:
            rel = link_type.get("inward", link_type.get("name", "linked from"))
            key = (link["inwardIssue"] or {}).get("key", "")
        else:
            continue
        if rel and rel not in types:
            types.append(rel)
        if key:
            keys.append(key)

    return (MULTI_SEP.join(types) if types else None,
            MULTI_SEP.join(keys) if keys else None,
            len(links))


# ── REST issues ───────────────────────────────────────────────────────────────

def flatten_value(value):
    """Reduce a Jira field value to a scalar suitable for grouping."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if "com.atlassian.greenhopper" in value:
            match = _GH_SPRINT.search(value)
            return match.group(1) if match else value
        return value
    if isinstance(value, dict):
        if value.get("type") == "doc":                         # Atlassian Document Format
            return None
        for key in ("displayName", "name", "value", "key"):
            if value.get(key) not in (None, ""):
                child = value.get("child")                     # cascading select
                if isinstance(child, dict) and child.get("value"):
                    return f"{value[key]} / {child['value']}"
                return value[key]
        return None
    if isinstance(value, list):
        parts = [str(p) for p in (flatten_value(v) for v in value) if p not in (None, "")]
        return MULTI_SEP.join(parts) if parts else None
    return str(value)


def _column_names(field_ids: list[str], field_names: dict[str, str]) -> dict[str, str]:
    taken = set(_LEADING_COLUMNS)
    out: dict[str, str] = {}
    for fid in field_ids:
        name = SYSTEM_NAMES.get(fid) or field_names.get(fid) or fid
        if name in taken and SYSTEM_NAMES.get(fid) != name:
            name = f"{name} ({fid})"
        taken.add(name)
        out[fid] = name
    return out


def issues_to_frame(issues: list[dict], field_names: dict[str, str] | None = None,
                    now: pd.Timestamp | None = None,
                    skip_fields: set[str] | None = None) -> tuple[pd.DataFrame, list[str]]:
    skip = (skip_fields | _ALWAYS_SPECIAL) if skip_fields is not None else _SKIP_FIELDS
    field_ids: dict[str, None] = {}
    for issue in issues:
        for fid in (issue.get("fields") or {}):
            if fid not in skip:
                field_ids.setdefault(fid)
    names = _column_names(list(field_ids), field_names or {})

    rows: list[dict] = []
    multi: set[str] = set()
    for issue in issues:
        fields = issue.get("fields") or {}
        status = fields.get("status") or {}
        row = {"Key": issue.get("key"),
               "Status Category": (status.get("statusCategory") or {}).get("name")}
        if "subtasks" in fields:
            row["Sub-tasks"] = len(fields.get("subtasks") or [])

        # Epic name and key
        epic_name, epic_key = _extract_epic(fields, field_names)
        if epic_name:
            row["Epic Name"] = epic_name
        if epic_key:
            row["Epic Key"] = epic_key

        # Issue links — structured columns
        link_types, linked_keys, link_count = _extract_links(fields)
        row["Linked Issues"] = link_count
        if link_types:
            row["Link Type"] = link_types
            multi.add("Link Type")
        if linked_keys:
            row["Linked Keys"] = linked_keys
            multi.add("Linked Keys")

        for fid, name in names.items():
            raw = fields.get(fid)
            row[name] = flatten_value(raw)
            if isinstance(raw, list) and row[name] is not None:
                multi.add(name)
        rows.append(row)

    df = pd.DataFrame(rows).dropna(axis=1, how="all")
    return finalize(df, now=now), sorted(c for c in multi if c in df.columns)


# ── CSV / Excel exports ───────────────────────────────────────────────────────

def frame_from_export(file: IO[bytes] | bytes, filename: str,
                      now: pd.Timestamp | None = None) -> tuple[pd.DataFrame, list[str]]:
    """Jira exports repeat a header once per value (Labels, Labels, …) — merge them."""
    data = file if isinstance(file, bytes) else file.read()
    if filename.lower().endswith((".xlsx", ".xls")):
        raw = pd.read_excel(io.BytesIO(data), header=None, dtype=str)
    else:
        raw = pd.read_csv(io.BytesIO(data), header=None, dtype=str, keep_default_na=False,
                          encoding_errors="replace")
    if raw.empty:
        return pd.DataFrame(), []

    headers = [str(h).strip() for h in raw.iloc[0]]
    body = raw.iloc[1:].reset_index(drop=True)
    columns: dict[str, pd.Series] = {}
    multi: list[str] = []
    for name in dict.fromkeys(headers):
        name_out = _CUSTOM_FIELD.sub(r"\1", name)
        name_out = "Key" if name_out.lower() == "issue key" else name_out
        idx = [i for i, h in enumerate(headers) if h == name]
        block = body.iloc[:, idx].replace("", None)
        if len(idx) > 1:
            multi.append(name_out)
            merged = block.apply(lambda r: MULTI_SEP.join(v for v in r if isinstance(v, str) and v), axis=1)
            columns[name_out] = merged.replace("", None)
        else:
            columns[name_out] = block.iloc[:, 0]

    df = pd.DataFrame(columns).dropna(axis=1, how="all")
    return finalize(df, now=now), [c for c in multi if c in df.columns]


# ── Shared normalisation ──────────────────────────────────────────────────────

def _looks_like_dates(s: pd.Series) -> bool:
    sample = s.dropna().astype(str).head(25)
    return len(sample) > 0 and sample.str.match(_DATE_LIKE).mean() >= 0.9


def normalise_types(df: pd.DataFrame) -> pd.DataFrame:
    for col in df.columns:
        s = df[col]
        if pd.api.types.is_bool_dtype(s) or pd.api.types.is_numeric_dtype(s) \
                or pd.api.types.is_datetime64_any_dtype(s):
            continue
        non_null = s.dropna()
        if col != "Key" and len(non_null) and _looks_like_dates(non_null):
            parsed = pd.to_datetime(s, format="mixed", utc=True, errors="coerce")
            if parsed.notna().sum() >= 0.9 * len(non_null):
                df[col] = parsed.dt.tz_convert(None)
                continue
        numeric = pd.to_numeric(non_null, errors="coerce")
        if col != "Key" and len(non_null) and numeric.notna().all() \
                and not non_null.map(lambda v: isinstance(v, bool)).any():
            df[col] = pd.to_numeric(s, errors="coerce")
        else:
            # Mixed scalars (e.g. ints and strings) must be strings for Parquet.
            df[col] = s.map(lambda v: v if v is None or (isinstance(v, float) and pd.isna(v)) else str(v))
    return df


def finalize(df: pd.DataFrame, now: pd.Timestamp | None = None) -> pd.DataFrame:
    if df.empty:
        return df
    df = normalise_types(df.copy())
    now = now if now is not None else pd.Timestamp.now(tz="UTC").tz_convert(None)

    resolved = df["Resolved"] if "Resolved" in df.columns else pd.Series(pd.NaT, index=df.index)
    if "Status Category" in df.columns and df["Status Category"].notna().any():
        df["Open/Closed"] = df["Status Category"].map(
            lambda v: None if v is None or pd.isna(v) else ("Closed" if v == "Done" else "Open"))
    else:
        closed = resolved.notna()
        if "Status" in df.columns:
            closed |= df["Status"].fillna("").astype(str).str.strip().str.lower().isin(_DONE_STATUSES)
        df["Open/Closed"] = closed.map({True: "Closed", False: "Open"})

    if "Created" in df.columns and pd.api.types.is_datetime64_any_dtype(df["Created"]):
        end = resolved.fillna(now) if pd.api.types.is_datetime64_any_dtype(resolved) else now
        df["Age (days)"] = ((end - df["Created"]).dt.total_seconds() / 86400).round(1)
        if pd.api.types.is_datetime64_any_dtype(resolved):
            df["Resolution Time (days)"] = ((resolved - df["Created"]).dt.total_seconds() / 86400).round(1)

    lead = [c for c in _LEADING_COLUMNS if c in df.columns]
    return df[lead + [c for c in df.columns if c not in lead]]
