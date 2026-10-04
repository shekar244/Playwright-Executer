from jira_insights import layout
from jira_insights.pivot import ReportSpec


def spec(rid, chart="Column", name=None):
    return ReportSpec(id=rid, chart=chart, name=name or rid)


REPORTS = [spec("n1", "Number"), spec("n2", "Number"), spec("c1"), spec("c2"), spec("c3"), spec("n3", "Number")]


def test_default_packs_numbers_four_and_charts_two_per_row():
    many = [spec(f"n{i}", "Number") for i in range(5)] + [spec("c1"), spec("c2"), spec("c3")]
    assert layout.default_layout(many) == [["n0", "n1", "n2", "n3"], ["n4"], ["c1", "c2"], ["c3"]]
    assert layout.default_layout(REPORTS) == [["n1", "n2"], ["c1", "c2"], ["c3"], ["n3"]]


def test_normalize_drops_unknown_duplicates_and_empty_rows_then_appends_new_reports():
    saved = [["c3", "ghost", "c3"], [], ["n1"], "junk", ["c1", "n2"]]
    assert layout.normalize(saved, REPORTS) == [["c3"], ["n1"], ["c1", "n2"], ["c2"], ["n3"]]
    assert layout.normalize(None, REPORTS) == layout.default_layout(REPORTS)


def test_hidden_reports_survive_an_edit_of_the_visible_ones():
    saved = [["n1", "c1"], ["c2"], ["c3"]]
    visible = {"n1", "c1", "c3"}                      # c2 can't render on this dataset
    assert layout.visible_rows(saved, visible) == [["n1", "c1"], ["c3"]]
    edited = [["c3", "n1"], ["c1"]]
    assert layout.merge_hidden(edited, saved, visible) == [["c3", "n1"], ["c1"], ["c2"]]


def test_drag_and_drop_round_trip_with_duplicate_names():
    reports = [spec("a", name="Weekly"), spec("b", name="Weekly"), spec("c", "Number", name="Open")]
    labels = layout.unique_labels(reports, {"Column": "📊", "Number": "🔢"})
    assert labels == {"a": "📊 Weekly", "b": "📊 Weekly · 2", "c": "🔢 Open"}

    containers = layout.to_containers([["a", "b"], ["c"]], labels, "+ new row")
    assert containers[-1] == {"header": "+ new row", "items": []}
    # user drags "Open" into the new-row group and "Weekly · 2" to the front of row 1
    containers[0]["items"] = ["📊 Weekly · 2", "📊 Weekly"]
    containers[1]["items"] = []
    containers[2]["items"] = ["🔢 Open"]
    assert layout.from_containers(containers, labels) == [["b", "a"], ["c"]]
