import pytest

from gridtest.pages.example_page import ExamplePage


@pytest.fixture
def example(page, base_url):
    return ExamplePage(page, base_url).open()


def test_example_title(example):
    assert "Example Domain" in example.title()


def test_example_body_text(example):
    # example.com no longer renders an <h1>, so assert on the body copy instead.
    assert "This domain is for use in documentation examples" in example.body_text()


def test_example_link(example):
    assert example.has_visible_link()
