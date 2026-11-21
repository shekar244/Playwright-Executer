"""Table reports: pick the columns, view the rows, download them as CSV and Excel.

The pure parts (model, filtering, layout, exports) are tested directly; the Builder and the Dashboard are
rendered through the real app with Streamlit's AppTest. UI tests are skipped without the Insights dependencies.
"""
import io
import json
from pathlib import Path

import openpyxl
import pandas as pd
import pytest

from amplify.jira_insights import layout
from amplify.jira_insights.pivot import (CHART_TYPES, TABLE, ReportSpec, build_table, clean_columns)
from amplify.jira_insights.store import Store
from amplify.jira_insights.ui.common import csv_safe, export_bytes

NOW = pd.Timestamp("2026-10-01")


@pytest.fixture
def df():
    return pd.DataFrame({
        "Key": ["QA-1", "QA-2", "QA-3", "QA-4"],
        "Summary": ["Login fails", "=HYPERLINK(\"http://evil\",\"x\")", "+1 cart", "Slow search"],
        "Issue Type": ["Bug", "Bug", "Story", "Bug"],
        "Status": ["Open", "Done", "Open", "Done"],
        "Labels": ["web", "web, api", None, "api"],
        "Story Points": [None, 2.0, 3.0, None],
        "Created": pd.to_datetime(["2026-09-01", "2026-09-10", "2026-09-20", "2026-09-28"]),
    })


# ── the model ─────────────────────────────────────────────────────────────────

def test_table_is_a_chart_type():
    assert TABLE == "Table" and TABLE in CHART_TYPES


def test_columns_keep_their_order_and_lose_blanks_and_repeats():
    assert clean_columns(["Status", "Key", "", "Status", "Summary"]) == ["Status", "Key", "Summary"]


@pytest.mark.parametrize("junk", [None, "Status", 5, {"a": 1}])
def test_columns_that_are_not_a_list_become_empty(junk):
    assert clean_columns(junk) == []


def test_a_table_spec_round_trips_through_json():
    spec = ReportSpec(name="Open bugs", chart=TABLE, columns=["Key", "Status"], filters={"Status": ["Open"]})
    back = ReportSpec.from_dict(json.loads(json.dumps(spec.to_dict())))
    assert back.chart == TABLE and back.columns == ["Key", "Status"] and back.filters == {"Status": ["Open"]}


def test_reports_saved_before_tables_existed_still_load():
    old = {"name": "Stories by status", "chart": "Column", "rows": "Status"}      # no "columns" key
    assert ReportSpec.from_dict(old).columns == []


def test_a_table_needs_exactly_its_columns_and_filters():
    spec = ReportSpec(chart=TABLE, columns=["Key", "Status"], filters={"Priority": ["High"]},
                      rows="Ignored", series="Ignored", value="Ignored")
    assert spec.required_columns() == {"Key", "Status", "Priority"}


# ── choosing the rows and columns ─────────────────────────────────────────────

def test_only_the_chosen_columns_come_back_in_the_chosen_order(df):
    out = build_table(df, ReportSpec(chart=TABLE, columns=["Status", "Key", "Created"]))
    assert list(out.columns) == ["Status", "Key", "Created"]
    assert len(out) == 4 and out["Key"].tolist() == ["QA-1", "QA-2", "QA-3", "QA-4"]


def test_filters_apply_and_the_index_restarts(df):
    out = build_table(df, ReportSpec(chart=TABLE, columns=["Key"], filters={"Status": ["Done"]}))
    assert out["Key"].tolist() == ["QA-2", "QA-4"] and out.index.tolist() == [0, 1]


def test_a_filter_column_need_not_be_shown(df):
    out = build_table(df, ReportSpec(chart=TABLE, columns=["Key"], filters={"Issue Type": ["Story"]}))
    assert list(out.columns) == ["Key"] and out["Key"].tolist() == ["QA-3"]


def test_multi_value_and_date_filters_work_like_other_reports(df):
    by_label = build_table(df, ReportSpec(chart=TABLE, columns=["Key"], filters={"Labels": ["api"]}), {"Labels"})
    assert by_label["Key"].tolist() == ["QA-2", "QA-4"]
    recent = build_table(df, ReportSpec(chart=TABLE, columns=["Key"], filters={"Created": {"last_days": 15}}),
                         now=NOW)
    assert recent["Key"].tolist() == ["QA-3", "QA-4"]


def test_columns_the_dataset_lacks_are_skipped_not_fatal(df):
    out = build_table(df, ReportSpec(chart=TABLE, columns=["Key", "Nope"]))
    assert list(out.columns) == ["Key"]


def test_the_dataset_itself_is_not_changed(df):
    before = df.copy()
    build_table(df, ReportSpec(chart=TABLE, columns=["Key", "Status"], filters={"Status": ["Done"]}))
    pd.testing.assert_frame_equal(df, before)


