"""report_support: the analytics stripper (and a guard that real Allure reports come out clean)."""
from pathlib import Path

import pytest

from report_support import strip_analytics, strip_analytics_bytes

GTAG = b"""<script async src="https://www.googletagmanager.com/gtag/js?id=G-LNDJ3J7WT0"></script>
    <script>
        window.dataLayer = window.dataLayer || [];
        function gtag(){dataLayer.push(arguments);}
        gtag('js', new Date());
        gtag('config', 'G-LNDJ3J7WT0', {"report": "awesome", "allureVersion": "3.16.0", "reportUuid": "abc-123"});
    </script>
"""
APP = b"<script>window.app = {tests: ['test_login_works']};</script>\n<script src=\"data:text/javascript;base64,AAAA\"></script>\n"


def page(*parts: bytes) -> bytes:
    return b"<!doctype html><html><head><title>Allure</title>\n" + b"".join(parts) + b"</head><body>report</body></html>"


def test_both_google_tag_manager_scripts_are_removed_and_nothing_else():
    cleaned = strip_analytics_bytes(page(APP, GTAG))
    assert b"googletagmanager" not in cleaned and b"gtag(" not in cleaned and b"dataLayer" not in cleaned
    assert b"G-LNDJ3J7WT0" not in cleaned and b"abc-123" not in cleaned
    assert APP in cleaned and cleaned.startswith(b"<!doctype html>") and cleaned.endswith(b"</body></html>")


@pytest.mark.parametrize("tag", [
    b"<script async src='https://googletagmanager.com/gtag/js?id=G-1'></script>",
    b'<SCRIPT ASYNC SRC="http://www.googletagmanager.com/gtag/js?id=G-1" ></SCRIPT>',
    b'<script src="https://www.googletagmanager.com/gtm.js?id=GTM-1"> </script>',
])
def test_the_loader_tag_is_found_however_it_is_written(tag):
    assert b"googletagmanager" not in strip_analytics_bytes(page(APP, tag)).lower()


def test_a_report_with_no_analytics_is_returned_untouched():
    original = page(APP)
    assert strip_analytics_bytes(original) == original


def test_it_does_not_eat_scripts_that_merely_follow_the_tracker():
    cleaned = strip_analytics_bytes(page(GTAG, APP, b"<script>window.after = 1;</script>"))
    assert b"window.app" in cleaned and b"window.after = 1;" in cleaned


def test_cleaning_a_file_in_place_reports_whether_anything_changed(tmp_path: Path):
    f = tmp_path / "report.html"
    f.write_bytes(page(APP, GTAG))
    assert strip_analytics(f) is True and b"googletagmanager" not in f.read_bytes()
    assert strip_analytics(f) is False                                    # idempotent


def test_a_tracker_it_cannot_recognise_is_logged_not_silently_kept(tmp_path: Path, caplog):
    f = tmp_path / "report.html"
    f.write_bytes(page(b'<link rel="preconnect" href="https://www.googletagmanager.com">'))
    with caplog.at_level("WARNING", logger="coordinator"):
        strip_analytics(f)
    assert "still references googletagmanager" in caplog.text
