"""
Edge Launcher -- AI Studio dual-tab workspace

Opens two new tabs in Microsoft Edge (works even if Edge is already running):
  Tab 1 -> Atlassian Rovo chat  ({jira_url}/rovo)
  Tab 2 -> Amplify QEA AI Studio  (http://amplify-qea:7777)

No Playwright required. Uses the OS native launcher so Edge's existing
SSO session and profile are reused automatically.

Usage:
  python edge_launcher.py [--studio-only] [--rovo-only]
"""
from __future__ import annotations

import json
import platform
import subprocess
import sys
from pathlib import Path

AMPLIFY_URL = "http://amplify-qea:7777"


def _load_jira_url() -> str:
    cfg_path = Path(__file__).parent / "config.json"
    if cfg_path.exists():
        try:
            with open(cfg_path, encoding="utf-8") as fh:
                cfg = json.load(fh)
            # Top-level jira_url takes priority; fall back to zephyr.jira_url
            url = (
                cfg.get("jira_url", "")
                or cfg.get("zephyr", {}).get("jira_url", "")
            )
            return url.strip().rstrip("/")
        except Exception:
            pass
    return ""


def _open_in_edge(url: str) -> None:
    """Open a URL as a new tab in running Edge. Uses subprocess.run so the
    launcher process exits cleanly without leaving zombie Popen objects."""
    system = platform.system()
    try:
        if system == "Darwin":
            # `open` hands off to Edge quickly then exits; 8s is generous
            subprocess.run(
                ["open", "-a", "Microsoft Edge", url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=8,
                check=False,
            )
        elif system == "Windows":
            subprocess.run(
                ["cmd", "/c", "start", "msedge", url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=8,
                check=False,
            )
        else:
            subprocess.run(
                ["microsoft-edge", url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=8,
                check=False,
            )
    except FileNotFoundError as exc:
        print(f"ERROR: Could not open Edge -- {exc}")
        raise
    except subprocess.TimeoutExpired:
        pass  # Edge launched; `open` just held the pipe briefly


def main(studio_only: bool = False, rovo_only: bool = False) -> None:
    jira_url = _load_jira_url()
    rovo_url = (jira_url + "/rovo") if jira_url else None
    studio_url = AMPLIFY_URL

    if not studio_only and rovo_url:
        print(f"Opening Rovo  : {rovo_url}")
        _open_in_edge(rovo_url)

    if not rovo_only:
        print(f"Opening Studio: {studio_url}")
        _open_in_edge(studio_url)

    if not studio_only and not rovo_url:
        print("WARNING: jira_url not configured -- Rovo tab skipped.")
        print("Set it in Config -> Test Management -> Jira URL, then retry.")


if __name__ == "__main__":
    args = sys.argv[1:]
    main(
        studio_only="--studio-only" in args,
        rovo_only="--rovo-only" in args,
    )
