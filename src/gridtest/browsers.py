"""Browser selection. The grid runs one image per browser; BROWSER picks the engine."""
import os
from typing import Any, Dict, Optional, Tuple

# browser name -> (playwright engine attribute, channel or None)
BROWSER_LAUNCH: Dict[str, Tuple[str, Optional[str]]] = {
    "edge": ("chromium", "msedge"),      # Microsoft Edge
    "chrome": ("chromium", "chrome"),    # Google Chrome
    "chromium": ("chromium", None),      # Playwright's bundled Chromium
    "firefox": ("firefox", None),        # Playwright's bundled Firefox
    "webkit": ("webkit", None),          # Playwright's bundled WebKit
}

# Container-safe flags; only Chromium-family browsers understand them.
CHROMIUM_ARGS = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]


def current_browser() -> str:
    name = os.getenv("BROWSER", "edge").lower()
    if name not in BROWSER_LAUNCH:
        raise ValueError(f"Unsupported BROWSER '{name}'. Choose one of: {', '.join(BROWSER_LAUNCH)}")
    return name


def launch(playwright: Any, name: Optional[str] = None, headless: bool = True):
    """Launch the configured browser from an active sync_playwright() instance."""
    engine_name, channel = BROWSER_LAUNCH[name or current_browser()]
    kwargs: Dict[str, Any] = {"headless": headless}
    if channel:
        kwargs["channel"] = channel
    if engine_name == "chromium":
        kwargs["args"] = CHROMIUM_ARGS
    return getattr(playwright, engine_name).launch(**kwargs)
