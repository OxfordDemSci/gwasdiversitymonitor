from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DashboardLoadingTests(unittest.TestCase):
    def test_bootstrap_and_retry_share_the_verified_loader(self):
        source = (ROOT / 'app/templates/index.html').read_text()
        self.assertIn('GwasDashboardLoading.create(window, window.__gwasPlotBootstrap)', source)
        self.assertIn('plotLoader.load(name, retry)', source)
        self.assertNotIn('d3.json(bootstrap.urls', source)
        self.assertIn("loadDashboardPanel('heatMap', ['heatMap', 'ancestriesOrdered']", source)
        self.assertIn('readyPanels.size === 6', source)
        self.assertIn("$('#cb1, #cb2').prop('disabled', true)", source)

    def test_failure_recovery_keeps_copy_bound_to_successfully_loaded_selection(self):
        source = (ROOT / 'app/templates/index.html').read_text()
        self.assertIn('canShare: function() { return window.gwasChartData.canExport(); }', source)
        self.assertIn('function showFilterFailure(', source)
        self.assertIn('Previous charts are still shown.', source)
        self.assertIn('applyDashboardFilters(captureDashboardState()).then', source)
        self.assertIn('window.gwasChartData.setSelectionReady(true, false)', source)

    def test_help_is_collapsed_and_fetches_bounded_options_only_on_open(self):
        markup = (ROOT / 'app/templates/components/dashboard-help.html').read_text()
        source = (ROOT / 'app/static/js/dashboard-help.js').read_text()
        self.assertIn('<details id="dashboard-examples"', markup)
        self.assertNotIn('<details open', markup)
        self.assertIn("if (details.open) loadExamples()", source)
        self.assertIn("s?page=1", source)
        self.assertNotIn('stage=initial', source)

    def test_help_css_has_matching_build_source(self):
        self.assertEqual(
            (ROOT / 'app/static/css/dashboard-help.css').read_text(),
            (ROOT / 'app/static/sass/dashboard-help.scss').read_text(),
        )


if __name__ == '__main__':
    unittest.main()
