import unittest
from html.parser import HTMLParser
from pathlib import Path

from app import app


class HeaderCitationParser(HTMLParser):
    """Collect citation actions that are direct descendants of the header UI."""

    def __init__(self):
        super().__init__()
        self.header_div_depth = 0
        self.actions = []
        self._current_action = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        classes = attributes.get("class", "").split()

        if tag == "div":
            if attributes.get("id") == "header":
                self.header_div_depth = 1
            elif self.header_div_depth:
                self.header_div_depth += 1

        if self.header_div_depth and "header-cite-action" in classes:
            self._current_action = {
                "tag": tag,
                "attributes": attributes,
                "text": [],
            }
            self.actions.append(self._current_action)

    def handle_endtag(self, tag):
        if self._current_action and tag == self._current_action["tag"]:
            self._current_action = None

        if tag == "div" and self.header_div_depth:
            self.header_div_depth -= 1

    def handle_data(self, data):
        if self._current_action is not None:
            self._current_action["text"].append(data)


class HeaderCitationTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_citation_action_is_prominent_and_unique_in_header(self):
        response = self.client.get("/privacy-policy")
        html = response.get_data(as_text=True)
        parser = HeaderCitationParser()
        parser.feed(html)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(parser.actions), 1)

        action = parser.actions[0]
        visible_text = " ".join("".join(action["text"]).split())
        self.assertEqual(action["tag"], "button")
        self.assertEqual(visible_text, "How to cite")
        self.assertEqual(
            action["attributes"].get("onclick"),
            "launchPopup('popup-footer');",
        )
        self.assertEqual(
            action["attributes"].get("aria-haspopup"), "dialog"
        )
        self.assertEqual(
            action["attributes"].get("aria-controls"), "popup-footer"
        )

        self.assertEqual(html.count('class="header-cite-action"'), 1)
        self.assertEqual(html.count("How to cite"), 1)

        stylesheet = (
            Path(__file__).resolve().parents[1]
            / "app"
            / "static"
            / "sass"
            / "partials"
            / "_header.scss"
        ).read_text()
        self.assertIn("content: '\\201C';", stylesheet)
        self.assertIn("content: '\\201D';", stylesheet)

    def test_shared_menu_does_not_duplicate_citation_action(self):
        repository_root = Path(__file__).resolve().parents[1]
        menu = (
            repository_root / "app" / "templates" / "partials" / "menu.html"
        ).read_text()

        self.assertNotIn("header-cite-action", menu)
        self.assertNotIn("How to cite", menu)
        self.assertNotIn("launchPopup('popup-footer')", menu)


if __name__ == "__main__":
    unittest.main()
