import contextlib
import os
import re
import unittest
from unittest import mock

from app import DataLoader, app


class InitialLoadPerformanceTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_homepage_only_reads_template_data(self):
        loader = mock.Mock()
        loader.getAncestriesList.return_value = {}
        loader.getTermsList.return_value = {}
        loader.getSummaryStatistics.return_value = {}

        with mock.patch(
            "app.routes.DataLoader.published_data_lock",
            return_value=contextlib.nullcontext("/data"),
        ), mock.patch(
            "app.routes.DataLoader.DataLoader", return_value=loader
        ):
            response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        loader.getAncestriesList.assert_called_once_with()
        loader.getTermsList.assert_called_once_with()
        loader.getSummaryStatistics.assert_called_once_with()
        loader.getBubbleGraph.assert_not_called()
        loader.getTSPlot.assert_not_called()
        loader.getChloroMap.assert_not_called()
        loader.getHeatMap.assert_not_called()
        loader.getDoughnutGraph.assert_not_called()

    def test_plot_json_is_conditional_and_browser_cacheable(self):
        unversioned = self.client.get("/json/summary.json")

        self.assertEqual(unversioned.status_code, 200)
        self.assertIn("max-age=0", unversioned.headers["Cache-Control"])
        self.assertIn("ETag", unversioned.headers)
        self.assertIn("Last-Modified", unversioned.headers)
        unversioned.close()

        with DataLoader.published_data_lock() as published_path:
            version = str(os.stat(os.path.join(
                published_path, "toplot", "summary.json"
            )).st_mtime_ns)
        response = self.client.get(f"/json/summary.json?v={version}")

        self.assertEqual(response.status_code, 200)
        self.assertIn("public", response.headers["Cache-Control"])
        self.assertIn("max-age=31536000", response.headers["Cache-Control"])
        self.assertIn("immutable", response.headers["Cache-Control"])
        self.assertIn("ETag", response.headers)
        self.assertIn("Last-Modified", response.headers)

        conditional = self.client.get(
            f"/json/summary.json?v={version}",
            headers={"If-None-Match": response.headers["ETag"]},
        )
        self.assertEqual(conditional.status_code, 304)
        self.assertEqual(conditional.data, b"")
        response.close()
        conditional.close()

    def test_plot_json_rejects_non_runtime_files(self):
        response = self.client.get("/json/not-a-dashboard-file.json")

        self.assertEqual(response.status_code, 404)

    def test_small_plots_start_early_without_duplicate_urls(self):
        response = self.client.get("/")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        for filename in (
            "tsPlot.json",
            "heatMap.json",
            "ancestriesOrdered.json",
            "chloroMap.json",
            "doughnutGraph.json",
            "summary.json",
        ):
            self.assertEqual(
                html.count(f"/json/{filename}?v="),
                1,
                f"{filename} should have one shared request URL",
            )

        self.assertIn("startPlotRequests", html)
        self.assertIn("window.fetch(", html)
        self.assertIn("loadPlotJson('tsPlot'", html)

        early_names = re.search(
            r"let earlyPlotNames = \[(.*?)\];", html, re.DOTALL
        )
        self.assertIsNotNone(early_names)
        self.assertIn("bubbleGraph", early_names.group(1))
        self.assertIn("format=compact-v1", html)

    def test_large_bubble_plot_is_scheduled_after_first_paint(self):
        response = self.client.get("/")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(html.count("/json/bubbleGraph.json?v="), 1)
        self.assertIn("scheduleAfterFirstPaint", html)
        self.assertIn("window.requestAnimationFrame", html)
        self.assertNotIn("window.requestIdleCallback", html)
        self.assertIn("loadBubbleGraphAfterFirstPaint();", html)
        self.assertNotIn("d3.json(\"/json/bubbleGraph.json", html)


if __name__ == "__main__":
    unittest.main()
