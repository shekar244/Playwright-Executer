"""Where Amplify finds the Allure CLI.

`allure3_bin` (Config → Allure) may be the executable, "npx", or the folder `npm install -g allure` puts it in
(what `echo $(npm config get prefix)/bin` prints). When it is empty the ALLURE3_BIN environment variable, which the
Amplify image sets, is the default; a saved value always wins. A server that has ALLURE3_BIN has no Java, so its
reports default to Allure 3 too, for every new user.
"""
import json

import pytest

from amplify.config_reader import _DEFAULTS, fresh_defaults
from amplify.routes.executor import _NPX_SENTINEL, _find_allure_bin


def make_cli(folder, name="allure"):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)
    return path


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.delenv("ALLURE3_BIN", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "nothing-on-path"))     # nothing is found by accident
    return tmp_path


def test_an_explicit_executable_is_used_as_it_is(tmp_path):
    cli = make_cli(tmp_path / "somewhere", "my-allure")

    assert _find_allure_bin(str(cli), want_v2=False) == str(cli)


def test_a_folder_means_the_allure_inside_it(tmp_path):
    """The value `echo $(npm config get prefix)/bin` prints is a folder, not the executable."""
    cli = make_cli(tmp_path / "npm-bin")

    assert _find_allure_bin(str(tmp_path / "npm-bin"), want_v2=False) == str(cli)


def test_a_folder_without_allure_falls_through_to_the_path(tmp_path, monkeypatch):
    (tmp_path / "empty").mkdir()
    on_path = make_cli(tmp_path / "on-path")
    monkeypatch.setenv("PATH", str(tmp_path / "on-path"))

    assert _find_allure_bin(str(tmp_path / "empty"), want_v2=False) == str(on_path)


def test_an_empty_setting_falls_back_to_the_server_default(tmp_path, monkeypatch):
    cli = make_cli(tmp_path / "server-bin")
    monkeypatch.setenv("ALLURE3_BIN", str(tmp_path / "server-bin"))

    assert _find_allure_bin("", want_v2=False) == str(cli)
    assert _find_allure_bin("   ", want_v2=False) == str(cli)


def test_a_saved_value_beats_the_server_default(tmp_path, monkeypatch):
    server = make_cli(tmp_path / "server-bin")
    mine = make_cli(tmp_path / "mine", "my-allure")
    monkeypatch.setenv("ALLURE3_BIN", str(tmp_path / "server-bin"))

    assert _find_allure_bin(str(mine), want_v2=False) == str(mine) != str(server)


def test_the_server_default_is_for_allure_3_only(tmp_path, monkeypatch):
    make_cli(tmp_path / "server-bin")
    v2_on_path = make_cli(tmp_path / "on-path")
    monkeypatch.setenv("ALLURE3_BIN", str(tmp_path / "server-bin"))
    monkeypatch.setenv("PATH", str(tmp_path / "on-path"))

    assert _find_allure_bin("", want_v2=True) == str(v2_on_path)


def test_npx_is_still_a_keyword_in_the_setting_and_in_the_server_default(tmp_path, monkeypatch):
    make_cli(tmp_path / "bin", "npx")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))

    assert _find_allure_bin("npx", want_v2=False) == _NPX_SENTINEL
    monkeypatch.setenv("ALLURE3_BIN", "npx")
    assert _find_allure_bin("", want_v2=False) == _NPX_SENTINEL


def test_a_server_with_an_allure_3_cli_defaults_to_allure_3_reports(monkeypatch):
    assert fresh_defaults()["allure_format"] == "allure2"
    monkeypatch.setenv("ALLURE3_BIN", "/usr/local/bin")

    assert fresh_defaults()["allure_format"] == "allure3"
    assert _DEFAULTS["allure_format"] == "allure2"          # the shared defaults are never modified


def test_a_new_users_saved_config_starts_with_that_format(data_dir, monkeypatch):
    from amplify.storage import get_user_config_path, initialize_user_workspace
    monkeypatch.setenv("ALLURE3_BIN", "/usr/local/bin")

    initialize_user_workspace(7)

    assert json.loads(get_user_config_path(7).read_text())["allure_format"] == "allure3"
