import json

import pandas as pd
import pytest

from jira_insights.pivot import ReportSpec
from jira_insights.store import STARTER_REPORTS, Store, slugify


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "ws")


@pytest.mark.parametrize("name, slug", [
    ("Sprint 42 Defects", "sprint-42-defects"),
    ("../../etc/passwd", "etc-passwd"),
    ("  ", "dataset"),
    ("a" * 100, "a" * 60),
])
def test_slugify_is_filesystem_safe(name, slug):
    assert slugify(name) == slug


def test_paths_never_escape_the_workspace(store):
    path = store.spec_path("../../outside")
    assert store.root in path.parents


def test_dataset_round_trip_preserves_types(store):
    df = pd.DataFrame({"Key": ["QA-1", "QA-2"], "Story Points": [3.0, None],
                       "Created": pd.to_datetime(["2026-09-01", "2026-09-02"])})
    meta = store.save_dataset("My Data", df, source="jql", jql="project = QA",
                              multi_cols=["Labels"], extra={"max_issues": 500})
    loaded, loaded_meta = store.load_dataset(meta["slug"])

    pd.testing.assert_frame_equal(loaded, df, check_dtype=False)
    assert pd.api.types.is_datetime64_any_dtype(loaded["Created"])
    assert loaded_meta["jql"] == "project = QA"
    assert loaded_meta["multi_value_cols"] == ["Labels"]
    assert loaded_meta["max_issues"] == 500
    assert [m["slug"] for m in store.list_datasets()] == ["my-data"]


def test_delete_dataset_removes_data_and_explorer_spec(store):
    store.save_dataset("d", pd.DataFrame({"Key": ["QA-1"]}), source="upload")
    store.spec_path("d").write_text("[]")
    store.delete_dataset("d")
    assert store.list_datasets() == []
    assert not store.spec_path("d").exists()


def test_reports_are_seeded_on_first_use(store):
    reports = store.list_reports()
    assert [r.name for r in reports] == [r["name"] for r in STARTER_REPORTS]
    assert all(r.id for r in reports)
    assert json.loads((store.root / "reports.json").read_text())   # persisted once


def test_save_report_appends_new_and_updates_in_place(store):
    first = store.list_reports()[0]
    added = store.save_report(ReportSpec(name="Mine", rows="Status"))
    first.name = "Renamed"
    store.save_report(first)

    reports = store.list_reports()
    assert reports[0].name == "Renamed" and reports[0].id == first.id
    assert reports[-1].id == added.id
    assert len(reports) == len(STARTER_REPORTS) + 1


def test_delete_report(store):
    target = store.list_reports()[1]
    store.delete_report(target.id)
    assert target.id not in {r.id for r in store.list_reports()}


def test_reports_get_unique_numbers_that_are_never_reused(store):
    seeded = store.list_reports()
    assert [r.number for r in seeded] == list(range(1, len(STARTER_REPORTS) + 1))
    assert seeded[0].code == "R-001" and seeded[0].title == f"R-001 · {seeded[0].name}"

    added = store.save_report(ReportSpec(name="Mine", rows="Status"))
    assert added.number == len(STARTER_REPORTS) + 1
    store.delete_report(added.id)
    again = store.save_report(ReportSpec(name="Another", rows="Status"))
    assert again.number == added.number + 1                      # deleted numbers are not reused


def test_edits_keep_their_number_and_copies_get_a_new_one(store):
    first = store.list_reports()[0]
    edited = ReportSpec.from_dict({**first.to_dict(), "name": "Renamed", "number": 0})
    assert store.save_report(edited).number == first.number      # builder edits keep the number
    copy = ReportSpec.from_dict({**first.to_dict(), "id": "", "name": "Copy"})
    assert store.save_report(copy).number not in {r.number for r in store.list_reports() if r.id != copy.id}


def test_reports_saved_before_numbering_are_numbered_once(store):
    import json
    raw = [ReportSpec(name=n, rows="Status", id=f"id{n}").to_dict() for n in ("a", "b")]
    for r in raw:
        r.pop("number")
    (store.root / "reports.json").write_text(json.dumps(raw))
    assert [r.number for r in store.list_reports()] == [1, 2]
    assert [r["number"] for r in json.loads((store.root / "reports.json").read_text())] == [1, 2]
    dup = json.loads((store.root / "reports.json").read_text())
    dup[1]["number"] = 1                                          # duplicate numbers get repaired
    (store.root / "reports.json").write_text(json.dumps(dup))
    assert sorted(r.number for r in store.list_reports()) == [1, 3]
