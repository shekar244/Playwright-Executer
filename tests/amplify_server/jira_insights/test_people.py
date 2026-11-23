import pandas as pd

from jira_insights.store import Store
from jira_insights.ui import people

RUNS = pd.DataFrame({"Executed By": ["acc-1", "acc-2", "acc-1"], "Assignee": ["acc-3", None, None]})


def test_lookup_fills_only_missing_names_and_is_remembered(tmp_path):
    store = Store(tmp_path)
    people.remember(store, {"acc-2": "Typed By Hand"})
    asked = []
    found = people.resolve_missing(store, RUNS, lambda ids: asked.extend(ids) or {"acc-1": "Ben Ortiz"})
    assert found == 1 and asked == ["acc-1", "acc-3"]                     # typed names are never overwritten
    assert people.known_names(Store(tmp_path)) == {"acc-2": "Typed By Hand", "acc-1": "Ben Ortiz"}


def test_failed_or_missing_lookups_never_break_the_fetch(tmp_path):
    store = Store(tmp_path)
    assert people.resolve_missing(store, RUNS, None) == 0
    assert people.resolve_missing(store, RUNS, lambda ids: 1 / 0) == 0
    assert people.known_names(store) == {}
