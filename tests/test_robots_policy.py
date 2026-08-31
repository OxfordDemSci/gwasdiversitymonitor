import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy"))

from app import app, robots_noindex_enabled


class RobotsPolicyTests(unittest.TestCase):
    def setUp(self):
        self.previous_noindex = app.config.get("GWAS_NOINDEX", False)
        self.client = app.test_client()

    def tearDown(self):
        app.config["GWAS_NOINDEX"] = self.previous_noindex

    def test_indexing_remains_enabled_by_default(self):
        app.config["GWAS_NOINDEX"] = False

        response = self.client.get("/privacy-policy")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            '<meta name="robots" content="index, follow">', html
        )
        self.assertNotIn("X-Robots-Tag", response.headers)

    def test_noindex_sets_meta_directive_and_response_header(self):
        app.config["GWAS_NOINDEX"] = True

        response = self.client.get("/privacy-policy")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            '<meta name="robots" '
            'content="noindex, nofollow, noarchive">',
            html,
        )
        self.assertEqual(
            response.headers["X-Robots-Tag"],
            "noindex, nofollow, noarchive",
        )

    def test_noindex_header_covers_non_html_responses(self):
        app.config["GWAS_NOINDEX"] = True

        response = self.client.get("/api/traits")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers["X-Robots-Tag"],
            "noindex, nofollow, noarchive",
        )

    def test_dev_environment_can_enable_noindex(self):
        self.assertTrue(robots_noindex_enabled({"GWAS_NOINDEX": "1"}))

    def test_production_marker_always_keeps_indexing_enabled(self):
        self.assertFalse(robots_noindex_enabled({
            "GWAS_DEPLOYMENT_DOMAIN": "gwasdiversitymonitor.com",
            "GWAS_NOINDEX": "1",
        }))

    def test_noindex_is_disabled_without_an_explicit_dev_setting(self):
        self.assertFalse(robots_noindex_enabled({}))


if __name__ == "__main__":
    unittest.main()
