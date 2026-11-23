"""Extra pytest arguments for Grid runs: an admin defines them (Admin → Grid), users pick values in the Grid tab, and the
run carries [{"flag", "style", "value"}] to the coordinator and on to the runner pods.

Covers the definitions an admin may save, how a user's pick becomes arguments, the endpoints, what reaches the
coordinator (and that an older coordinator is refused rather than silently dropping them), and that the rules are the
same in Amplify, the coordinator and the runner.
"""
import ast
import importlib
from pathlib import Path

import pytest
from flask import Flask

from amplify_server import grid_args
from amplify_server.routes import grid

ROOT = Path(__file__).resolve().parents[3]
ENV = {"flag": "--env", "label": "Environment", "kind": "environment", "style": "separate"}
MARKS = {"flag": "-m", "label": "Markers", "kind": "choice", "style": "separate", "values": ["smoke", "regression"],
         "default": "smoke", "required": False}
LF = {"flag": "--lf", "label": "Last failures", "kind": "switch", "style": "switch", "default": False}


# ── definitions ───────────────────────────────────────────────────────────────

def test_with_nothing_saved_there_are_no_extra_arguments(data_dir):
    assert grid_args.load_definitions() == []


def test_definitions_are_saved_in_canonical_form(data_dir):
    saved = grid_args.save_definitions([
        {"flag": " --env ", "kind": "environment", "style": "joined", "label": " Env "},
        {"flag": "-m", "kind": "choice", "values": [" smoke ", "regression", "smoke"]},
        {"flag": "--lf", "kind": "switch", "default": True, "style": "joined"},
        {"flag": "-c", "kind": "choice", "values": ["config/pytest.ini"], "required": True},
    ])

    assert saved == [
        {"flag": "--env", "label": "Env", "kind": "environment", "style": "joined"},
        {"flag": "-m", "label": "-m", "kind": "choice", "style": "separate", "values": ["smoke", "regression"],
         "required": False, "default": ""},
        {"flag": "--lf", "label": "--lf", "kind": "switch", "style": "switch", "default": True},
        {"flag": "-c", "label": "-c", "kind": "choice", "style": "separate", "values": ["config/pytest.ini"],
         "required": True, "default": "config/pytest.ini"},              # required without a default: the first value
    ]
    assert grid_args.load_definitions() == saved


def test_saving_an_empty_list_means_no_extra_arguments(data_dir):
    grid_args.save_definitions([])
    assert grid_args.load_definitions() == []


@pytest.mark.parametrize("items, why", [
    ([{"flag": "env", "kind": "environment"}], "not a pytest option"),
    ([{"flag": "--env value", "kind": "environment"}], "not a pytest option"),
    ([{"flag": "-p", "kind": "choice", "values": ["no:cacheprovider"]}], "set by the Grid runner"),
    ([{"flag": "--alluredir", "kind": "environment"}], "set by the Grid runner"),
    ([{"flag": "-n", "kind": "choice", "values": ["8"]}], "set by the Grid runner"),
    ([{"flag": "-m", "kind": "choice", "style": "joined", "values": ["x"]}], "short option"),
    ([{"flag": "-m", "kind": "choice", "values": []}], "give 1 to"),
    ([{"flag": "-m", "kind": "choice", "values": ["--collect-only"]}], "cannot start with '-'"),
    ([{"flag": "-m", "kind": "choice", "values": ["a\nb"]}], "printable"),
    ([{"flag": "-m", "kind": "choice", "values": ["a"], "default": "b"}], "default must be one of"),
    ([{"flag": "--lf", "kind": "switch", "default": "yes"}], r"on \(true\) or off"),
    ([{"flag": "--x", "kind": "bogus"}], "kind must be"),
    ([ENV, dict(ENV, flag="--stage")], "Only one argument can take the run's environment"),
    ([MARKS, MARKS], "defined twice"),
    ([dict(LF, flag=f"--o{i}") for i in range(21)], "At most 20"),
    ("--env", "must be a list"),
    ([None], "must be an object"),
])
def test_bad_definitions_are_refused_and_nothing_is_saved(data_dir, items, why):
    with pytest.raises(ValueError, match=why):
        grid_args.save_definitions(items)
    assert grid_args.load_definitions() == []


# ── a user's pick ─────────────────────────────────────────────────────────────

DEFS = [ENV, MARKS, LF]


def test_a_pick_becomes_the_runs_arguments():
    assert grid_args.resolve(DEFS, {"-m": "regression", "--lf": True}, "qa3") == [
        {"flag": "--env", "style": "separate", "value": "qa3"},
        {"flag": "-m", "style": "separate", "value": "regression"},
        {"flag": "--lf", "style": "switch", "value": None},
    ]


