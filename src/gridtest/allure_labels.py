"""pytest plugin: tag every Allure result with the grid pod, browser and environment.

The runner loads it with -p gridtest.allure_labels so the Allure report can group by
host / browser / environment, and so the coordinator can give the report one Allure environment per browser
(coordinator/report_environments.py reads the browser and environment labels). It does nothing when allure-pytest is not installed or no
--alluredir was given, and a labelling problem can never fail a test.
"""
import os

import pytest

try:
    import allure
except ImportError:  # allure-pytest not installed: the plugin is a no-op
    allure = None

# Allure label name -> environment variable that carries its value (labels without a value are skipped).
LABEL_ENV = (("host", "GRID_POD"), ("browser", "BROWSER"), ("environment", "TEST_ENV"))


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    if allure is None:
        return
    for label, var in LABEL_ENV:
        value = os.getenv(var)
        if value:
            try:
                allure.dynamic.label(label, value)
            except Exception:
                pass  # reporting metadata only
    # Allure 3 keys a test's identity on its full name AND parameters. Without this parameter the same test
    # run on two browsers collapses into one test whose other-browser result is only a hidden "retry".
    browser = os.getenv("BROWSER")
    if browser:
        try:
            allure.dynamic.parameter("browser", browser)
        except Exception:
            pass
