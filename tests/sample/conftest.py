import os

import pytest

from gridtest import browsers, environments

# Records per-test outcomes for the grid runner (active only when GRID_RESULTS_FILE is set).
pytest_plugins = ["gridtest.results_plugin"]


def pytest_addoption(parser):
    # Lives here, not in the gridtest plugin the runner loads into every repo: a repo that defines --env itself
    # would otherwise fail to start ("conflicting option string"). Admin → Grid passes it as `--env <environment>`.
    parser.addoption("--env", action="store", default="", help="Target environment (overrides TEST_ENV)")


def pytest_configure(config):
    environment = config.getoption("--env")
    if environment:
        os.environ["TEST_ENV"] = environment


def pytest_report_header(config):
    return f"grid: browser={browsers.current_browser()} environment={environments.current_environment()}"


@pytest.fixture(scope="session")
def base_url() -> str:
    return environments.base_url()


@pytest.fixture(scope="function")
def page():
    # Imported here so test collection (e.g. by the seeder) does not require Playwright.
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = browsers.launch(p)
        context = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = context.new_page()
        yield page
        context.close()
        browser.close()