def test_defaults_apply_when_nothing_is_picked():
    assert grid_args.resolve(DEFS, None, "int") == [{"flag": "--env", "style": "separate", "value": "int"},
                                                   {"flag": "-m", "style": "separate", "value": "smoke"}]


def test_not_passed_and_switched_off_leave_the_option_out():
    assert grid_args.resolve(DEFS, {"-m": "", "--lf": False}, None) == []


def test_the_words_the_runner_builds():
    args = grid_args.resolve([dict(ENV, style="joined"), MARKS, LF], {"--lf": True}, "qa3")
    assert grid_args.words(args) == ["--env=qa3", "-m", "smoke", "--lf"]


@pytest.mark.parametrize("selection, why", [
    ({"-m": "nightly"}, "not one of the values"),
    ({"-p": "evil"}, "not one of the pytest arguments"),
    ({"--lf": "yes"}, r"on \(true\) or off"),
    ("-m smoke", "must be an object"),
])
def test_a_pick_outside_what_the_admin_allowed_is_refused(selection, why):
    with pytest.raises(ValueError, match=why):
        grid_args.resolve(DEFS, selection, "qa3")


def test_a_required_choice_must_be_made():
    with pytest.raises(ValueError, match="Choose a value for Config"):
        grid_args.resolve([{"flag": "-c", "label": "Config", "kind": "choice", "style": "separate",
                            "values": ["a.ini"], "required": True, "default": ""}], {"-c": ""}, "qa")


# ── the endpoints ─────────────────────────────────────────────────────────────

@pytest.fixture
def app(data_dir):
    flask_app = Flask(__name__)
    for name in ("auth", "admin", "grid"):
        flask_app.register_blueprint(importlib.import_module(f"amplify_server.routes.{name}").bp)
    return flask_app


class Coordinator:
    def __init__(self, features=("code-delivery", "pytest-args")):
        self.features, self.calls = list(features), []

    def __call__(self, method, path, params=None, body=None, timeout=None):
        self.calls.append((method, path, body))
        if path == "/api/config":
            return {"features": self.features, "environments": ["qa3", "int"]}, 200
        if method == "POST" and path == "/api/runs":
            return {"run_id": "run-1", "chunks_queued": 1, **{k: body.get(k) for k in ("environment", "pytest_args")}}, 200
        return {"error": "unexpected"}, 500


@pytest.fixture
def coordinator(monkeypatch):
    fake = Coordinator()
    monkeypatch.setattr(grid, "coordinator_request", fake)
    return fake


def test_only_admins_can_change_the_arguments(admin, user):
    assert user.post("/api/admin/grid/pytest-args", json={"arguments": []}).status_code == 403
    assert user.get("/api/admin/grid/pytest-args").status_code == 403


def test_an_admin_saves_them_and_users_see_them(admin, user):
    resp = admin.post("/api/admin/grid/pytest-args", json={"arguments": [ENV, MARKS]})

    assert resp.status_code == 200 and resp.get_json()["arguments"] == [ENV, MARKS]
    assert "-p" in resp.get_json()["reserved"]
    assert user.get("/api/grid/pytest-args").get_json() == {"arguments": [ENV, MARKS]}
    log = admin.get("/api/admin/activity").get_json()
    assert any(a["action"] == "admin_update_grid_pytest_args" and "--env, -m" in a["detail"] for a in log)


@pytest.mark.parametrize("body", [{"arguments": [{"flag": "-p", "kind": "switch"}]}, {"arguments": "x"}, ["x"]])
def test_bad_admin_input_is_a_400(admin, body):
    resp = admin.post("/api/admin/grid/pytest-args", json=body)

    assert resp.status_code == 400 and resp.get_json()["error"]


def start(client, **extra):
    return client.post("/api/grid/runs", json={"tests": ["tests/test_a.py"], "environment": "qa3",
                                               "browsers": ["chromium"], **extra})


def test_a_run_carries_the_picked_arguments_to_the_coordinator(admin, user, coordinator):
    admin.post("/api/admin/grid/pytest-args", json={"arguments": [ENV, MARKS, LF]})

    resp = start(user, pytest_args={"-m": "regression", "--lf": True})

    assert resp.status_code == 200
    sent = next(body for method, path, body in coordinator.calls if path == "/api/runs")
    assert sent["pytest_args"] == [{"flag": "--env", "style": "separate", "value": "qa3"},
                                   {"flag": "-m", "style": "separate", "value": "regression"},
                                   {"flag": "--lf", "style": "switch", "value": None}]


def test_the_environment_argument_takes_the_runs_environment(admin, user, coordinator):
    admin.post("/api/admin/grid/pytest-args", json={"arguments": [ENV]})

    start(user)

    sent = next(body for method, path, body in coordinator.calls if path == "/api/runs")
    assert sent["pytest_args"] == [{"flag": "--env", "style": "separate", "value": "qa3"}]


