"""Allure 3 environments for a run's report: one per browser.

With an environment per browser the report gets an "Environment" dropdown, lists the results under each browser,
and gives each test an "Environments" tab comparing its outcome across browsers. It is also the native answer to
a problem the runner otherwise works around: Allure 3 identifies a test by its name and parameters, so the same
test on two browsers collapses into one test with a hidden "retry" (and a wrong pass rate) unless the results carry
a `browser` parameter, which gridtest.allure_labels adds. Allure reads environments from a config file, so one is
written beside the results and passed with `--config`.

The file is JavaScript (.mjs: a matcher is a function) and the CLI executes it, so nothing taken from the results is
ever pasted in unchecked: a browser name must be a valid Allure environment id, anything else goes in as JSON.

Pure functions, no database or web imports: unit-tested on their own.
"""
import json
import logging
import re
from pathlib import Path
from typing import List, Optional, Tuple

log = logging.getLogger("coordinator")

# NOT allurerc.*: the CLI loads a file of that name from its working directory without being asked, which would
# make the fail-soft retries (that drop --config) use it anyway.
CONFIG_NAME = "environments.config.mjs"
ENV_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")   # Allure's rule for an environment id
MAX_ENVIRONMENTS = 20                           # five browsers exist; stray labels must not create hundreds
MAX_VALUES = 5                                  # distinct Grid environments listed in the report's variables
MAX_TEXT = 100


def label_values(results: Path) -> Tuple[List[str], List[str]]:
    """(browsers, environments): the distinct, sorted values of those two labels over every *-result.json.

    Files that are not JSON, or whose labels are not the expected shape, are skipped: a pod's test code can write
    anything into its results.
    """
    browsers, environments = set(), set()
    for path in results.glob("*-result.json"):
        try:
            labels = json.loads(path.read_text(encoding="utf-8")).get("labels")
        except (OSError, ValueError, AttributeError):
            continue
        for label in labels if isinstance(labels, list) else []:
            if not isinstance(label, dict) or not isinstance(label.get("value"), str):
                continue
            if label.get("name") == "browser" and len(browsers) <= MAX_ENVIRONMENTS * 4:
                browsers.add(label["value"])
            elif label.get("name") == "environment" and len(environments) <= MAX_VALUES * 4:
                environments.add(label["value"])
    return sorted(browsers), sorted(environments)


def _environment(browser: str) -> str:
    quoted = json.dumps(browser)
    return (f"    {quoted}: {{ name: {quoted}, matcher: ({{ labels }}) => "
            f'labels.some(({{ name, value }}) => name === "browser" && value === {quoted}) }}')


def write_environments_config(results: Path, dest: Path) -> Optional[Path]:
    """Write the config for the browsers found in `results` to `dest`; None when there is nothing to configure.

    The Grid environment (qa, perf, ...) a run was started against is listed as a report variable. Never raises:
    on any problem the caller simply generates the report without environments.
    """
    try:
        browsers, grid_environments = label_values(results)
        ids = [b for b in browsers if ENV_ID.match(b)][:MAX_ENVIRONMENTS]
        if not ids:
            return None
        shown = [v[:MAX_TEXT] for v in grid_environments[:MAX_VALUES] if v.isprintable()]
        variables = f"  variables: {{ \"Test environment\": {json.dumps(', '.join(shown))} }},\n" if shown else ""
        dest.write_text("export default {\n" + variables + "  environments: {\n"
                        + ",\n".join(_environment(b) for b in ids) + "\n  },\n};\n", encoding="utf-8")
        return dest
    except Exception:  # reporting metadata only
        log.exception("Could not write the Allure environments config")
        return None
