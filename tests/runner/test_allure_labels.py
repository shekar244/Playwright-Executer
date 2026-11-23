"""gridtest.allure_labels in isolation (the end-to-end labelling is covered in test_run_snapshot.py)."""
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from gridtest import allure_labels

PLUGIN_FILE = Path(allure_labels.__file__)


def _fake_allure(recorded, fail=False):
    def label(name, value):
        if fail:
            raise RuntimeError("allure broke")
        recorded.append((name, value))

    def parameter(name, value):
        if fail:
            raise RuntimeError("allure broke")
        recorded.append(("param:" + name, value))

    return SimpleNamespace(dynamic=SimpleNamespace(label=label, parameter=parameter))


def test_sets_only_the_labels_whose_variable_is_set(monkeypatch):
    recorded = []
    monkeypatch.setattr(allure_labels, "allure", _fake_allure(recorded))
    monkeypatch.setenv("GRID_POD", "pod-7")
    monkeypatch.setenv("BROWSER", "firefox")
    monkeypatch.delenv("TEST_ENV", raising=False)

    allure_labels.pytest_runtest_setup(object())

    assert recorded == [("host", "pod-7"), ("browser", "firefox"), ("param:browser", "firefox")]


def test_all_three_labels(monkeypatch):
    recorded = []
    monkeypatch.setattr(allure_labels, "allure", _fake_allure(recorded))
    monkeypatch.setenv("GRID_POD", "p")
    monkeypatch.setenv("BROWSER", "b")
    monkeypatch.setenv("TEST_ENV", "qa")

    allure_labels.pytest_runtest_setup(object())

    assert recorded == [("host", "p"), ("browser", "b"), ("environment", "qa"), ("param:browser", "b")]


def test_a_labelling_error_never_reaches_the_test(monkeypatch):
    monkeypatch.setattr(allure_labels, "allure", _fake_allure([], fail=True))
    monkeypatch.setenv("GRID_POD", "p")

    allure_labels.pytest_runtest_setup(object())  # must not raise


def test_is_a_noop_when_allure_cannot_be_imported(monkeypatch):
    monkeypatch.setitem(sys.modules, "allure", None)  # `import allure` now raises ImportError
    spec = importlib.util.spec_from_file_location("allure_labels_without_allure", PLUGIN_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv("GRID_POD", "p")

    assert module.allure is None
    module.pytest_runtest_setup(object())  # must not raise


def test_the_hook_runs_first():
    marker = getattr(allure_labels.pytest_runtest_setup, "pytest_impl", None)
    assert marker is not None and marker["tryfirst"] is True


def test_the_browser_is_also_a_parameter_so_one_test_on_two_browsers_stays_two_results(monkeypatch):
    recorded = []
    monkeypatch.setattr(allure_labels, "allure", _fake_allure(recorded))
    monkeypatch.delenv("GRID_POD", raising=False)
    monkeypatch.delenv("TEST_ENV", raising=False)
    monkeypatch.setenv("BROWSER", "webkit")

    allure_labels.pytest_runtest_setup(object())

    assert ("param:browser", "webkit") in recorded
    monkeypatch.delenv("BROWSER")
    recorded.clear()
    allure_labels.pytest_runtest_setup(object())
    assert recorded == []                                         # no browser, no parameter
