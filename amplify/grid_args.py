"""Extra pytest arguments for Grid runs: defined by an admin (Admin Panel → Grid), chosen per run in the Grid tab.

A definition says how one pytest option is formed and where its value comes from:

    {"flag": "--env", "label": "Environment", "kind": "environment", "style": "separate",
     "values": ["qa3", "int", "prod"], "default": "qa3"}
    {"flag": "-m", "label": "Markers", "kind": "choice", "style": "separate",
     "values": ["smoke", "regression"], "default": "smoke", "required": false}
    {"flag": "--lf", "label": "Only last failures", "kind": "switch", "default": false}

  kind   environment — a dropdown of `values` (environment names); the choice is the run's environment (TEST_ENV on
                       the pods, the Runs list) and this option's value. Without values: the Grid Environments an
                       admin saved earlier (server_secrets.load_grid_environments), else the coordinator's list
         choice      — one of `values` (a dropdown in the Grid tab); "" = not passed, unless `required`
         switch      — the bare flag, on or off (a checkbox)
  style  separate → `--env qa3`   joined → `--env=qa3`   (switches are always just the flag)

A run sends the coordinator [{"flag", "style", "value"}]; the runner puts the words before the "--" that precedes the
test ids. The rules below are the same in coordinator/pytest_args.py and runner/pytest_args.py: keep them identical.
Stored under "grid_pytest_args" in server_config.json; with nothing saved, runs pass no extra arguments (the
environment still reaches the pods as TEST_ENV). The options must exist in the test repo, or pytest stops at start-up.
"""
from __future__ import annotations

import re

from amplify.server_secrets import _load_server_file, save_server_config

MAX_ARGS = 20
MAX_VALUE = 200
MAX_VALUES = 50
STYLES = ("separate", "joined", "switch")
KINDS = ("environment", "choice", "switch")
FLAG = re.compile(r"^(-[A-Za-z]|--[A-Za-z][A-Za-z0-9_-]{0,63})$")
RESERVED = frozenset({
    "-p", "-n", "--numprocesses", "--maxprocesses", "--dist", "--alluredir", "-o", "--override-ini", "--headed",
    "--basetemp", "--rootdir", "--confcutdir", "--pyargs", "-h", "--help", "-V", "--version",
})
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_ENV_NAME = re.compile(r"^[a-z0-9_-]{1,64}$")
_KEY = "grid_pytest_args"


def check(flag, style, value) -> str | None:
    """None when (flag, style, value) is an acceptable argument, else why not."""
    if not isinstance(flag, str) or not FLAG.match(flag):
        return f"{flag!r} is not a pytest option name (like -m or --env)"
    if flag in RESERVED:
        return f"{flag} is set by the Grid runner itself and cannot be configured"
    if style not in STYLES:
        return f"style must be one of {', '.join(STYLES)}"
    if style == "switch":
        return None if value is None else f"{flag} is a switch and takes no value"
    if style == "joined" and not flag.startswith("--"):
        return f"{flag} is a short option: use the separate style ({flag} value)"
    if not isinstance(value, str) or not value or len(value) > MAX_VALUE or _CONTROL.search(value):
        return f"the value of {flag} must be 1-{MAX_VALUE} printable characters"
    if style == "separate" and value.startswith("-"):
        return f"the value of {flag} cannot start with '-' in the separate style (pytest would read it as an option)"
    return None


def words(args: list[dict]) -> list[str]:
    """The command-line words the runner will build from checked arguments (shown as a preview)."""
    out: list[str] = []
    for a in args:
        flag, style, value = a["flag"], a["style"], a.get("value")
        out += [flag] if style == "switch" else ([f"{flag}={value}"] if style == "joined" else [flag, value])
    return out


# ── definitions (admin) ───────────────────────────────────────────────────────

def _text(value, name: str, limit: int) -> str:
    if value is None:
        return ""
    if not isinstance(value, str) or len(value.strip()) > limit or _CONTROL.search(value):
        raise ValueError(f"{name} must be text of at most {limit} characters")
    return value.strip()