def test_filters_matching_nothing_give_an_empty_table(df):
    out = build_table(df, ReportSpec(chart=TABLE, columns=["Key"], filters={"Status": ["Nope"]}))
    assert out.empty and list(out.columns) == ["Key"]


# ── the dashboard layout ──────────────────────────────────────────────────────

def test_a_table_gets_a_full_width_row_of_its_own():
    reports = [ReportSpec(id="c1", chart="Column", rows="Status"), ReportSpec(id="t1", chart=TABLE, columns=["Key"]),
               ReportSpec(id="t2", chart=TABLE, columns=["Key"]), ReportSpec(id="c2", chart="Bar", rows="Status"),
               ReportSpec(id="c3", chart="Bar", rows="Status")]
    assert layout.default_layout(reports) == [["c1"], ["t1"], ["t2"], ["c2", "c3"]]


def test_tables_are_kept_per_the_columns_they_need():
    spec = ReportSpec(chart=TABLE, columns=["Key", "Result"])
    assert layout.report_kind(spec) == "zephyr"


# ── downloads ─────────────────────────────────────────────────────────────────

def sheet(xlsx: bytes):
    return openpyxl.load_workbook(io.BytesIO(xlsx)).active


def test_csv_and_excel_hold_exactly_the_table(df):
    table = build_table(df, ReportSpec(chart=TABLE, columns=["Key", "Status", "Story Points", "Created"]))
    csv, xlsx = export_bytes(table, False)
    back = pd.read_csv(io.BytesIO(csv), parse_dates=["Created"])
    assert list(back.columns) == ["Key", "Status", "Story Points", "Created"]
    assert back["Key"].tolist() == ["QA-1", "QA-2", "QA-3", "QA-4"] and back["Story Points"].tolist()[1:3] == [2.0, 3.0]
    rows = list(sheet(xlsx).iter_rows(values_only=True))
    assert rows[0] == ("Key", "Status", "Story Points", "Created")
    assert rows[2][:3] == ("QA-2", "Done", 2.0) and rows[2][3] == pd.Timestamp("2026-09-10")
    assert len(rows) == 1 + 4


def test_blank_cells_stay_blank(df):
    table = build_table(df, ReportSpec(chart=TABLE, columns=["Key", "Story Points"]))
    csv, xlsx = export_bytes(table, False)
    assert csv.decode().splitlines()[1] == "QA-1,"
    assert sheet(xlsx)["B2"].value is None


def test_text_that_looks_like_a_formula_is_never_run(df):
    table = build_table(df, ReportSpec(chart=TABLE, columns=["Key", "Summary"]))
    csv, xlsx = export_bytes(table, False)
    ws = sheet(xlsx)
    assert ws["B3"].value == '=HYPERLINK("http://evil","x")' and ws["B3"].data_type != "f"   # text, value intact
    lines = csv.decode().splitlines()
    assert "'=HYPERLINK" in lines[2] and "'+1 cart" in lines[3]
    assert "Login fails" in lines[1] and "'Login" not in lines[1]                          # ordinary text untouched


def test_csv_safe_leaves_numbers_dates_and_the_original_alone():
    frame = pd.DataFrame({"n": [-1.5, 2.0], "d": pd.to_datetime(["2026-01-01", "2026-01-02"]),
                          "t": ["-x", "ok"]})
    safe = csv_safe(frame)
    assert safe["n"].tolist() == [-1.5, 2.0] and safe["t"].tolist() == ["'-x", "ok"]
    assert frame["t"].tolist() == ["-x", "ok"]


def test_pivot_downloads_keep_working_with_the_index():
    frame = pd.DataFrame({"Issues": [3, 1]}, index=pd.Index(["Bug", "-none-"], name="Type"))
    csv, xlsx = export_bytes(frame, True)
    assert csv.decode().splitlines()[0] == "Type,Issues"
    assert sheet(xlsx)["A2"].value == "Bug"


# ── saving ────────────────────────────────────────────────────────────────────

def test_saved_tables_keep_their_columns_and_get_a_report_number(tmp_path):
    store = Store(tmp_path)
    saved = store.save_report(ReportSpec(name="Open bugs", chart=TABLE, columns=["Key", "Status"],
                                         filters={"Status": ["Open"]}))
    back = next(r for r in store.list_reports() if r.id == saved.id)
    assert back.chart == TABLE and back.columns == ["Key", "Status"] and back.code.startswith("R-")
    assert back.filters == {"Status": ["Open"]}


# ── the Builder and the Dashboard, rendered for real ──────────────────────────

APP = Path(__file__).resolve().parents[2] / "jira_insights" / "app.py"


