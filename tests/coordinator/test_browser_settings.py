"""Which browsers the coordinator accepts, offers and defaults to (config.browser_settings).

A run for a browser that has no runner image waits in the queue forever, so a deployment can limit the list; with
nothing set it is the five browsers with edge as the default, as it always was.
"""
import pytest

from coordinator.config import KNOWN_BROWSERS, browser_settings


def test_with_nothing_set_it_is_every_browser_and_edge_as_ever():
    assert browser_settings({}) == (KNOWN_BROWSERS, "edge")
    assert browser_settings({"SUPPORTED_BROWSERS": "", "DEFAULT_BROWSER": ""}) == (KNOWN_BROWSERS, "edge")


def test_a_deployment_can_limit_the_browsers_and_pick_the_default():
    assert browser_settings({"SUPPORTED_BROWSERS": "chromium", "DEFAULT_BROWSER": "chromium"}) == (("chromium",), "chromium")
    assert browser_settings({"SUPPORTED_BROWSERS": "firefox, webkit", "DEFAULT_BROWSER": "webkit"}) == (("firefox", "webkit"), "webkit")


def test_the_default_must_be_one_of_the_supported_browsers():
    assert browser_settings({"SUPPORTED_BROWSERS": "chromium"}) == (("chromium",), "chromium")          # edge is not offered
    assert browser_settings({"SUPPORTED_BROWSERS": "firefox,chromium", "DEFAULT_BROWSER": "edge"}) == (("firefox", "chromium"), "firefox")


def test_case_spaces_repeats_and_unknown_names_are_ignored():
    assert browser_settings({"SUPPORTED_BROWSERS": " Chromium , chromium,netscape,,FIREFOX "}) == (("chromium", "firefox"), "chromium")
    assert browser_settings({"SUPPORTED_BROWSERS": "netscape,lynx"}) == (KNOWN_BROWSERS, "edge")      # nothing usable: all


@pytest.mark.parametrize("default", ["EDGE", " edge ", "Edge"])
def test_the_default_is_matched_ignoring_case_and_spaces(default):
    assert browser_settings({"DEFAULT_BROWSER": default})[1] == "edge"
