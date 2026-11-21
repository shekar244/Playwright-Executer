"""Extra pytest arguments of a run: what an admin configured in Amplify and the user picked when starting it.

A run carries them as a list of {"flag", "style", "value"}; the runner turns each into command-line words placed before
the "--" that precedes the test ids:  separate -> [flag, value]   joined -> [flag=value]   switch -> [flag].

The same rules are applied in Amplify (amplify/grid_args.py), here and on the pod (runner/pytest_args.py); keep the
three identical. Flags the runner sets itself, or that would change what it collects, are reserved.
"""
import re
from typing import List, Optional

from pydantic import BaseModel, model_validator

MAX_ARGS = 20
MAX_VALUE = 200
STYLES = ("separate", "joined", "switch")
FLAG = re.compile(r"^(-[A-Za-z]|--[A-Za-z][A-Za-z0-9_-]{0,63})$")
RESERVED = frozenset({
    "-p", "-n", "--numprocesses", "--maxprocesses", "--dist", "--alluredir", "-o", "--override-ini", "--headed",
    "--basetemp", "--rootdir", "--confcutdir", "--pyargs", "-h", "--help", "-V", "--version",
})
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def check(flag: str, style: str, value: Optional[str]) -> Optional[str]:
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


class PytestArg(BaseModel):
    flag: str
    style: str = "separate"
    value: Optional[str] = None

    @model_validator(mode="after")
    def _valid(self):
        problem = check(self.flag, self.style, self.value)
        if problem:
            raise ValueError(problem)
        return self


def validate_list(args: List[PytestArg]) -> List[PytestArg]:
    if len(args) > MAX_ARGS:
        raise ValueError(f"at most {MAX_ARGS} pytest arguments")
    flags = [a.flag for a in args]
    if len(set(flags)) != len(flags):
        raise ValueError("each pytest option may be given once")
    return args

