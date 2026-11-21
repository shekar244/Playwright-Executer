"""
Sprint-specific DataFrame enrichment — adds sprint metadata columns and
computes burndown / velocity metrics from the standard issues frame.

Sits between the raw JiraClient.search() results and the pivot/chart layer,
adding columns that the sprint starter reports depend on.
"""
from __future__ import annotations

import re

import pandas as pd

from .transform import MULTI_SEP, issues_to_frame

_GH_SPRINT = re.compile(r"name=([^,\]]+)")
_GH_SPRINT_ID = re.compile(r"id=(\d+)")

CARRIED_FORWARD_FROM = "Carried Forward From"


def carried_forward_from(sprints) -> str | None:
    """The sprints an issue was carried forward FROM, as a multi-value cell; None when it never was.

    Jira lists every sprint an issue has been in, oldest first. It was carried forward out of each of them
    except the last: it appears in a later sprint too. Counting an issue once under every sprint it left is
    what makes a sprint-by-sprint carry-forward count possible; an issue in a single sprint (or in none)
    was not carried anywhere yet, and the issue's last sprint is not counted because nothing follows it.
    """
    if sprints is None or (isinstance(sprints, float) and pd.isna(sprints)):
        return None
    names = list(dict.fromkeys(p.strip() for p in str(sprints).split(MULTI_SEP) if p.strip()))
    return MULTI_SEP.join(names[:-1]) if len(names) > 1 else None


def add_carry_forward(df: pd.DataFrame, multi_cols=()) -> tuple[pd.DataFrame, list[str]]:
    """(frame, multi-value columns) with "Carried Forward From" added when the issues have a Sprint column.

    Safe to call again (a frame that has the column is returned as it is), and it never changes the frame it is given.
    """
    multi = list(multi_cols)
    if "Sprint" not in df.columns or CARRIED_FORWARD_FROM in df.columns:
        return df, multi
    out = df.assign(**{CARRIED_FORWARD_FROM: df["Sprint"].map(carried_forward_from)})
    return out, multi + ([CARRIED_FORWARD_FROM] if CARRIED_FORWARD_FROM not in multi else [])


def _extract_sprint_name(value) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    s = str(value)
    if "com.atlassian.greenhopper" in s:
        match = _GH_SPRINT.search(s)
        return match.group(1) if match else None
    parts = [p.strip() for p in s.split(MULTI_SEP) if p.strip()]
    return parts[-1] if parts else None


def _assign_sprint_id(fields: dict, valid_ids: set[int]) -> int | None:
    """Find which of the valid sprint ids this issue belongs to.

    An issue can appear in multiple sprints (carry-over). We pick the one that
    is in our valid set; when multiple match, the highest id wins (most recent).
    """
    sprint_field = fields.get("sprint")
    if isinstance(sprint_field, dict) and sprint_field.get("id"):
        sid = int(sprint_field["id"])
        return sid if sid in valid_ids else None

    # The sprint field can be a list of Greenhopper sprint strings or dicts
    raw = sprint_field
    if not isinstance(raw, list):
        raw = [raw] if raw else []

    found: list[int] = []
    for entry in raw:
        if isinstance(entry, dict) and entry.get("id"):
            sid = int(entry["id"])
            if sid in valid_ids:
                found.append(sid)
        elif isinstance(entry, str) and "com.atlassian.greenhopper" in entry:
            match = _GH_SPRINT_ID.search(entry)
            if match:
                sid = int(match.group(1))
                if sid in valid_ids:
                    found.append(sid)
    return max(found) if found else None


