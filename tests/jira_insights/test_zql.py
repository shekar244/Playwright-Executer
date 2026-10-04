from jira_insights.zql import drilldown_zql, quote


def test_drilldown_becomes_one_zql_query():
    assert drilldown_zql("ABC", "Release 3.2", ["Sprint 41"]) == \
        'project = "ABC" AND fixVersion = "Release 3.2" AND cycleName = "Sprint 41"'
    assert drilldown_zql("ABC", "Release 3.2", ["Sprint 41", "Sprint 42", "Sprint 41"]) == \
        'project = "ABC" AND fixVersion = "Release 3.2" AND cycleName IN ("Sprint 41", "Sprint 42")'


def test_no_cycles_means_the_whole_version_and_no_version_the_whole_project():
    assert drilldown_zql("ABC", "Unscheduled") == 'project = "ABC" AND fixVersion = "Unscheduled"'
    assert drilldown_zql("ABC") == 'project = "ABC"'


def test_names_with_quotes_are_escaped():
    assert quote('Sprint "A"') == '"Sprint \\"A\\""'
    assert drilldown_zql("ABC", 'R "1"', []) == 'project = "ABC" AND fixVersion = "R \\"1\\""'
