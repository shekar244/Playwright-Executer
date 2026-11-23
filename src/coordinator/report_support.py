"""Helpers for the generated Allure report: cache key, ETag matching, and removing Allure's built-in analytics.

Analytics: Allure 3 bakes Google Tag Manager into a single-file report.

Allure 3 hard-codes `analyticsEnable: true` in its report templates (no setting turns it off): every report
loads https://www.googletagmanager.com/gtag/js and reports the Allure version and the report id to Google.
For an internal tool that is unwanted tracking, and in an offline cluster it is a failing request, so the
coordinator strips it from every report it generates.

Pure functions, no database or web imports: unit-tested on their own.
"""
import hashlib
import logging
import os
import re
from pathlib import Path
from typing import List, Optional, Tuple

log = logging.getLogger("coordinator")

# <script async src="https://www.googletagmanager.com/gtag/js?id=G-XXXX"></script>
_SRC = re.compile(rb"""<script\b[^>]*\bsrc\s*=\s*["']https?://(?:www\.)?googletagmanager\.com/[^"']*["'][^>]*>\s*</script>\s*""", re.I)
# <script> window.dataLayer = window.dataLayer || []; function gtag(){...} gtag('js', ...); gtag('config', ...) </script>
_INLINE = re.compile(rb"""<script>\s*window\.dataLayer\s*=\s*window\.dataLayer\s*\|\|\s*\[\]\s*;.*?</script>\s*""", re.S)


def archive_key(archives: List[Tuple[str, Path, os.stat_result]]) -> str:
    """Cache key of a run: changes whenever an archive is added, replaced or removed."""
    parts = (f"{chunk}\0{st.st_size}\0{st.st_mtime_ns}\n" for chunk, _p, st in sorted(archives, key=lambda t: t[0]))
    return hashlib.sha256("".join(parts).encode()).hexdigest()


def etag_matches(header: Optional[str], key: str) -> bool:
    """True when an If-None-Match header lists our ETag (weak comparison)."""
    return f'"{key}"' in [t.strip().removeprefix("W/") for t in (header or "").split(",")]


def strip_analytics_bytes(data: bytes) -> bytes:
    return _INLINE.sub(b"", _SRC.sub(b"", data))


def strip_analytics(path: Path) -> bool:
    """Clean the report file in place. True if anything was removed. Logs a warning if a tracker is still
    referenced afterwards (a new Allure version may have changed its template)."""
    data = path.read_bytes()
    cleaned = strip_analytics_bytes(data)
    if cleaned != data:
        path.write_bytes(cleaned)
    if b"googletagmanager" in cleaned.lower():
        log.warning("Allure report %s still references googletagmanager after cleaning", path.name)
    return cleaned != data
