from streamlit.testing.v1 import AppTest


def _app():
    from unittest import mock

    from jira_insights.ui import sprint_view

    boards = [{"id": 11, "name": "PME Scrum", "type": "scrum"}, {"id": 12, "name": "PME Kanban", "type": "kanban"}]
    meta = {"sprint": {"_project": "PME", "_board_id": 12}}
    with mock.patch.object(sprint_view, "_project_boards", lambda p: boards if p == "PME" else []):
        sprint_view._fetch_velocity_from_board(None, None, meta)


def test_boards_come_from_the_project_and_the_saved_one_is_preselected():
    at = AppTest.from_function(_app).run()
    board = at.selectbox[0]
    assert board.label == "Board"
    assert board.options == ["PME Scrum (scrum)", "PME Kanban (kanban)"]
    assert board.value == 12
    assert not at.button[0].disabled


def test_no_project_means_no_board_choice():
    at = AppTest.from_function(_app).run()
    at.text_input[0].set_value("").run()
    assert at.selectbox[0].disabled and at.button[0].disabled