def sprint_issues_to_frame(issues: list[dict], field_names: dict[str, str] | None = None,
                           sprint_meta: dict | None = None,
                           now: pd.Timestamp | None = None,
                           skip_fields: set[str] | None = None) -> tuple[pd.DataFrame, list[str]]:
    """Like issues_to_frame but enriches with sprint-derived columns."""
    df, multi = issues_to_frame(issues, field_names, now, skip_fields=skip_fields)
    if df.empty:
        return df, multi

    if "Sprint" in df.columns:
        df["Sprint Name"] = df["Sprint"].map(_extract_sprint_name)
    elif sprint_meta and sprint_meta.get("name"):
        df["Sprint Name"] = sprint_meta["name"]

    if sprint_meta:
        if sprint_meta.get("startDate"):
            df["Sprint Start"] = pd.to_datetime(sprint_meta["startDate"], utc=True, errors="coerce")
            if df["Sprint Start"].notna().any():
                df["Sprint Start"] = df["Sprint Start"].dt.tz_convert(None)
        if sprint_meta.get("endDate"):
            df["Sprint End"] = pd.to_datetime(sprint_meta["endDate"], utc=True, errors="coerce")
            if df["Sprint End"].notna().any():
                df["Sprint End"] = df["Sprint End"].dt.tz_convert(None)
        if sprint_meta.get("goal"):
            df["Sprint Goal"] = sprint_meta["goal"]

    if "Story Points" not in df.columns:
        for col in df.columns:
            if col.lower() in ("story points", "story point estimate"):
                df.rename(columns={col: "Story Points"}, inplace=True)
                break

    if "Story Points" in df.columns:
        df["Story Points"] = pd.to_numeric(df["Story Points"], errors="coerce")

    return add_carry_forward(df, multi)


def compute_velocity(df: pd.DataFrame) -> pd.DataFrame:
    """Per-sprint velocity: committed vs completed story points."""
    if "Sprint Name" not in df.columns or "Story Points" not in df.columns:
        return pd.DataFrame(columns=["Sprint", "Committed", "Completed"])

    groups = df.groupby("Sprint Name", sort=False)
    rows = []
    for name, group in groups:
        committed = group["Story Points"].sum()
        done = group.loc[
            group.get("Status Category", pd.Series(dtype=str)).str.lower() == "done",
            "Story Points",
        ].sum()
        rows.append({"Sprint": name, "Committed": committed, "Completed": done})
    return pd.DataFrame(rows)


def compute_burndown(df: pd.DataFrame, sprint_meta: dict | None = None) -> pd.DataFrame:
    """Daily burndown: remaining story points by day within the sprint window."""
    if "Story Points" not in df.columns:
        return pd.DataFrame(columns=["Date", "Ideal", "Remaining"])

    total_points = df["Story Points"].sum()
    if not total_points or pd.isna(total_points):
        return pd.DataFrame(columns=["Date", "Ideal", "Remaining"])

    start = end = None
    if sprint_meta:
        start = pd.to_datetime(sprint_meta.get("startDate"), utc=True, errors="coerce")
        end = pd.to_datetime(sprint_meta.get("endDate"), utc=True, errors="coerce")
    if "Sprint Start" in df.columns and df["Sprint Start"].notna().any():
        start = start or df["Sprint Start"].iloc[0]
    if "Sprint End" in df.columns and df["Sprint End"].notna().any():
        end = end or df["Sprint End"].iloc[0]

    if pd.isna(start) or pd.isna(end):
        return pd.DataFrame(columns=["Date", "Ideal", "Remaining"])

    start = pd.Timestamp(start).normalize().tz_localize(None)
    end = pd.Timestamp(end).normalize().tz_localize(None)
    days = pd.date_range(start, end, freq="D")
    if len(days) < 2:
        return pd.DataFrame(columns=["Date", "Ideal", "Remaining"])

    ideal_per_day = total_points / (len(days) - 1)

    resolved = df.loc[df["Resolved"].notna(), ["Resolved", "Story Points"]].copy() \
        if "Resolved" in df.columns else pd.DataFrame(columns=["Resolved", "Story Points"])
    if not resolved.empty:
        resolved["Resolved"] = pd.to_datetime(resolved["Resolved"], errors="coerce").dt.normalize()

    rows = []
    for i, day in enumerate(days):
        ideal = max(0, total_points - ideal_per_day * i)
        done_by_day = resolved.loc[resolved["Resolved"] <= day, "Story Points"].sum() if not resolved.empty else 0
        remaining = total_points - done_by_day
        rows.append({"Date": day, "Ideal": round(ideal, 1), "Remaining": round(remaining, 1)})
    return pd.DataFrame(rows)