@pytest.fixture
def workspace(tmp_path, monkeypatch, df):
    pytest.importorskip("plotly")
    pytest.importorskip("pygwalker")
    from amplify.config import settings
    monkeypatch.setattr(settings, "DATABASE_PATH", tmp_path / "amplify.db")
    monkeypatch.setenv("JIRA_INSIGHTS_HOME", str(tmp_path / "ws"))
    monkeypatch.delenv("QEA_API_URL", raising=False)
    store = Store(tmp_path / "ws")
    store.save_dataset("Demo", df, source="upload", multi_cols=["Labels"])
    return store


def open_view(view, *, state=None):
    from streamlit.testing.v1 import AppTest
    from amplify import sso
    from amplify.jira_insights import options as insights_options
    insights_options._cache.update(at=0.0, url=None, value=None)
    at = AppTest.from_file(str(APP), default_timeout=60)
    at.query_params["sso"] = sso.issue_insights_token(1, "tester")
    at.session_state["view_pending"] = view
    for key, value in (state or {}).items():
        at.session_state[key] = value
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def labels(at, kind):
    return [w.label for w in getattr(at, kind)]


def downloads(at):
    return [b.proto.label for b in at.get("download_button")]


def build_table_in_builder(workspace, columns, name="Bug list"):
    at = open_view("Report Builder")
    at.session_state["rb_chart"] = TABLE
    at.session_state["rb_columns"] = columns
    at.session_state["rb_name"] = name
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def test_the_builder_offers_table_and_swaps_the_pivot_controls_for_a_column_picker(workspace):
    at = build_table_in_builder(workspace, ["Key", "Summary", "Status"])
    assert "Columns" in labels(at, "multiselect")
    assert not any(s.label.startswith("Rows") for s in at.selectbox)            # no X-axis, series or measure
    assert "Aggregation" not in labels(at, "selectbox") and "Measure" not in labels(at, "selectbox")
    assert "🎨 Colours" not in labels(at, "expander")


def test_the_column_picker_offers_every_dataset_column_including_key_and_summary(workspace, df):
    at = build_table_in_builder(workspace, ["Key"])
    picker = next(m for m in at.multiselect if m.label == "Columns")
    assert list(picker.options) == list(df.columns)


def test_the_preview_shows_the_chosen_columns_and_offers_csv_and_excel(workspace):
    at = build_table_in_builder(workspace, ["Status", "Key"])
    shown = at.dataframe[0].value
    assert list(shown.columns) == ["Status", "Key"] and len(shown) == 4
    assert downloads(at) == ["⬇ CSV", "⬇ Excel"]
    assert any("4 rows · 2 columns" in c.value for c in at.caption)


def test_saving_creates_a_table_report_with_those_columns_in_that_order(workspace):
    at = build_table_in_builder(workspace, ["Status", "Key", "Created"], name="Bug list")
    next(b for b in at.button if b.label == "Save").click().run()
    assert not at.exception, [e.value for e in at.exception]
    saved = next(r for r in workspace.list_reports() if r.name == "Bug list")
    assert saved.chart == TABLE and saved.columns == ["Status", "Key", "Created"] and saved.rows == ""


def test_a_new_table_starts_with_the_usual_issue_columns(workspace):
    at = open_view("Report Builder")
    at.session_state["rb_chart"] = TABLE
    at.run()
    picker = next(m for m in at.multiselect if m.label == "Columns")
    assert picker.value == ["Key", "Summary", "Issue Type", "Status"]


def test_filters_in_the_builder_narrow_the_table(workspace):
    at = build_table_in_builder(workspace, ["Key", "Status"])
    at.session_state["rb_filter_cols"] = ["Status"]
    at.session_state["rb_f_Status"] = ["Done"]
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.dataframe[0].value["Key"].tolist() == ["QA-2", "QA-4"]


def test_a_saved_table_reopens_with_its_columns(workspace):
    saved = workspace.save_report(ReportSpec(name="Mine", chart=TABLE, columns=["Status", "Key"]))
    at = open_view("Report Builder", state={"rb_pending_pick": saved.id})
    assert next(m for m in at.multiselect if m.label == "Columns").value == ["Status", "Key"]


def test_the_dashboard_shows_a_table_report_with_downloads(workspace):
    workspace.save_report(ReportSpec(name="Bug list", chart=TABLE, columns=["Key", "Status"],
                                     filters={"Issue Type": ["Bug"]}))
    at = open_view("Dashboard")
    frames = [d.value for d in at.dataframe if list(d.value.columns) == ["Key", "Status"]]
    assert frames and frames[0]["Key"].tolist() == ["QA-1", "QA-2", "QA-4"]
    assert downloads(at).count("⬇ CSV") >= 1 and downloads(at).count("⬇ Excel") >= 1


def test_a_table_whose_columns_are_missing_is_left_off_a_dataset_that_lacks_them(workspace):
    workspace.save_report(ReportSpec(name="Runs", chart=TABLE, columns=["Key", "Result"]))
    at = open_view("Dashboard")
    assert not any("Result" in d.value.columns for d in at.dataframe)
