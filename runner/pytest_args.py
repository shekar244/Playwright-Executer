"""Extra pytest arguments a leased chunk carries: [{"flag", "style", "value"}], configured by an Amplify admin.

Turned into the command-line words placed before the "--" that precedes the test ids:
  separate -> [flag, value]    joined -> [flag=value]    switch -> [flag]
The rules are the same as in the coordinator (coordinator/pytest_args.py) and Amplify (amplify/grid_args.py); keep
the three identical. They are checked again here: a chunk whose arguments break them fails, with the reason, rather
than running with some of them dropped.
"""
from __future__ import annotations

import re

MAX_ARGS = 20
MAX_VALUE = 200
STYLES = ("separate", "joined", "switch")
FLAG = re.compile(r"^(-[A-Za-z]|--[A-Za-z][A-Za-z0-9_-]{0,63})$")
RESERVED = frozenset({
    "-p", "-n", "--numprocesses", "--maxprocesses", "--dist", "--alluredir", "-o", "--override-ini", "--headed",
    "--basetemp", "--rootdir", "--confcutdir", "--pyargs", "-h", "--help", "-V", "--version",
})
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class InvalidArguments(ValueError):
    pass


def check(flag, style, value) -> str | None:
    """None when (flag, style, value) is an acceptable argument, else why not."""
    if not isinstance(flag, str) or not FLAG.match(flag):
        return f"{flag!r} is not a pytest option name"
    if flag in RESERVED:
        return f"{flag} is set by the Grid runner itself"
    if style not in STYLES:
        return f"style must be one of {', '.join(STYLES)}"
    if style == "switch":
        return None if value is None else f"{flag} is a switch and takes no value"
    if style == "joined" and not flag.startswith("--"):
        return f"{flag} is a short option and cannot be joined"
    if not isinstance(value, str) or not value or len(value) > MAX_VALUE or _CONTROL.search(value):
        return f"the value of {flag} must be 1-{MAX_VALUE} printable characters"
    if style == "separate" and value.startswith("-"):
        return f"the value of {flag} cannot start with '-' in the separate style"
    return None


def words(args) -> list[str]:
    """Command-line words for the chunk's arguments ([] for None). Raises InvalidArguments."""
    if args is None:
        return []
    if not isinstance(args, list) or len(args) > MAX_ARGS:
        raise InvalidArguments(f"expected a list of at most {MAX_ARGS} arguments")
    out: list[str] = []
    seen: set[str] = set()
    for a in args:
        if not isinstance(a, dict):
            raise InvalidArguments("each argument must be an object")
        flag, style, value = a.get("flag"), a.get("style", "separate"), a.get("value")
        problem = check(flag, style, value) or (f"{flag} is given twice" if flag in seen else None)
        if problem:
            raise InvalidArguments(problem)
        seen.add(flag)
        out += [flag] if style == "switch" else ([f"{flag}={value}"] if style == "joined" else [flag, value])
    return out
