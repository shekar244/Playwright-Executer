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


def test_gauge_sets_get_a_full_row_by_default():
    reports = [spec("g1", "Gauge"), spec("c1"), ReportSpec(id="g2", chart="Gauge", rows="Cycle"), spec("c2")]
    assert layout.default_layout(reports) == [["g1", "c1"], ["g2"], ["c2"]]



def test_drag_labels_carry_the_report_number():
    labels = layout.unique_labels([ReportSpec(id="a", name="Weekly", number=7)], {"Column": "📊"})
    assert labels == {"a": "📊 R-007 · Weekly"}


def test_clean_keeps_only_known_reports_without_appending_new_ones():
    assert layout.clean([["c3", "ghost"], ["n1", "n1"], []], REPORTS) == [["c3"], ["n1"]]


def test_with_members_keeps_positions_drops_unpicked_and_adds_new_rows():
    rows = [["n1", "n2"], ["c1", "c2"]]
    assert layout.with_members(rows, ["n2", "c2", "c3"], REPORTS) == [["n2"], ["c2"], ["c3"]]
    assert layout.with_members(rows, [], REPORTS) == []


def test_report_kind_from_columns_used():
    assert layout.report_kind(ReportSpec(chart="Gauge", rows="Cycle", gauge_where={"Result": ["Passed"]})) == "zephyr"
    assert layout.report_kind(ReportSpec(rows="Status", filters={"Issue Type": ["Story"]})) == "jira"
    assert layout.report_kind(ReportSpec(rows="Priority")) == "any"

