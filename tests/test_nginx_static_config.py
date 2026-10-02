import unittest
from pathlib import Path


class NginxStaticConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (
            Path(__file__).resolve().parents[1] / "deploy" / "nginx.conf"
        ).read_text(encoding="utf-8")

    def test_static_assets_are_served_without_gunicorn(self):
        self.assertIn("location ^~ /static/", self.source)
        self.assertIn("root /var/www/app;", self.source)
        self.assertIn("try_files $uri =404;", self.source)

    def test_static_cache_policy_distinguishes_versioned_urls(self):
        self.assertIn("map \"$uri:$arg_v\" $static_cache_control", self.source)
        self.assertIn(
            '"public, max-age=31536000, immutable";', self.source
        )
        self.assertIn(
            '"public, max-age=0, must-revalidate";', self.source
        )
        self.assertIn(
            "add_header Cache-Control $static_cache_control;",
            self.source,
        )
        self.assertNotIn(
            "add_header Cache-Control $static_cache_control always;",
            self.source,
        )


if __name__ == "__main__":
    unittest.main()
