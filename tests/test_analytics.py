import unittest

from app import app


class AnalyticsTemplateTests(unittest.TestCase):
    def setUp(self):
        self.previous_url = app.config.get("GOATCOUNTER_URL", "")
        self.client = app.test_client()

    def tearDown(self):
        app.config["GOATCOUNTER_URL"] = self.previous_url

    def test_goatcounter_is_rendered_when_configured(self):
        app.config["GOATCOUNTER_URL"] = "https://stats.example.test/"

        response = self.client.get("/privacy-policy")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            'data-goatcounter="https://stats.example.test/count"', html
        )
        self.assertIn('src="https://stats.example.test/count.js"', html)
        self.assertNotIn("googletagmanager", html)
        self.assertNotIn('id="cookie-consent"', html)
        self.assertNotIn('id="cookies-agree"', html)

    def test_analytics_is_disabled_without_a_url(self):
        app.config["GOATCOUNTER_URL"] = ""

        response = self.client.get("/privacy-policy")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("data-goatcounter", html)
        self.assertNotIn("count.js", html)


if __name__ == "__main__":
    unittest.main()
