class ExamplePage:
    """Page object for the sample target site."""

    def __init__(self, page, base_url: str):
        self.page = page
        self.base_url = base_url

    def open(self) -> "ExamplePage":
        self.page.goto(self.base_url)
        return self

    def title(self) -> str:
        return self.page.title()

    def body_text(self) -> str:
        return self.page.locator("body").inner_text()

    def has_visible_link(self) -> bool:
        return self.page.locator("a").is_visible()
