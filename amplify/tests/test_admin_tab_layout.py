"""The Admin Portal displays each configuration area in a separate tab."""

import pytest


@pytest.fixture
def app(data_dir):
    from amplify.server import app as real_app
    return real_app


def test_admin_portal_renders_a_tab_for_every_section(admin):
    page = admin.get("/admin").get_data(as_text=True)

    for tab in ("overview", "users", "server", "grid", "tabs", "insights", "activity"):
        assert f'data-admin-tab="{tab}"' in page
        assert f'id="admin-pane-{tab}"' in page

    assert 'function switchAdminTab(tab)' in page
    assert "admin-tab-pane active" in page