def test_a_bad_pick_is_a_400_and_nothing_is_sent(admin, user, coordinator):
    admin.post("/api/admin/grid/pytest-args", json={"arguments": [MARKS]})

    resp = start(user, pytest_args={"-m": "nightly"})

    assert resp.status_code == 400 and "not one of the values" in resp.get_json()["error"]
    assert not any(path == "/api/runs" for _, path, _ in coordinator.calls)


def test_an_older_coordinator_is_refused_instead_of_dropping_the_arguments(admin, user, coordinator):
    admin.post("/api/admin/grid/pytest-args", json={"arguments": [ENV]})
    coordinator.features = ["code-delivery"]

    resp = start(user)

    assert resp.status_code == 502 and "too old for pytest arguments" in resp.get_json()["error"]
    assert not any(path == "/api/runs" for _, path, _ in coordinator.calls)


def test_without_arguments_nothing_extra_is_sent_or_checked(user, coordinator):
    coordinator.features = []

    assert start(user).status_code == 200
    sent = next(body for method, path, body in coordinator.calls if path == "/api/runs")
    assert "pytest_args" not in sent and not any(path == "/api/config" for _, path, _ in coordinator.calls)


# ── the same rules in all three places ────────────────────────────────────────

def _constants(path: Path) -> dict:
    tree = ast.parse(path.read_text())
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in ("MAX_ARGS", "MAX_VALUE", "STYLES", "FLAG", "RESERVED"):
                out[name] = ast.unparse(node.value)
    return out


def test_amplify_the_coordinator_and_the_runner_apply_the_same_rules():
    amplify_rules = _constants(ROOT / "src" / "amplify_server" / "grid_args.py")

    assert set(amplify_rules) == {"MAX_ARGS", "MAX_VALUE", "STYLES", "FLAG", "RESERVED"}
    assert _constants(ROOT / "src" / "coordinator" / "pytest_args.py") == amplify_rules
    assert _constants(ROOT / "src" / "runner" / "pytest_args.py") == amplify_rules


# ── the environment argument carries the environments (Admin → Grid no longer has its own list) ──

ENV_VALUES = {"flag": "--env", "label": "Environment", "kind": "environment", "style": "separate",
              "values": ["qa3", "int", "prod"], "default": "int"}


def test_the_environment_argument_keeps_its_values_and_default(data_dir):
    saved = grid_args.save_definitions([dict(ENV_VALUES, values=[" QA3 ", "int", "prod", "int"], default="INT")])

    assert saved == [ENV_VALUES]
    assert grid_args.run_environments(saved) == {"environments": ["qa3", "int", "prod"], "default_environment": "int"}


@pytest.mark.parametrize("item, why", [
    (dict(ENV_VALUES, values=["qa 3"]), "environment names"),
    (dict(ENV_VALUES, values=["qa;rm"]), "environment names"),
    (dict(ENV_VALUES, default="stage"), "default environment must be one of"),
])
def test_bad_environments_are_refused(data_dir, item, why):
    with pytest.raises(ValueError, match=why):
        grid_args.save_definitions([item])


def test_without_values_the_grid_environments_saved_earlier_still_apply(data_dir):
    from amplify_server import server_secrets as ss
    ss.save_grid_config(environments=["qa3", "int"], default_environment="qa3")

    assert grid_args.run_environments([ENV]) == {"environments": ["qa3", "int"], "default_environment": "qa3"}
    assert grid_args.run_environments([dict(ENV_VALUES)])["environments"] == ["qa3", "int", "prod"]   # the argument wins


def test_the_grid_tab_offers_the_arguments_environments(admin, user, monkeypatch):
    monkeypatch.setattr(grid, "coordinator_request",
                        lambda m, p, *a, **k: ({"environments": ["dev", "qa"], "default_environment": "qa", "features": []}, 200))
    admin.post("/api/admin/grid/pytest-args", json={"arguments": [ENV_VALUES]})

    cfg = user.get("/api/grid/config").get_json()

    assert cfg["environments"] == ["qa3", "int", "prod"] and cfg["default_environment"] == "int"


def test_a_run_must_use_one_of_the_arguments_environments_and_defaults_to_its_default(admin, user, coordinator):
    admin.post("/api/admin/grid/pytest-args", json={"arguments": [ENV_VALUES]})

    bad = user.post("/api/grid/runs", json={"tests": ["tests/test_a.py"], "environment": "dev"})
    assert bad.status_code == 400 and "Unsupported environment 'dev'" in bad.get_json()["error"]

    assert user.post("/api/grid/runs", json={"tests": ["tests/test_a.py"]}).status_code == 200
    sent = next(body for method, path, body in coordinator.calls if path == "/api/runs")
    assert sent["environment"] == "int" and sent["pytest_args"] == [{"flag": "--env", "style": "separate", "value": "int"}]
