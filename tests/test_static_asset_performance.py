import re
import unittest
from pathlib import Path

from app import app, versioned_static


class StaticAssetPerformanceTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_html_revalidates_without_disabling_health_or_api_cache_policies(self):
        for path in ('/', '/additional-information', '/privacy-policy'):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.cache_control.no_cache)
                response.close()
        health = self.client.get('/health/live')
        self.assertEqual(health.headers['Cache-Control'], 'no-store')
        health.close()
        api = self.client.get('/api/traits?search=height')
        self.assertEqual(api.status_code, 200)
        self.assertFalse(api.cache_control.no_cache)
        api.close()

    def test_only_matching_fingerprinted_assets_are_immutable(self):
        with app.test_request_context():
            versioned_url = versioned_static("css/select2.min.css")

        response = self.client.get(versioned_url)
        cache_control = response.headers["Cache-Control"]
        self.assertEqual(response.status_code, 200)
        self.assertIn("public", cache_control)
        self.assertIn("max-age=31536000", cache_control)
        self.assertIn("immutable", cache_control)
        self.assertNotIn("no-cache", cache_control)
        response.close()

        unversioned = self.client.get("/static/css/select2.min.css")
        self.assertNotIn(
            "immutable", unversioned.headers.get("Cache-Control", "")
        )
        unversioned.close()

        wrong_version = self.client.get(
            "/static/css/select2.min.css?v=not-the-file-mtime"
        )
        self.assertNotIn(
            "immutable", wrong_version.headers.get("Cache-Control", "")
        )
        wrong_version.close()

        hashed_font = self.client.get(
            "/static/fonts/PTSansNarrow/"
            "PTSansNarrow-Regular.427aa961c68c.woff2"
        )
        self.assertIn("max-age=31536000", hashed_font.headers["Cache-Control"])
        self.assertIn("immutable", hashed_font.headers["Cache-Control"])
        hashed_font.close()

        missing = self.client.get("/static/not-present.js")
        self.assertEqual(missing.status_code, 404)
        self.assertNotIn(
            "immutable", missing.headers.get("Cache-Control", "")
        )
        missing.close()

    def test_initial_html_omits_unused_and_export_only_libraries(self):
        response = self.client.get("/")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("queue.v1.min.js", html)
        self.assertNotIn("topojson.v1.min.js", html)
        self.assertNotRegex(
            html,
            re.compile(
                r'<script[^>]+src="[^"]*d3plus-text\.full\.min\.js'
            ),
        )
        self.assertIn("d3plusText:", html)
        self.assertIn(
            '<script defer src="/static/js/imports/jquery-3.4.1.min.js',
            html,
        )
        self.assertIn(
            '<script defer src="/static/js/imports/d3.v4.min.js', html
        )
        response.close()

    def test_non_dashboard_pages_do_not_load_chart_libraries(self):
        response = self.client.get("/privacy-policy")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("d3.v4.min.js", html)
        self.assertNotIn("d3-tip.min.js", html)
        self.assertNotIn("select2.min.js", html)
        self.assertNotIn("select2.min.css", html)
        self.assertNotIn("d3plusText:", html)
        response.close()

    def test_standalone_report_does_not_load_site_javascript(self):
        with app.test_request_context():
            html = app.jinja_env.get_template(
                "pages/funder-report.html"
            ).render(
                report={},
                selection={},
                entity={},
                report_title="Report",
                report_note="",
                download_url="#",
                browser="",
            )

        self.assertNotIn("jquery-3.4.1.min.js", html)
        self.assertNotIn("js/script.js", html)

    def test_chart_export_library_is_loaded_on_demand(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "app"
            / "static"
            / "js"
            / "script.js"
        ).read_text(encoding="utf-8")

        self.assertIn("function loadD3plusText()", source)
        self.assertIn("document.head.appendChild(script);", source)
        self.assertIn("return loadD3plusText().then", source)

    def test_render_blocking_css_does_not_embed_the_font(self):
        with open("app/static/css/app.scss.css", encoding="utf-8") as css_file:
            source = css_file.read()

        self.assertNotIn("data:application/font-woff", source)
        self.assertGreaterEqual(source.count("font-display: swap"), 2)
        self.assertIn("PTSansNarrow-Regular.427aa961c68c.woff2", source)
        self.assertIn("PTSansNarrow-Bold.ee484e3d8bba.woff2", source)


if __name__ == "__main__":
    unittest.main()