def normalize(item) -> dict:
    """One definition, checked and in canonical form. Raises ValueError with a message fit for the admin."""
    if not isinstance(item, dict):
        raise ValueError("Each argument must be an object")
    flag = _text(item.get("flag"), "The option", 66)
    kind = item.get("kind") or "choice"
    if kind not in KINDS:
        raise ValueError(f"{flag or 'An argument'}: kind must be one of {', '.join(KINDS)}")
    style = "switch" if kind == "switch" else (item.get("style") or "separate")
    if style not in ("separate", "joined") and kind != "switch":
        raise ValueError(f"{flag}: style must be separate or joined")
    problem = check(flag, style, None if style == "switch" else "x")
    if problem:
        raise ValueError(problem)
    label = _text(item.get("label"), f"{flag}: the label", 60) or flag
    out = {"flag": flag, "label": label, "kind": kind, "style": style}
    if kind == "switch":
        default = item.get("default", False)
        if not isinstance(default, bool):
            raise ValueError(f"{flag}: a switch's default is on (true) or off (false)")
        out["default"] = default
    elif kind == "environment":
        values = item.get("values") or []
        if not isinstance(values, list) or len(values) > MAX_VALUES:
            raise ValueError(f"{flag}: give at most {MAX_VALUES} environments")
        clean = []
        for v in values:
            v = v.strip().lower() if isinstance(v, str) else v
            if not isinstance(v, str) or not _ENV_NAME.match(v):
                raise ValueError(f"{flag}: environment names are letters, numbers, '-' and '_' ({v!r})")
            if v not in clean:
                clean.append(v)
        default = (_text(item.get("default"), f"{flag}: the default", 64) or "").lower()
        if default and default not in clean:
            raise ValueError(f"{flag}: the default environment must be one of its values")
        if clean:
            out.update(values=clean, default=default or clean[0])
    elif kind == "choice":
        values = item.get("values")
        if not isinstance(values, list) or not values or len(values) > MAX_VALUES:
            raise ValueError(f"{flag}: give 1 to {MAX_VALUES} values to choose from")
        clean = []
        for v in values:
            v = v.strip() if isinstance(v, str) else v
            problem = check(flag, style, v)
            if problem:
                raise ValueError(problem)
            if v not in clean:
                clean.append(v)
        required = item.get("required", False)
        if not isinstance(required, bool):
            raise ValueError(f"{flag}: required is true or false")
        default = _text(item.get("default"), f"{flag}: the default", MAX_VALUE)
        if default and default not in clean:
            raise ValueError(f"{flag}: the default must be one of its values")
        out.update(values=clean, required=required, default=default or (clean[0] if required else ""))
    return out


def validate_definitions(items) -> list[dict]:
    if not isinstance(items, list):
        raise ValueError("arguments must be a list")
    if len(items) > MAX_ARGS:
        raise ValueError(f"At most {MAX_ARGS} arguments")
    out = [normalize(i) for i in items]
    flags = [d["flag"] for d in out]
    duplicate = next((f for f in flags if flags.count(f) > 1), None)
    if duplicate:
        raise ValueError(f"{duplicate} is defined twice")
    if sum(d["kind"] == "environment" for d in out) > 1:
        raise ValueError("Only one argument can take the run's environment")
    return out


def load_definitions() -> list[dict]:
    """What the admin saved; [] (no extra arguments) when nothing was saved."""
    saved = _load_server_file().get(_KEY)
    if not isinstance(saved, dict) or not isinstance(saved.get("arguments"), list):
        return []
    try:
        return validate_definitions(saved["arguments"])
    except ValueError:
        return []                                    # a hand-edited file that breaks the rules passes nothing


def save_definitions(items) -> list[dict]:
    clean = validate_definitions(items)
    data = _load_server_file()
    data[_KEY] = {"arguments": clean}
    save_server_config(data)
    return clean


# ── a run's environment ──────────────────────────────────────────────────────

def run_environments(definitions: list[dict]) -> dict:
    """{"environments", "default_environment"} a run may use: the "Run's environment" argument's values, else the
    Grid Environments saved earlier in Admin → Grid ({"environments": []} = the coordinator's own list)."""
    env = next((d for d in definitions if d.get("kind") == "environment" and d.get("values")), None)
    if env:
        return {"environments": list(env["values"]), "default_environment": env.get("default") or env["values"][0]}
    from amplify.server_secrets import load_grid_environments
    return load_grid_environments()


# ── a run's choice (Grid tab) ─────────────────────────────────────────────────

def resolve(definitions: list[dict], selection, environment: str | None) -> list[dict]:
    """The run's arguments [{"flag", "style", "value"}] for what the user picked. Raises ValueError."""
    if selection is None:
        selection = {}
    if not isinstance(selection, dict):
        raise ValueError("pytest_args must be an object of option → value")
    known = {d["flag"]: d for d in definitions}
    unknown = [f for f in selection if f not in known]
    if unknown:
        raise ValueError(f"{unknown[0]!r} is not one of the pytest arguments an admin configured")
    out = []
    for d in definitions:
        flag, kind, style = d["flag"], d["kind"], d["style"]
        if kind == "environment":
            if environment:
                value = environment
            else:
                continue
        elif kind == "switch":
            on = selection.get(flag, d.get("default", False))
            if not isinstance(on, bool):
                raise ValueError(f"{d['label']} is on (true) or off (false)")
            if on:
                out.append({"flag": flag, "style": "switch", "value": None})
            continue
        else:
            value = selection.get(flag, d.get("default", ""))
            if value is None or value == "":
                if d.get("required"):
                    raise ValueError(f"Choose a value for {d['label']}")
                continue
            if value not in d.get("values", []):
                raise ValueError(f"{value!r} is not one of the values configured for {d['label']}")
        problem = check(flag, style, value)
        if problem:
            raise ValueError(problem)
        out.append({"flag": flag, "style": style, "value": value})
    return out
