"""One Allure environment per browser (report_environments.py).

The generated config is JavaScript that the Allure CLI executes, and the values come from result files that a pod's
test code can write, so most of these tests are about what must never reach that file.
"""
import json
from pathlib import Path

import pytest

from coordinator import report_environments as env
from coordinator.report_environments import CONFIG_NAME, ENV_ID, MAX_ENVIRONMENTS, label_values, write_environments_config


def result(directory: Path, name: str, labels) -> None:
    (directory / f"{name}-result.json").write_text(json.dumps({"uuid": name, "name": name, "labels": labels}))


def labelled(browser, environment=None):
    return ([{"name": "browser", "value": browser}] if browser else []) + \
           ([{"name": "environment", "value": environment}] if environment else [])


def test_every_browser_found_in_the_results_gets_an_environment(tmp_path):
    result(tmp_path, "a", labelled("firefox", "qa"))
    result(tmp_path, "b", labelled("chromium", "qa"))
    result(tmp_path, "c", labelled("chromium", "qa"))

    config = write_environments_config(tmp_path, tmp_path / CONFIG_NAME)

    text = config.read_text()
    assert config == tmp_path / CONFIG_NAME and text.startswith("export default {")
    assert text.index('"chromium"') < text.index('"firefox"')                        # sorted, so the file is stable
    assert text.count("matcher:") == 2 and 'name === "browser" && value === "chromium"' in text
    assert '"Test environment": "qa"' in text                                         # the Grid environment is a variable


def test_results_without_a_browser_label_configure_nothing(tmp_path):
    result(tmp_path, "a", labelled(None, "qa"))
    result(tmp_path, "b", [])

    assert write_environments_config(tmp_path, tmp_path / CONFIG_NAME) is None
    assert not (tmp_path / CONFIG_NAME).exists()


def test_the_grid_environment_is_optional(tmp_path):
    result(tmp_path, "a", labelled("chromium"))

    assert "variables" not in write_environments_config(tmp_path, tmp_path / CONFIG_NAME).read_text()


@pytest.mark.parametrize("hostile", ['x"); process.exit(1); ("', "a b", "../../etc", "é", "x" * 65, "a\nb", "`${1}`"])
def test_a_browser_name_that_is_not_a_valid_environment_id_never_reaches_the_file(tmp_path, hostile):
    result(tmp_path, "a", labelled(hostile))
    result(tmp_path, "b", labelled("chromium"))

    text = write_environments_config(tmp_path, tmp_path / CONFIG_NAME).read_text()

    assert not ENV_ID.match(hostile) and hostile not in text and text.count("matcher:") == 1


def test_a_hostile_grid_environment_label_can_only_appear_as_an_escaped_json_string(tmp_path):
    hostile = 'qa"}, evil: (() => process.exit(1))(), x: {"'
    result(tmp_path, "a", labelled("chromium", hostile))

    text = write_environments_config(tmp_path, tmp_path / CONFIG_NAME).read_text()

    line = next(l for l in text.splitlines() if "Test environment" in l)
    literal = line.split('"Test environment": ', 1)[1].rstrip(" },")
    assert json.loads(literal) == hostile                    # one well-formed string literal: nothing escaped out of it
    assert text.count("matcher:") == 1 and "evil" not in text.replace(literal, "")


def test_a_control_character_in_the_grid_environment_is_dropped(tmp_path):
    result(tmp_path, "a", labelled("chromium", "qa\x1b[31m"))

    assert "variables" not in write_environments_config(tmp_path, tmp_path / CONFIG_NAME).read_text()


def test_unreadable_or_oddly_shaped_results_are_skipped_not_fatal(tmp_path):
    (tmp_path / "broken-result.json").write_text("{not json")
    (tmp_path / "list-result.json").write_text("[1, 2]")
    (tmp_path / "labels-not-a-list-result.json").write_text(json.dumps({"labels": {"name": "browser", "value": "x"}}))
    (tmp_path / "value-not-text-result.json").write_text(json.dumps({"labels": [{"name": "browser", "value": 7}, "junk", None]}))
    (tmp_path / "empty-result.json").write_bytes(b"")
    (tmp_path / "binary-result.json").write_bytes(b"\xff\xfe\x00")
    result(tmp_path, "ok", labelled("webkit"))

    assert label_values(tmp_path) == (["webkit"], [])


def test_stray_labels_cannot_create_hundreds_of_environments(tmp_path):
    for i in range(MAX_ENVIRONMENTS * 10):
        result(tmp_path, f"r{i}", labelled(f"b{i:04d}"))

    text = write_environments_config(tmp_path, tmp_path / CONFIG_NAME).read_text()

    assert text.count("matcher:") == MAX_ENVIRONMENTS


def test_the_config_never_has_a_name_the_cli_loads_by_itself():
    """The CLI runs in the work folder and picks up any allurerc.* there; a retry without --config must not."""
    assert not CONFIG_NAME.lower().startswith("allurerc") and CONFIG_NAME.endswith(".mjs")


def test_a_write_failure_means_no_environments_not_an_error(tmp_path):
    result(tmp_path, "a", labelled("chromium"))

    assert write_environments_config(tmp_path, tmp_path / "no-such-folder" / CONFIG_NAME) is None
